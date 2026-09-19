"""Read-only API. Every handler is a thin translation over the service layer."""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, Query, Request

from .. import __version__
from ..config import config_json_schema
from ..doctor import run_doctor
from ..errors import ConfigError, PlexConnectionError
from ..models import MediaType
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


def _run_view(row, server_started_at: float) -> schemas.RunView:
    report = json.loads(row["report"]) if row["report"] else None
    if row["undone_at"]:
        status = "undone"
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
    )


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
                section_type=section.type,
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
            last_run=_run_view(last[0], state.started_at) if last else None,
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
    return [_run_view(row, state.started_at) for row in rows]


@router.get("/runs/{run_id}", response_model=schemas.RunView)
async def run(request: Request, run_id: str) -> schemas.RunView:
    """One run by id."""
    state = _state(request)
    row = state.store.get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No run {run_id!r}.")
    return _run_view(row, state.started_at)


@router.get("/bindings", response_model=list[schemas.BindingView])
async def bindings(request: Request, library: str | None = None) -> list[schemas.BindingView]:
    state = _state(request)
    return [schemas.BindingView(**dict(row)) for row in state.store.list_bindings(library)]


@router.get("/meta/types")
async def media_types() -> list[str]:
    """The library types the config accepts."""
    return [t.value for t in MediaType]
