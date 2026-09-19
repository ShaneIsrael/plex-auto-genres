"""In-process job queue with live progress.

One job is one library processed with one set of options. Jobs run
**one at a time**: the provider rate limiters live inside each run, so two
concurrent runs would each believe they had the whole quota and trip 429s.
Sequential is also exactly what the nightly schedule always did.

Jobs are ephemeral orchestration; the durable record is the ``runs`` table
the pipeline writes. A job remembers which run ids it produced so the UI can
join the two.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from .config import AppConfig, LibraryRun
from .errors import PagError
from .models import ItemOutcome, RunReport
from .runner import run_libraries
from .store import Store

log = logging.getLogger(__name__)

JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]
Event = tuple[str, dict[str, Any]]


class JobConflict(PagError):
    """A job for that library is already queued or running."""


class JobError(PagError):
    """The job could not be set up (unknown library, no Plex, ...)."""


@dataclass(frozen=True, slots=True)
class JobOptions:
    """What a job should do. Mirrors the CLI flags."""

    dry_run: bool = False
    force: bool = False
    only: tuple[str, ...] = ()
    source: str = "api"  # "api" | "schedule"


@dataclass(slots=True)
class Progress:
    """Where the current action stands. Reset when the next action begins."""

    action: str | None = None
    run_id: str | None = None
    total: int = 0
    pending: int = 0
    done: int = 0
    written: int = 0
    unchanged: int = 0
    failed: int = 0
    title: str | None = None


@dataclass
class Job:
    """One queued/running/finished job."""

    job_id: str
    library: str
    options: JobOptions
    status: JobStatus = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    progress: Progress = field(default_factory=Progress)
    run_ids: list[str] = field(default_factory=list)
    reports: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    cancel_requested: bool = False
    subscribers: list[asyncio.Queue] = field(default_factory=list, repr=False)
    task: asyncio.Task | None = field(default=None, repr=False)

    @property
    def active(self) -> bool:
        return self.status in ("queued", "running")

    def snapshot(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "library": self.library,
            "status": self.status,
            "source": self.options.source,
            "dry_run": self.options.dry_run,
            "force": self.options.force,
            "only": list(self.options.only),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "run_ids": list(self.run_ids),
            "progress": asdict(self.progress),
            "reports": list(self.reports),
        }


class JobManager:
    """Queue, run, watch and cancel jobs."""

    def __init__(
        self,
        store: Store,
        config_provider: Callable[[], AppConfig],
        plex_provider: Callable[[], Awaitable[Any]],
        *,
        posters_dir: str | Path = "posters",
        history: int = 50,
    ) -> None:
        self.store = store
        self._config = config_provider
        self._plex = plex_provider
        self._posters_dir = posters_dir
        self._queue: asyncio.Queue[Job] = asyncio.Queue()
        self._active: dict[str, Job] = {}
        self._recent: deque[Job] = deque(maxlen=history)
        self._worker: asyncio.Task | None = None

    # -- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        """Start the single worker. Idempotent."""
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._work(), name="pag-jobs")

    async def stop(self) -> None:
        """Cancel whatever is running and stop the worker."""
        if self._worker is None:
            return
        self._worker.cancel()
        try:
            await self._worker
        except (asyncio.CancelledError, Exception):
            pass
        self._worker = None

    # -- queries ----------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        if job_id in self._active:
            return self._active[job_id]
        return next((j for j in self._recent if j.job_id == job_id), None)

    def list(self) -> list[Job]:
        """Active jobs in queue order, then recent ones newest first."""
        active = sorted(self._active.values(), key=lambda j: (j.status != "running", j.created_at))
        return [*active, *reversed(self._recent)]

    def active_for(self, library: str) -> Job | None:
        key = library.casefold()
        return next((j for j in self._active.values() if j.library.casefold() == key), None)

    def job_for_run(self, run_id: str) -> Job | None:
        for job in (*self._active.values(), *self._recent):
            if run_id in job.run_ids:
                return job
        return None

    # -- commands ---------------------------------------------------------

    def enqueue(self, library: str, options: JobOptions = JobOptions()) -> Job:
        """Queue a job for one configured library."""
        config = self._config()
        run = config.find(library)
        if run is None:
            raise JobError(f"Library {library!r} is not configured.")
        if self.active_for(run.library) is not None:
            raise JobConflict(f"A job for {run.library!r} is already queued or running.")
        job = Job(job_id=uuid.uuid4().hex[:12], library=run.library, options=options)
        self._active[job.job_id] = job
        self._queue.put_nowait(job)
        log.info("Job %s queued for %s (%s)", job.job_id, job.library, options.source)
        return job

    def enqueue_all(self, options: JobOptions = JobOptions()) -> list[Job]:
        """Queue every enabled library, skipping ones that already have a job."""
        jobs: list[Job] = []
        for run in self._config().libraries:
            if not run.enabled or self.active_for(run.library) is not None:
                continue
            jobs.append(self.enqueue(run.library, options))
        return jobs

    async def cancel(self, job_id: str) -> Job | None:
        """Cancel a queued or running job. Returns the job, or None if unknown."""
        job = self.get(job_id)
        if job is None:
            return None
        if job.status == "queued":
            job.cancel_requested = True
            self._finish(job, "cancelled")
        elif job.status == "running" and job.task is not None:
            job.cancel_requested = True
            job.task.cancel()  # the worker does the bookkeeping
        return job

    async def subscribe(self, job_id: str, *, heartbeat: float = 15.0) -> AsyncIterator[Event]:
        """Yield ``(event, data)`` for a job: a snapshot, then live events, then ``end``.

        A ``("ping", {})`` is yielded every ``heartbeat`` seconds of silence so
        an SSE route can keep the connection alive.
        """
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)

        yield ("snapshot", job.snapshot())
        if not job.active:
            yield ("end", self._end_payload(job))
            return

        queue: asyncio.Queue = asyncio.Queue(maxsize=2000)
        job.subscribers.append(queue)
        try:
            # Closed the gap between the snapshot and the subscription.
            if not job.active:
                yield ("end", self._end_payload(job))
                return
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), heartbeat)
                except asyncio.TimeoutError:
                    yield ("ping", {})
                    continue
                if message is None:
                    return
                yield message
        finally:
            try:
                job.subscribers.remove(queue)
            except ValueError:
                pass

    # -- internals --------------------------------------------------------

    async def _work(self) -> None:
        while True:
            job = await self._queue.get()
            if job.status != "queued":
                continue  # cancelled while waiting
            job.status = "running"
            job.started_at = time.time()
            self.emit(job, "status", job.snapshot())
            job.task = asyncio.create_task(self._execute(job), name=f"pag-job-{job.job_id}")
            try:
                await job.task
                self._finish(job, "done")
            except asyncio.CancelledError:
                if not job.cancel_requested:
                    # The worker itself is being stopped: take the job down too.
                    job.task.cancel()
                    await asyncio.gather(job.task, return_exceptions=True)
                    job.error = "server shutting down"
                    self._finish(job, "cancelled")
                    raise
                self._finish(job, "cancelled")
            except Exception as exc:  # a broken job must not kill the worker
                log.exception("Job %s failed", job.job_id)
                job.error = f"{type(exc).__name__}: {exc}"
                self._finish(job, "failed")

    async def _execute(self, job: Job) -> None:
        config = self._config()
        run = config.find(job.library)
        if run is None:
            raise JobError(f"Library {job.library!r} is no longer in the config.")
        server = await self._plex()
        await run_libraries(
            config, self.store, server, [run],
            dry_run=job.options.dry_run, force=job.options.force,
            only=set(job.options.only), posters_dir=self._posters_dir,
            observer=_JobObserver(self, job),
        )

    def _finish(self, job: Job, status: JobStatus) -> None:
        if status == "cancelled":
            self._collect_abandoned_reports(job)
        job.status = status
        job.finished_at = time.time()
        self.emit(job, "end", self._end_payload(job))
        for queue in job.subscribers:
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        self._active.pop(job.job_id, None)
        self._recent.append(job)
        log.info("Job %s %s (%s)", job.job_id, status, job.library)

    def _collect_abandoned_reports(self, job: Job) -> None:
        """Pick up the report of a run the pipeline closed while being cancelled.

        The pipeline writes that report to the store before the cancellation
        propagates, but the runner's ``report`` callback never fires because
        the action raised instead of returning. Read it back so the job's
        snapshot and ``end`` event agree with the run row.
        """
        known = {r.get("run_id") for r in job.reports}
        for run_id in job.run_ids:
            if run_id in known:
                continue
            row = self.store.get_run(run_id)
            if row is not None and row["report"]:
                job.reports.append(json.loads(row["report"]))

    @staticmethod
    def _end_payload(job: Job) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "status": job.status,
            "error": job.error,
            "run_ids": list(job.run_ids),
            "reports": list(job.reports),
        }

    def emit(self, job: Job, event: str, data: dict[str, Any]) -> None:
        """Fan an event out to every subscriber of ``job``."""
        for queue in job.subscribers:
            try:
                queue.put_nowait((event, data))
            except asyncio.QueueFull:
                # A subscriber that cannot keep up loses events; it still gets
                # the final report because that goes through _finish.
                pass


class _JobObserver:
    """Translates runner callbacks into job progress and events."""

    def __init__(self, manager: JobManager, job: Job) -> None:
        self._m = manager
        self._job = job

    def begin(self, _run: LibraryRun, action: str, run_id: str, total: int, pending: int) -> None:
        """A new action started: reset progress and remember its run id."""
        job = self._job
        job.run_ids.append(run_id)
        job.progress = Progress(action=action, run_id=run_id, total=total, pending=pending)
        self._m.emit(job, "begin", {
            "job_id": job.job_id, "library": job.library, "action": action,
            "run_id": run_id, "total": total, "pending": pending,
        })

    def item(self, _run: LibraryRun, outcome: ItemOutcome) -> None:
        """One item finished: bump the counters and broadcast."""
        job = self._job
        p = job.progress
        p.done += 1
        if outcome.status == "written":
            p.written += 1
        elif outcome.status == "unchanged":
            p.unchanged += 1
        elif outcome.status == "failed":
            p.failed += 1
        p.title = outcome.item.title
        self._m.emit(job, "item", {
            "job_id": job.job_id, "run_id": p.run_id, "done": p.done, "pending": p.pending,
            "written": p.written, "unchanged": p.unchanged, "failed": p.failed,
            "title": outcome.item.title, "status": outcome.status,
            "error": outcome.error,
        })

    def report(self, report: RunReport) -> None:
        """An action finished: keep its report and broadcast it."""
        job = self._job
        payload = report.as_dict()
        job.reports.append(payload)
        self._m.emit(
            job, "report", {"job_id": job.job_id, "run_id": report.run_id, "report": payload}
        )
