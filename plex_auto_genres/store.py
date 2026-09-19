"""Persistent state, backed by SQLite.

Replaces v1's five JSON files under ``logs/``. Those were keyed on media *type*
only, held no timestamps, treated any failure as permanent, and could not
record what a run had overwritten. This store fixes all four, and doubles as
the data layer a web UI will need:

``media_state``  per-library processing cache, invalidated by settings changes
``bindings``     manual "this Plex item IS that provider id" overrides
``snapshots``    previous tag values, so a run can be undone
``runs``         run history with counters
``kv``           small cached blobs (the AniDB mapping table, taxonomy, ...)
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .models import ExternalId, RunReport

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media_state (
    library     TEXT    NOT NULL,
    media_key   TEXT    NOT NULL,   -- GUID when available, else 'title (year)'
    rating_key  INTEGER,
    title       TEXT    NOT NULL,
    year        INTEGER,
    fingerprint TEXT    NOT NULL,   -- settings hash that produced this result
    status      TEXT    NOT NULL,   -- 'ok' | 'failed'
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    genres      TEXT,               -- JSON array actually written
    provider    TEXT,
    provider_id TEXT,
    updated_at  REAL    NOT NULL,
    PRIMARY KEY (library, media_key)
);
CREATE INDEX IF NOT EXISTS idx_media_state_status ON media_state(library, status);

CREATE TABLE IF NOT EXISTS bindings (
    library     TEXT NOT NULL,
    media_key   TEXT NOT NULL,
    provider    TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    note        TEXT,
    created_at  REAL NOT NULL,
    PRIMARY KEY (library, media_key)
);

CREATE TABLE IF NOT EXISTS snapshots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT    NOT NULL,
    library    TEXT    NOT NULL,
    rating_key INTEGER NOT NULL,
    title      TEXT    NOT NULL,
    field      TEXT    NOT NULL,   -- 'genre' | 'collection'
    before     TEXT    NOT NULL,   -- JSON array
    after      TEXT    NOT NULL,   -- JSON array
    created_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_run ON snapshots(run_id);

CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    library     TEXT NOT NULL,
    action      TEXT NOT NULL,
    dry_run     INTEGER NOT NULL DEFAULT 0,
    started_at  REAL NOT NULL,
    finished_at REAL,
    report      TEXT,
    undone_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC);

CREATE TABLE IF NOT EXISTS kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    expires_at REAL
);
"""


@dataclass(frozen=True, slots=True)
class CachedState:
    """A previously recorded processing result for one media item."""
    status: str
    fingerprint: str
    attempts: int
    updated_at: float
    genres: list[str]
    last_error: str | None


def _retry_after(attempts: int) -> float:
    """Exponential backoff before a failed item is retried: 1h, 4h, 16h, capped.

    v1 never retried: one transient TMDB hiccup blacklisted a title forever
    unless the user ran --force, which wiped the whole cache.
    """
    return min(3600.0 * (4 ** max(attempts - 1, 0)), 7 * 86400.0)


