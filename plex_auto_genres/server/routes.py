"""Read-only API. Every handler is a thin translation over the service layer."""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .. import __version__
from ..config import config_json_schema, validate_document, write_config
from ..doctor import run_doctor
from ..errors import ConfigError, PlexConnectionError
from ..jobs import Job, JobConflict, JobError, JobOptions
from ..models import MediaType
from ..plexsvc.writer import undo_run
from . import schemas
from .state import AppState

router = APIRouter(prefix="/api/v1", tags=["v1"])


def _state(request: Request) -> AppState:
    return request.app.state.pag


def _config_or_503(state: AppState):
    try:
        return state.config()
    except ConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _run_view(row, server_started_at: float, job: Job | None = None) -> schemas.RunView:
    report = json.loads(row["report"]) if row["report"] else None
    if row["undone_at"]:
        status = "undone"
    elif report and report.get("cancelled"):
        status = "cancelled"
    elif row["finished_at"] is None:
        # A run with no finish time that predates this process cannot still
        # be running; the process that owned it is gone.
        status = "running" if row["started_at"] >= server_started_at else "interrupted"
    elif report is None:
        status = "interrupted"
    elif report.get("failed", 0) and not report.get("written", 0):
        status = "failed"
    elif report.get("failed", 0):
        status = "partial"
    else:
        status = "ok"
    return schemas.RunView(
        run_id=row["run_id"],
        library=row["library"],
        action=row["action"],
        dry_run=bool(row["dry_run"]),
        status=status,
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        undone_at=row["undone_at"],
        report=report,
        job_id=job.job_id if job else None,
    )


def _job_view(job: Job) -> schemas.JobView:
    return schemas.JobView.model_validate(job.snapshot())


@router.get("/health", response_model=schemas.Health)
async def health(request: Request) -> schemas.Health:
    """Liveness plus the three things an operator glances at."""
    state = _state(request)
    link = await state.plex_link()
    scheduler = None
    if state.scheduler_cron:
        scheduler = schemas.SchedulerStatus(
            cron=state.scheduler_cron, next_fire_at=state.scheduler_next
        )
    return schemas.Health(
        status="ok" if link.reachable else "degraded",
        version=__version__,
        uptime_s=time.time() - state.started_at,
        started_at=state.started_at,
        config_path=str(state.config_path),
        db_path=str(state.db_path),
        plex=schemas.PlexStatus(
            reachable=link.reachable,
            server_name=link.server_name,
            version=link.version,
            error=link.error,
            checked_at=link.checked_at or None,
        ),
        scheduler=scheduler,
    )


@router.get("/config", response_model=schemas.ConfigView, response_model_by_alias=True)
async def get_config(request: Request) -> schemas.ConfigView:
    """The config as the UI should see it: everything except secret values."""
    state = _state(request)
    config = _config_or_503(state)
    return schemas.ConfigView(
        path=str(state.config_path),
        etag=state.config_etag(),
        version=config.version,
        defaults=config.defaults,
        libraries=config.libraries,
        secrets=schemas.Secrets(
            plex_token=bool(config.plex.token),
            plex_password=bool(config.plex.password),
            tmdb_api_key=bool(config.providers.tmdb_api_key),
            plex_base_url=config.plex.base_url,
            plex_server_name=config.plex.server_name,
            collection_prefix=config.plex.collection_prefix,
        ),
        providers={
            "tmdb_language": config.providers.tmdb_language,
            "concurrency": config.providers.concurrency,
            "max_attempts": config.providers.max_attempts,
        },
    )


@router.get("/config/schema")
async def get_schema() -> dict:
    """JSON Schema for the config file; the form generator's input."""
    return config_json_schema()


@router.post("/config/validate", response_model=schemas.ValidationResult)
async def validate_config(body: schemas.ConfigDocument) -> schemas.ValidationResult:
    """Dry validation for live form feedback. Always 200; the verdict is in the body."""
    _, errors = validate_document(body.model_dump())
    return schemas.ValidationResult(ok=not errors, errors=errors)  # type: ignore[arg-type]


