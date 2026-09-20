"""One-time upgrade steps, run at startup and narrated in the log.

A v1 install is recognised by its *shape*, not by a version number:

* ``config.json`` carrying v1's ``general_settings`` / ``automation_settings``
  blocks is converted on disk to the v2 layout; the original is kept next to
  it as ``config.json.v1`` (never overwritten).
* The v1 progress files ``logs/plex-<type>-successful.txt`` and
  ``-failures.txt`` are imported into the database for every configured
  library of that type, then renamed ``*.imported`` so they are neither read
  twice nor lost.
* The other v1 files (rating and rating-collection progress, the automate
  log) are not needed by v2 and are left exactly where they are.

Everything here is idempotent and best-effort: a v2 install with nothing to
migrate does nothing and says nothing, and a step that cannot write (a
read-only mount, a volume owned by someone else) is reported and skipped --
the config is still migrated in memory on load, exactly as before, so a
failed upgrade degrades to the old behaviour instead of killing the process.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from .config import AppConfig, is_v1_layout, load_config, migrate_v1, write_config
from .models import MediaType
from .store import Store

log = logging.getLogger(__name__)

#: v1 wrote these too; v2 derives what they held from its own cache.
_UNUSED_V1_FILES = (
    "plex-anime-ratings-progress.txt",
    "plex-anime-rc-successful.txt", "plex-anime-rc-failures.txt",
    "plex-standard-tv-rc-successful.txt", "plex-standard-tv-rc-failures.txt",
    "plex-standard-movie-rc-successful.txt", "plex-standard-movie-rc-failures.txt",
    "plex-auto-genres-automate.log",
)


@dataclass
class MigrationReport:
    """What a startup migration did, for the log and for tests."""

    config_converted: bool = False
    config_backup: Path | None = None
    imported: dict[str, int] = field(default_factory=dict)
    renamed: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: Steps that could not be carried out (permissions, a vanished file).
    failures: list[str] = field(default_factory=list)

    @property
    def did_anything(self) -> bool:
        return self.config_converted or bool(self.imported) or bool(self.renamed)


def legacy_logs_dir(db_path: str | Path) -> Path:
    """Where v1 left its progress files: next to the database, else ``logs/``.

    v1 always wrote them to ``logs/`` relative to its working directory, and
    that is also where v2 puts its database by default -- but a ``--db``
    elsewhere must not make the files invisible.
    """
    beside_db = Path(db_path).parent
    if any(beside_db.glob("plex-*-successful.txt")) or any(beside_db.glob("plex-*-failures.txt")):
        return beside_db
    fallback = Path("logs")
    if fallback.is_dir() and any(fallback.glob("plex-*.txt")):
        return fallback
    return beside_db


def migrate_install(config_path: str | Path, logs_dir: str | Path, store: Store) -> MigrationReport:
    """Bring a v1 install up to date. Safe to call on every start."""
    report = MigrationReport()
    config_path = Path(config_path)
    logs_dir = Path(logs_dir)

    _convert_config_file(config_path, report)
    try:
        config = load_config(config_path, missing_ok=True)
    except Exception as exc:  # the command that follows will report it properly
        log.warning("Skipping the progress-file import: the config does not load (%s)", exc)
        return report
    _import_progress_files(logs_dir, config, store, report)
    _note_unused_files(logs_dir, report)
    return report


# -- config.json --------------------------------------------------------------


def _convert_config_file(path: Path, report: MigrationReport) -> None:
    if not path.is_file():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return  # load_config will explain what is wrong with it
    # Shape, not version: a v2 file whose "version" key was dropped by hand is
    # still a v2 file, and running it through migrate_v1 would empty it.
    if not is_v1_layout(raw):
        return

    migrated = migrate_v1(raw)
    try:
        AppConfig.model_validate({**migrated, "plex": {}, "providers": {}})
    except ValidationError as exc:
        log.error("Config %s is in the v1 layout but cannot be converted; left as is:\n%s",
                  path, exc)
        return

    backup = _fresh_name(path.with_name(path.name + ".v1"))
    try:
        shutil.copy2(path, backup)
        # The .v1 copy is this upgrade's backup; write_config's own .bak would
        # otherwise replace whatever the last edit left there.
        write_config(path, migrated, backup=False)
    except OSError as exc:
        report.failures.append(f"could not convert {path}: {exc}")
        log.error("Config %s is in the v1 layout but could not be rewritten (%s). "
                  "It is still migrated in memory on every load; fix the permissions "
                  "to convert it on disk.", path, exc)
        return
    report.config_converted = True
    report.config_backup = backup

    log.info("Config %s was in the v1 layout: converted to v2. The original is kept as %s.",
             path, backup)
    for entry in migrated.get("libraries", []):
        actions = [name for name, key in (
            ("genre field" if entry.get("useGenres") else "collections", "library"),
            ("replace existing genres", "clearGenres"),
            ("TMDB keywords", "useKeywords"),
            ("posters", "setPosters"), ("sort", "sortCollections"),
            ("ratings", "rateAnime"), ("rating collections", "createRatingCollections"),
        ) if key == "library" or entry.get(key)]
        log.info("  library %r (%s): %s", entry["library"], entry["type"], ", ".join(actions))
    for type_name, rules in migrated.get("defaults", {}).items():
        log.info("  defaults for %s: %d ignored, %d replaced, sort prefix %r, "
                 "%d sorted collections",
                 type_name, len(rules.get("ignore", [])), len(rules.get("replace", {})),
                 rules.get("sortedPrefix", ""), len(rules.get("sortedCollections", [])))


def _fresh_name(path: Path) -> Path:
    """``path`` itself, or the first free ``<name>.<stamp>`` beside it."""
    if not path.exists():
        return path
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for suffix in ("", *(f"-{n}" for n in range(2, 100))):
        candidate = path.with_name(f"{path.name}.{stamp}{suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{path.name}.{time.time_ns()}")


# -- logs/plex-<type>-*.txt ----------------------------------------------------


def _import_progress_files(logs_dir: Path, config: AppConfig, store: Store,
                           report: MigrationReport) -> None:
    for media_type in MediaType:
        present = [
            path for kind in ("successful", "failures")
            if (path := logs_dir / f"plex-{media_type.value}-{kind}.txt").is_file()
        ]
        if not present:
            continue
        libraries = [run for run in config.libraries if run.type is media_type]
        if not libraries:
            _note(report, f"{', '.join(f.name for f in present)}: v1 progress for "
                          f"{media_type.value} libraries, but none is configured; left in place")
            continue
        unreadable = [f.name for f in present if not _is_readable_json_list(f)]
        if unreadable:
            # Renaming a file we could not read would lose it for good.
            _note(report, f"{', '.join(unreadable)}: not readable as v1 progress; left in place")
            continue

        for run in libraries:
            flag = f"legacy_imported::{run.library}"
            if store.kv_get(flag):
                continue
            count = store.import_legacy_logs(logs_dir, run.library, media_type.value,
                                             config.fingerprint(run))
            store.kv_set(flag, "1")
            report.imported[run.library] = count
            log.info("Imported %d v1 progress entries for %r from %s",
                     count, run.library, " and ".join(f.name for f in present))

        for file in present:
            target = _fresh_name(file.with_name(file.name + ".imported"))
            try:
                file.replace(target)
            except OSError as exc:
                report.failures.append(f"could not rename {file.name}: {exc}")
                log.warning("Imported %s but could not rename it (%s); it will be skipped "
                            "on the next start anyway.", file.name, exc)
                continue
            report.renamed.append(target)
            log.info("Renamed %s to %s (kept as a backup; v2 keeps its state in the database)",
                     file.name, target.name)


def _is_readable_json_list(path: Path) -> bool:
    try:
        return isinstance(json.loads(path.read_text(encoding="utf-8")), list)
    except (OSError, json.JSONDecodeError):
        return False


def _note(report: MigrationReport, message: str) -> None:
    report.notes.append(message)
    log.info("%s", message)


def _note_unused_files(logs_dir: Path, report: MigrationReport) -> None:
    found = [name for name in _UNUSED_V1_FILES if (logs_dir / name).is_file()]
    if found:
        _note(report, f"v1 files not needed by v2, left untouched: {', '.join(found)} "
                      "(ratings and rating collections are derived from the cache now)")
