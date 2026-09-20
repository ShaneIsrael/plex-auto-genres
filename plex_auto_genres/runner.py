"""Run one or more configured libraries end to end.

This is the piece every front end shares: the CLI drives it with progress
bars and a confirmation prompt, the job manager drives it from HTTP and from
the scheduler. It knows nothing about argparse, SSE or terminals; whoever
calls it passes a :class:`RunObserver` if they want to watch.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from .config import AppConfig, LibraryRun
from .models import ItemOutcome, RunReport
from .pipeline import Pipeline
from .store import Store

log = logging.getLogger(__name__)

#: Actions that ``--only`` can select.
ACTIONS = ("tags", "posters", "sort", "ratings", "rating-collections")


class RunObserver(Protocol):
    """Callbacks a caller may implement to follow a run as it happens."""

    def begin(self, run: LibraryRun, action: str, run_id: str, total: int, pending: int) -> None:
        """An action has sized its work: ``total`` items, ``pending`` to process."""

    def item(self, run: LibraryRun, outcome: ItemOutcome) -> None:
        """One item finished."""

    def report(self, report: RunReport) -> None:
        """One action finished."""


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
    observer: RunObserver | None = None,
) -> list[RunReport]:
    """Process each library: tags first, then whichever post-actions apply.

    Cancelling the awaiting task cancels the run in progress; the pipeline
    closes its run row as ``cancelled`` before the exception propagates.
    """
    only = set(only or ())
    pipeline = Pipeline(config, store, server, dry_run=dry_run, force=force)
    reports: list[RunReport] = []

    def hooks(run: LibraryRun, action: str):
        if observer is None:
            return None, None
        return (
            lambda run_id, total, pending: observer.begin(run, action, run_id, total, pending),
            lambda outcome: observer.item(run, outcome),
        )

    def emit(report: RunReport) -> None:
        reports.append(report)
        if observer is not None:
            observer.report(report)

    for run in runs:
        if not only or "tags" in only:
            on_begin, progress = hooks(run, "genres" if run.use_genres else "collections")
            emit(await pipeline.tag_library(run, progress=progress, on_begin=on_begin))

        if (not only and run.rate_media) or "ratings" in only:
            on_begin, progress = hooks(run, "ratings")
            emit(await pipeline.rate_library(run, progress=progress, on_begin=on_begin))
        if (not only and run.create_rating_collections) or "rating-collections" in only:
            on_begin, progress = hooks(run, "rating-collections")
            emit(await pipeline.rating_collections(run, progress=progress, on_begin=on_begin))
        if (not only and run.set_posters) or "posters" in only:
            on_begin, _ = hooks(run, "posters")
            posters = str(Path(posters_dir) / run.type.value)
            emit(await pipeline.set_posters(run, posters, on_begin=on_begin))
        if (not only and run.sort_collections) or "sort" in only:
            on_begin, _ = hooks(run, "sort")
            emit(await pipeline.sort(run, on_begin=on_begin))

    return reports