@router.put("/config", response_model=schemas.SaveResult)
async def put_config(request: Request, body: schemas.ConfigDocument):
    """Validate, then atomically replace the config file.

    Send the ``etag`` from GET as ``If-Match``: if the file changed on disk in
    the meantime the write is refused with 412 rather than clobbering it.
    """
    state = _state(request)
    if_match = request.headers.get("if-match")
    current = state.config_etag()
    if if_match and current and if_match.strip('"') != current:
        return JSONResponse(
            status_code=412,
            headers={"ETag": f'"{current}"'},
            content={
                "title": "Precondition Failed",
                "status": 412,
                "detail": "The config file changed on disk since you loaded it. "
                          "Reload it before saving again.",
                "etag": current,
            },
        )

    document = body.model_dump()
    _, errors = validate_document(document)
    if errors:
        return JSONResponse(
            status_code=422,
            content=schemas.SaveResult(
                ok=False, path=str(state.config_path), errors=errors  # type: ignore[arg-type]
            ).model_dump(),
        )

    etag, backup = await asyncio.to_thread(write_config, state.config_path, document)
    state.invalidate_config()
    return schemas.SaveResult(
        ok=True, etag=etag, path=str(state.config_path),
        backup=str(backup) if backup else None,
    )


@router.get("/doctor")
async def doctor(request: Request) -> dict:
    """The same checks as ``plex-auto-genres doctor``, as structured JSON."""
    state = _state(request)
    report = await asyncio.to_thread(run_doctor, str(state.config_path), state.store)
    return report.as_dict()


@router.get("/libraries", response_model=list[schemas.LibraryView], response_model_by_alias=True)
async def libraries(request: Request) -> list[schemas.LibraryView]:
    """Configured libraries merged with what Plex actually has."""
    state = _state(request)
    config = _config_or_503(state)

    sections: dict[str, schemas.PlexSection] = {}
    try:
        server = await state.plex()
        for section in await asyncio.to_thread(server.library.sections):
            count = None
            try:
                count = int(await asyncio.to_thread(lambda s=section: s.totalSize))
            except Exception:  # count is decoration; never fail the listing for it
                pass
            sections[section.title.casefold()] = schemas.PlexSection(
                key=int(section.key),
                section_type=getattr(section, "type", "unknown"),
                item_count=count,
                agent=getattr(section, "agent", None),
            )
    except PlexConnectionError:
        pass  # /health carries the error; the listing degrades to config-only

    views: list[schemas.LibraryView] = []
    seen: set[str] = set()
    for entry in config.libraries:
        key = entry.library.casefold()
        seen.add(key)
        last = state.store.recent_runs(1, entry.library)
        views.append(schemas.LibraryView(
            name=entry.library,
            configured=True,
            enabled=entry.enabled,
            type=entry.type,
            providers=list(entry.resolved_providers),
            useGenres=entry.use_genres,
            clearGenres=entry.clear_genres,
            plex=sections.get(key),
            stats=state.store.stats(entry.library),
            last_run=(
                _run_view(last[0], state.started_at, state.jobs.job_for_run(last[0]["run_id"]))
                if last else None
            ),
        ))

    for key, section in sections.items():
        if key in seen or section.section_type not in ("movie", "show"):
            continue
        views.append(
            schemas.LibraryView(name=_title_of(server, key), configured=False, plex=section)
        )

    return views


def _title_of(server, casefolded: str) -> str:
    for section in server.library.sections():
        if section.title.casefold() == casefolded:
            return section.title
    return casefolded


@router.get("/runs", response_model=list[schemas.RunView])
async def runs(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    library: str | None = None,
) -> list[schemas.RunView]:
    """Run history, newest first."""
    state = _state(request)
    rows = state.store.recent_runs(limit, library)
    return [
        _run_view(row, state.started_at, state.jobs.job_for_run(row["run_id"])) for row in rows
    ]


@router.get("/runs/{run_id}", response_model=schemas.RunView)
async def run(request: Request, run_id: str) -> schemas.RunView:
    """One run by id."""
    state = _state(request)
    row = state.store.get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No run {run_id!r}.")
    return _run_view(row, state.started_at, state.jobs.job_for_run(run_id))


