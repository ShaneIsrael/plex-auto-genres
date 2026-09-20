"""One-time upgrade steps, run at startup and narrated in the log.

A v1 install is recognised by its files, not by a version flag:

* ``config.json`` in the v1 layout is converted on disk to the v2 layout; the
  original is kept next to it as ``config.json.v1`` (never overwritten).
* The v1 progress files ``logs/plex-<type>-successful.txt`` and
  ``-failures.txt`` are imported into the database for every configured
  library of that type, then renamed ``*.imported`` so they are neither read
  twice nor lost.
* The other v1 files (rating and rating-collection progress, the automate
  log) are not needed by v2 and are left exactly where they are.

Everything here is idempotent: a v2 install with nothing left to migrate
does nothing and says nothing.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from .config import CONFIG_VERSION, AppConfig, load_config, migrate_v1, write_config
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

    @property
    def did_anything(self) -> bool:
        return self.config_converted or bool(self.imported) or bool(self.renamed)


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
    if not isinstance(raw, dict) or int(raw.get("version", 1)) >= CONFIG_VERSION:
        return

    migrated = migrate_v1(raw)
    try:
        AppConfig.model_validate({**migrated, "plex": {}, "providers": {}})
    except ValidationError as exc:
        log.error("Config %s is in the v1 layout but cannot be converted; left as is:\n%s",
                  path, exc)
        return

    backup = _fresh_name(path.with_name(path.name + ".v1"))
    shutil.copy2(path, backup)
    write_config(path, migrated)
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
    """``path`` itself, or a timestamped sibling if it already exists."""
    if not path.exists():
        return path
    return path.with_name(f"{path.name}.{time.strftime('%Y%m%d-%H%M%S')}")


# -- logs/plex-<type>-*.txt ----------------------------------------------------


def _import_progress_files(logs_dir: Path, config: AppConfig, store: Store,
                           report: MigrationReport) -> None:
    for media_type in MediaType:
        files = [
            logs_dir / f"plex-{media_type.value}-{kind}.txt" for kind in ("successful", "failures")
        ]
        present = [f for f in files if f.is_file()]
        if not present:
            continue
        libraries = [run for run in config.libraries if run.type is media_type]
        if not libraries:
            note = (f"{', '.join(f.name for f in present)}: v1 progress for {media_type.value} "
                    "libraries, but none is configured; left in place")
            report.notes.append(note)
            log.info("%s", note)
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
            target = file.with_name(file.name + ".imported")
            file.replace(target)
            report.renamed.append(target)
            log.info("Renamed %s to %s (kept as a backup; v2 keeps its state in the database)",
                     file.name, target.name)


def _note_unused_files(logs_dir: Path, report: MigrationReport) -> None:
    found = [name for name in _UNUSED_V1_FILES if (logs_dir / name).is_file()]
    if not found:
        return
    note = (f"v1 files not needed by v2, left untouched: {', '.join(found)} "
            "(ratings and rating collections are derived from the cache now)")
    report.notes.append(note)
    log.info("%s", note)