class Store:
    """Thin, synchronous SQLite wrapper. Safe to share across threads.

    One connection, guarded by a re-entrant lock. ``check_same_thread=False``
    only tells the sqlite3 module to *allow* other threads in; it does not
    make a connection safe for two threads to use at once -- the statement
    cache and transaction state are unprotected, and the pipeline (writes in
    worker threads, bookkeeping on the event loop) crashed the interpreter
    the first time two of them overlapped. Every method takes the lock.
    Contention is irrelevant at this scale; the writes are sub-millisecond.
    """

    def __init__(self, path: str | Path = "logs/plex-auto-genres.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self._conn

    # -- media state ------------------------------------------------------

    def get_state(self, library: str, media_key: str) -> CachedState | None:
        """The cached result for one item, or ``None`` if it was never processed."""
        with self._read() as conn:
            row = conn.execute(
                "SELECT status, fingerprint, attempts, updated_at, genres, last_error "
                "FROM media_state WHERE library = ? AND media_key = ?",
                (library, media_key),
            ).fetchone()
        if row is None:
            return None
        return CachedState(
            status=row["status"],
            fingerprint=row["fingerprint"],
            attempts=row["attempts"],
            updated_at=row["updated_at"],
            genres=json.loads(row["genres"]) if row["genres"] else [],
            last_error=row["last_error"],
        )

    def should_process(
        self,
        library: str,
        media_key: str,
        fingerprint: str,
        *,
        force: bool = False,
        now: float | None = None,
    ) -> bool:
        """Decide whether an item needs work.

        Reprocess when: forced, never seen, the settings fingerprint changed,
        or it failed and its backoff window has elapsed.
        """
        if force:
            return True
        state = self.get_state(library, media_key)
        if state is None:
            return True
        if state.fingerprint != fingerprint:
            return True
        if state.status == "ok":
            return False
        now = time.time() if now is None else now
        return now - state.updated_at >= _retry_after(state.attempts)

    def record_success(
        self,
        library: str,
        media_key: str,
        *,
        fingerprint: str,
        title: str,
        year: int | None,
        rating_key: int | None,
        genres: list[str],
        provider: str | None,
        provider_id: str | None,
    ) -> None:
        """Record that an item was processed and tagged successfully."""
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO media_state (library, media_key, rating_key, title, year, "
                "fingerprint, status, attempts, last_error, genres, provider, "
                "provider_id, updated_at) "
                "VALUES (?,?,?,?,?,?,'ok',0,NULL,?,?,?,?) "
                "ON CONFLICT(library, media_key) DO UPDATE SET "
                "rating_key=excluded.rating_key, title=excluded.title, "
                "year=excluded.year, fingerprint=excluded.fingerprint, "
                "status='ok', attempts=0, last_error=NULL, genres=excluded.genres, "
                "provider=excluded.provider, provider_id=excluded.provider_id, "
                "updated_at=excluded.updated_at",
                (library, media_key, rating_key, title, year, fingerprint,
                 json.dumps(genres), provider, provider_id, time.time()),
            )

    def record_failure(
        self,
        library: str,
        media_key: str,
        *,
        fingerprint: str,
        title: str,
        year: int | None,
        rating_key: int | None,
        error: str,
    ) -> None:
        """Record a failure, incrementing the attempt counter used for backoff."""
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO media_state (library, media_key, rating_key, title, year, "
                "fingerprint, status, attempts, last_error, updated_at) "
                "VALUES (?,?,?,?,?,?,'failed',1,?,?) "
                "ON CONFLICT(library, media_key) DO UPDATE SET "
                "rating_key=excluded.rating_key, fingerprint=excluded.fingerprint, "
                "status='failed', attempts=media_state.attempts + 1, "
                "last_error=excluded.last_error, updated_at=excluded.updated_at",
                (library, media_key, rating_key, title, year, fingerprint,
                 error[:500], time.time()),
            )

    def clear_library(self, library: str) -> int:
        with self._tx() as conn:
            cur = conn.execute("DELETE FROM media_state WHERE library = ?", (library,))
        return cur.rowcount

    def failures(self, library: str, limit: int = 100) -> list[sqlite3.Row]:
        with self._read() as conn:
            return conn.execute(
                "SELECT title, year, attempts, last_error, updated_at FROM media_state "
                "WHERE library = ? AND status = 'failed' ORDER BY updated_at DESC LIMIT ?",
                (library, limit),
            ).fetchall()

    def stats(self, library: str) -> dict[str, int]:
        """Count of items per status for one library."""
        with self._read() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM media_state WHERE library = ? GROUP BY status",
                (library,),
            ).fetchall()
        return {row["status"]: row["n"] for row in rows}

    # -- manual bindings --------------------------------------------------

    def set_binding(
        self,
        library: str,
        media_key: str,
        provider: str,
        provider_id: str,
        note: str | None = None,
    ) -> None:
        """Pin an item to a provider id, overriding automatic matching."""
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO bindings "
                "(library, media_key, provider, provider_id, note, created_at) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(library, media_key) DO UPDATE SET "
                "provider=excluded.provider, provider_id=excluded.provider_id, "
                "note=excluded.note, created_at=excluded.created_at",
                (library, media_key, provider, provider_id, note, time.time()),
            )
            # A new binding invalidates whatever the automatic match produced.
            conn.execute(
                "DELETE FROM media_state WHERE library = ? AND media_key = ?", (library, media_key)
            )

    def get_binding(self, library: str, media_key: str) -> tuple[str, ExternalId] | None:
        """The manual binding for an item, as ``(provider, id)``."""
        with self._read() as conn:
            row = conn.execute(
                "SELECT provider, provider_id FROM bindings WHERE library = ? AND media_key = ?",
                (library, media_key),
            ).fetchone()
        if row is None:
            return None
        return row["provider"], ExternalId(row["provider"], row["provider_id"])

    def delete_binding(self, library: str, media_key: str) -> bool:
        """Remove a binding. Returns whether one existed."""
        with self._tx() as conn:
            cur = conn.execute(
                "DELETE FROM bindings WHERE library = ? AND media_key = ?", (library, media_key)
            )
        return cur.rowcount > 0

    def list_bindings(self, library: str | None = None) -> list[sqlite3.Row]:
        """Every binding, optionally narrowed to one library."""
        with self._read() as conn:
            if library:
                return conn.execute(
                    "SELECT * FROM bindings WHERE library = ? ORDER BY media_key", (library,)
                ).fetchall()
            return conn.execute("SELECT * FROM bindings ORDER BY library, media_key").fetchall()

    # -- runs and undo ----------------------------------------------------

    def start_run(self, library: str, action: str, *, dry_run: bool) -> str:
        """Open a run and return its id."""
        run_id = uuid.uuid4().hex[:12]
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO runs (run_id, library, action, dry_run, started_at) "
                "VALUES (?,?,?,?,?)",
                (run_id, library, action, int(dry_run), time.time()),
            )
        return run_id

    def finish_run(self, report: RunReport) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE runs SET finished_at = ?, report = ? WHERE run_id = ?",
                (time.time(), json.dumps(report.as_dict()), report.run_id),
            )

    def add_snapshot(
        self,
        run_id: str,
        library: str,
        rating_key: int,
        title: str,
        field: str,
        before: Iterable[str],
        after: Iterable[str],
    ) -> None:
        """Record an item's tag values before and after a write, for undo."""
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO snapshots (run_id, library, rating_key, title, "
                "field, before, after, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (run_id, library, rating_key, title, field,
                 json.dumps(list(before)), json.dumps(list(after)), time.time()),
            )

    def snapshots_for(self, run_id: str) -> list[sqlite3.Row]:
        with self._read() as conn:
            return conn.execute(
                "SELECT * FROM snapshots WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()

    def mark_undone(self, run_id: str) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE runs SET undone_at = ? WHERE run_id = ?", (time.time(), run_id))

    def recent_runs(self, limit: int = 20, library: str | None = None) -> list[sqlite3.Row]:
        """Most recent runs, newest first."""
        with self._read() as conn:
            if library:
                return conn.execute(
                    "SELECT * FROM runs WHERE library = ? ORDER BY started_at DESC LIMIT ?",
                    (library, limit),
                ).fetchall()
            return conn.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()

    def get_run(self, run_id: str) -> sqlite3.Row | None:
        with self._read() as conn:
            return conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()

    # -- generic cache ----------------------------------------------------

    def kv_get(self, key: str) -> str | None:
        """A cached value, or ``None`` when absent or expired."""
        with self._read() as conn:
            row = conn.execute(
                "SELECT value, expires_at FROM kv WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        if row["expires_at"] is not None and row["expires_at"] < time.time():
            with self._tx() as conn:
                conn.execute("DELETE FROM kv WHERE key = ?", (key,))
            return None
        return row["value"]

    def kv_set(self, key: str, value: str, ttl_s: float | None = None) -> None:
        expires = time.time() + ttl_s if ttl_s else None
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO kv (key, value, expires_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "expires_at=excluded.expires_at",
                (key, value, expires),
            )

    # -- migration from the v1 text files ---------------------------------

    def import_legacy_logs(
        self, logs_dir: str | Path, library: str, media_type: str, fingerprint: str
    ) -> int:
        """Seed the cache from v1's ``logs/plex-<type>-*.txt`` progress files.

        Best-effort: those files were keyed by type, not library, so the same
        entries are imported for every library of that type. Anything that no
        longer exists in the library is simply never looked up again.
        """
        logs_dir = Path(logs_dir)
        imported = 0
        sources = {
            "ok": logs_dir / f"plex-{media_type}-successful.txt",
            "failed": logs_dir / f"plex-{media_type}-failures.txt",
        }
        for status, path in sources.items():
            if not path.is_file():
                continue
            try:
                entries = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(entries, list):
                continue
            with self._tx() as conn:
                for identifier in entries:
                    if not isinstance(identifier, str):
                        continue
                    title, year = _split_identifier(identifier)
                    conn.execute(
                        "INSERT OR IGNORE INTO media_state (library, media_key, title, year, "
                        "fingerprint, status, attempts, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                        (library, identifier, title, year, fingerprint, status,
                         1 if status == "failed" else 0, time.time()),
                    )
                    imported += 1
        return imported


def _split_identifier(identifier: str) -> tuple[str, int | None]:
    """Split ``"Some Show (2019)"`` back into title and year."""
    if identifier.endswith(")") and "(" in identifier:
        head, _, tail = identifier.rpartition(" (")
        candidate = tail[:-1]
        if candidate.isdigit():
            return head, int(candidate)
    return identifier, None
