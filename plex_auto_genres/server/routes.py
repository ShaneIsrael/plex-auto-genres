"""Read-only API. Every handler is a thin translation over the service layer."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from .. import __version__
from ..config import DEFAULT_PROVIDERS, config_json_schema, validate_document, write_config
from ..doctor import run_doctor
from ..errors import ConfigError, PlexConnectionError, ProviderError
from ..jobs import Job, JobConflict, JobError, JobOptions
from ..models import MediaItem, MediaType
from ..pipeline import media_key
from ..plexsvc.writer import undo_run
from ..providers import GUID_SCHEMES, LookupRequest, build_providers
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


# -- library browser ---------------------------------------------------------


def _match_source(item: MediaItem, entry, bound: bool) -> str:
    if bound:
        return "binding"
    schemes: set[str] = set()
    for name in entry.resolved_providers:
        schemes.update(GUID_SCHEMES.get(name, ()))
    if entry.type.is_anime:
        schemes.add("anidb")  # translated through the mapping table
    return "guid" if any(g.scheme in schemes for g in item.guids) else "search"


@router.get("/libraries/{name}/items", response_model=schemas.ItemsPage)
async def library_items(
    request: Request,
    name: str,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    q: str | None = Query(default=None, max_length=200),
    status: Literal["all", "ok", "failed", "unprocessed", "bound"] = "all",
    refresh: bool = False,
) -> schemas.ItemsPage:
    """A page of a configured library's items, joined with match and cache state."""
    state = _state(request)
    config = _config_or_503(state)
    entry = config.find(name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Library {name!r} is not configured.")

    try:
        items = await state.library_items(entry.library, refresh=refresh)
    except PlexConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    states = state.store.states_for_library(entry.library)
    providers = state.store.providers_for_library(entry.library)
    bound = {
        row["media_key"]: schemas.BindingView(**dict(row))
        for row in state.store.list_bindings(entry.library)
    }

    def classify(item: MediaItem) -> tuple[str, str]:
        key = media_key(item)
        cached = states.get(key)
        bucket = "unprocessed" if cached is None else cached.status
        return key, bucket

    counts = {"all": len(items), "ok": 0, "failed": 0, "unprocessed": 0, "bound": 0}
    rows: list[tuple[MediaItem, str, str]] = []
    needle = q.casefold().strip() if q else ""
    for item in items:
        key, bucket = classify(item)
        counts[bucket] += 1
        if key in bound:
            counts["bound"] += 1
        if needle and needle not in item.title.casefold():
            continue
        if status == "bound" and key not in bound:
            continue
        if status in ("ok", "failed", "unprocessed") and bucket != status:
            continue
        rows.append((item, key, bucket))

    rows.sort(key=lambda r: (r[0].title.casefold(), r[0].year or 0))
    start = (page - 1) * size
    views: list[schemas.ItemView] = []
    for item, key, _bucket in rows[start:start + size]:
        cached = states.get(key)
        provider, provider_id = providers.get(key, (None, None))
        views.append(schemas.ItemView(
            rating_key=item.rating_key,
            media_key=key,
            title=item.title,
            year=item.year,
            thumb=item.thumb,
            guids=[str(g) for g in item.guids],
            match=_match_source(item, entry, key in bound),  # type: ignore[arg-type]
            binding=bound.get(key),
            state=schemas.ItemState(
                status=cached.status,  # type: ignore[arg-type]
                provider=provider,
                provider_id=provider_id,
                genres=cached.genres,
                attempts=cached.attempts,
                last_error=cached.last_error,
                updated_at=cached.updated_at,
            ) if cached else None,
            current_genres=item.current_genres,
            current_collections=item.current_collections,
        ))

    return schemas.ItemsPage(
        library=entry.library, total=len(rows), page=page, size=size, counts=counts, items=views
    )


@router.post("/libraries/{name}/items/forget", status_code=200)
async def forget_item(
    request: Request, name: str, media_key_: str = Query(alias="media_key")
) -> dict:
    """Drop one item's cache entry so the next run looks at it again."""
    state = _state(request)
    config = _config_or_503(state)
    entry = config.find(name)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Library {name!r} is not configured.")
    return {"forgotten": state.store.forget(entry.library, media_key_)}


# -- candidate search ----------------------------------------------------------


@router.get("/search", response_model=list[schemas.CandidateView])
async def search_candidates(
    request: Request,
    q: str = Query(min_length=1, max_length=200),
    type: MediaType = Query(alias="type"),  # pylint: disable=redefined-builtin
    provider: str | None = None,
    year: int | None = Query(default=None, ge=1800, le=2100),
    limit: int = Query(default=8, ge=1, le=20),
) -> list[schemas.CandidateView]:
    """Ranked suggestions from one provider, for picking a binding by hand."""
    state = _state(request)
    config = _config_or_503(state)
    name = provider or DEFAULT_PROVIDERS[type][0]
    try:
        pool = build_providers((name,), type, config.providers)
    except (ConfigError, ProviderError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    lookup = LookupRequest(title=q, year=year, media_type=type)
    async with pool:
        try:
            found = await pool.providers[0].search_candidates(lookup, limit=limit)
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return [schemas.CandidateView(**c.as_dict()) for c in found]


# -- bindings CRUD -------------------------------------------------------------


@router.post("/bindings", response_model=schemas.BindingView, status_code=201)
async def create_binding(request: Request, body: schemas.BindingIn) -> schemas.BindingView:
    """Pin an item to a provider id. Replaces an existing binding for that item."""
    state = _state(request)
    config = _config_or_503(state)
    entry = config.find(body.library)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Library {body.library!r} is not configured.")
    state.store.set_binding(
        entry.library, body.media_key, body.provider, body.provider_id, body.note
    )
    row = next(
        r for r in state.store.list_bindings(entry.library) if r["media_key"] == body.media_key
    )
    return schemas.BindingView(**dict(row))


@router.delete("/bindings", status_code=200)
async def delete_binding(
    request: Request, library: str, media_key_: str = Query(alias="media_key")
) -> dict:
    """Remove a binding; the item's cached match goes with it."""
    state = _state(request)
    removed = state.store.delete_binding(library, media_key_)
    if not removed:
        raise HTTPException(status_code=404, detail="No such binding.")
    return {"removed": True}


# -- poster proxy --------------------------------------------------------------


@router.get("/plex/thumb", include_in_schema=False)
async def plex_thumb(request: Request, path: str) -> Response:
    """Fetch a poster from Plex with our token, so the browser never sees it."""
    if not path.startswith("/library/") or ".." in path or "?" in path:
        raise HTTPException(status_code=400, detail="Not a Plex artwork path.")
    state = _state(request)
    try:
        server = await state.plex()
    except PlexConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    url = server.url(path, includeToken=True)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            upstream = await client.get(url)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Plex artwork fetch failed: {exc}") from exc
    if upstream.status_code != 200:
        raise HTTPException(status_code=404, detail="No artwork.")
    return Response(
        content=upstream.content,
        media_type=upstream.headers.get("content-type", "image/jpeg"),
        headers={"Cache-Control": "public, max-age=86400"},
    )