@router.post("/runs/{run_id}/undo", response_model=schemas.UndoResult)
async def undo(request: Request, run_id: str) -> schemas.UndoResult:
    """Restore the tags a run overwrote."""
    state = _state(request)
    row = state.store.get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No run {run_id!r}.")
    if row["undone_at"]:
        raise HTTPException(status_code=409, detail="That run was already undone.")
    if state.jobs.active_for(row["library"]) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"A job is running for {row['library']!r}; wait for it or cancel it first.",
        )
    if not state.store.snapshots_for(run_id):
        raise HTTPException(
            status_code=400, detail="That run recorded no changes, so there is nothing to undo."
        )
    try:
        server = await state.plex()
    except PlexConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    restored, skipped = await asyncio.to_thread(undo_run, server, state.store, run_id)
    return schemas.UndoResult(run_id=run_id, restored=restored, skipped=skipped)


@router.get("/bindings", response_model=list[schemas.BindingView])
async def bindings(request: Request, library: str | None = None) -> list[schemas.BindingView]:
    state = _state(request)
    return [schemas.BindingView(**dict(row)) for row in state.store.list_bindings(library)]


@router.get("/meta/types")
async def media_types() -> list[str]:
    """The library types the config accepts."""
    return [t.value for t in MediaType]


# -- jobs ------------------------------------------------------------------


def _options(body: schemas.RunOptions, source: str = "api") -> JobOptions:
    return JobOptions(
        dry_run=body.dry_run, force=body.force, only=tuple(body.only), source=source
    )


@router.get("/jobs", response_model=list[schemas.JobView])
async def jobs(request: Request) -> list[schemas.JobView]:
    """Queued and running jobs first, then recently finished ones."""
    return [_job_view(job) for job in _state(request).jobs.list()]


@router.post("/jobs", response_model=list[schemas.JobView], status_code=202)
async def start_jobs(request: Request, body: schemas.StartJobs) -> list[schemas.JobView]:
    """Queue jobs for the named libraries, or for every enabled one."""
    state = _state(request)
    _config_or_503(state)
    options = _options(body)
    try:
        if body.libraries is None:
            created = state.jobs.enqueue_all(options)
        else:
            # All or nothing: check for conflicts before queueing anything.
            for name in body.libraries:
                if state.jobs.active_for(name) is not None:
                    raise JobConflict(f"A job for {name!r} is already queued or running.")
            created = [state.jobs.enqueue(name, options) for name in body.libraries]
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except JobError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [_job_view(job) for job in created]


@router.post("/libraries/{name}/run", response_model=schemas.JobView, status_code=202)
async def run_library(
    request: Request, name: str, body: schemas.RunOptions | None = None
) -> schemas.JobView:
    """Queue a job for one library."""
    state = _state(request)
    _config_or_503(state)
    try:
        job = state.jobs.enqueue(name, _options(body or schemas.RunOptions()))
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except JobError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _job_view(job)


@router.get("/jobs/{job_id}", response_model=schemas.JobView)
async def get_job(request: Request, job_id: str) -> schemas.JobView:
    """One job by id."""
    found = _state(request).jobs.get(job_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"No job {job_id!r}.")
    return _job_view(found)


@router.post("/jobs/{job_id}/cancel", response_model=schemas.JobView)
async def cancel_job(request: Request, job_id: str) -> schemas.JobView:
    """Cancel a queued or running job."""
    state = _state(request)
    found = state.jobs.get(job_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"No job {job_id!r}.")
    if not found.active:
        raise HTTPException(status_code=409, detail=f"Job {job_id!r} already {found.status}.")
    await state.jobs.cancel(job_id)
    return _job_view(found)


@router.get("/jobs/{job_id}/events", include_in_schema=False)
async def job_events(request: Request, job_id: str) -> StreamingResponse:
    """Server-sent events: a snapshot, then begin/item/report events, then end."""
    state = _state(request)
    if state.jobs.get(job_id) is None:
        raise HTTPException(status_code=404, detail=f"No job {job_id!r}.")

    async def stream():
        yield "retry: 3000\n\n"
        async for event, data in state.jobs.subscribe(job_id):
            if event == "ping":
                yield ": ping\n\n"
                continue
            yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
