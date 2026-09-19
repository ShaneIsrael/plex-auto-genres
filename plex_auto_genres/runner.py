"""Run one or more configured libraries end to end.

This is the piece both front ends share: the CLI drives it with a progress
bar and a confirmation prompt, the server drives it from the scheduler and,
later, from a job manager. It knows nothing about argparse or HTTP.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from .config import AppConfig, LibraryRun
from .models import RunReport
from .pipeline import Pipeline, ProgressFn
from .plexsvc import client as plex_client
from .store import Store

log = logging.getLogger(__name__)

#: Actions that ``--only`` can select.
ACTIONS = ("tags", "posters", "sort", "ratings", "rating-collections")

#: Called when a library starts; may return a per-item progress callback.
OnLibraryStart = Callable[[LibraryRun, int], ProgressFn | None]
OnReport = Callable[[RunReport], None]


def import_legacy_once(store: Store, config: AppConfig, run: LibraryRun) -> None:
    """Seed the database from v1's ``logs/*.txt`` the first time a library runs."""
    key = f"legacy_imported::{run.library}"
    if store.kv_get(key):
        return
    imported = store.import_legacy_logs(
        "logs", run.library, run.type.value, config.fingerprint(run)
    )
    store.kv_set(key, "1")
    if imported:
        log.info("Imported %d v1 progress entries for %s", imported, run.library)


def library_size(server, library: str) -> int:
    """Item count for a progress bar; 0 if Plex will not say."""
    try:
        return int(plex_client.get_section(server, library).totalSize)
    except Exception:  # only used for the bar's denominator
        return 0


async def run_libraries(
    config: AppConfig,
    store: Store,
    server,
    runs: list[LibraryRun],
    *,
    dry_run: bool = False,
    force: bool = False,
    only: set[str] | None = None,
    posters_dir: str | Path = "posters",
    on_library_start: OnLibraryStart | None = None,
    on_report: OnReport | None = None,
) -> list[RunReport]:
    """Process each library: tags first, then whichever post-actions apply."""
    only = set(only or ())
    pipeline = Pipeline(config, store, server, dry_run=dry_run, force=force)
    reports: list[RunReport] = []

    def emit(report: RunReport) -> None:
        reports.append(report)
        if on_report is not None:
            on_report(report)

    for run in runs:
        import_legacy_once(store, config, run)

        if not only or "tags" in only:
            progress: ProgressFn | None = None
            if on_library_start is not None:
                total = library_size(server, run.library)
                progress = on_library_start(run, total)
            emit(await pipeline.tag_library(run, progress=progress))

        if (not only and run.rate_media) or "ratings" in only:
            emit(await pipeline.rate_library(run))
        if (not only and run.create_rating_collections) or "rating-collections" in only:
            emit(await pipeline.rating_collections(run))
        if (not only and run.set_posters) or "posters" in only:
            emit(await pipeline.set_posters(run, str(Path(posters_dir) / run.type.value)))
        if (not only and run.sort_collections) or "sort" in only:
            emit(await pipeline.sort(run))

    return reports
