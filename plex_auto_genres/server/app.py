"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..config import AppConfig
from ..errors import PagError
from ..plexsvc import client as plex_client
from ..runner import run_libraries
from ..scheduler import run_forever
from .routes import router
from .state import AppState

log = logging.getLogger(__name__)

#: Where the built UI lives inside the package, when it has been built.
PACKAGE_STATIC = Path(__file__).parent / "static"


def resolve_static_dir(explicit: str | Path | None = None) -> Path | None:
    """The directory holding ``index.html``, or ``None`` when the UI is not built.

    An explicit path is taken at its word: if the operator points at a
    directory and it holds no build, that is a misconfiguration to surface,
    not something to paper over by serving whatever the package happens to
    contain. Only when nothing is specified do we look at ``PAG_STATIC_DIR``
    and then the package's own ``static/``.
    """
    if explicit is not None:
        path = Path(explicit)
        return path if (path / "index.html").is_file() else None
    for candidate in (os.getenv("PAG_STATIC_DIR"), PACKAGE_STATIC):
        if candidate and (Path(candidate) / "index.html").is_file():
            return Path(candidate)
    return None


def create_app(
    config_path: str | Path = "config/config.json",
    db_path: str | Path = "logs/plex-auto-genres.db",
    *,
    cron: str | None = None,
    run_on_start: bool = False,
    posters_dir: str | Path = "posters",
    static_dir: str | Path | None = None,
) -> FastAPI:
    """Build the app. ``cron`` also starts the scheduler inside the process."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state = AppState(config_path, db_path)
        app.state.pag = state
        task: asyncio.Task | None = None

        if cron:
            state.scheduler_cron = cron

            def note_next(fire_at: float) -> None:
                state.scheduler_next = fire_at

            async def scheduled() -> None:
                await _scheduled_pass(state, posters_dir)

            task = asyncio.create_task(
                run_forever(cron, scheduled, run_now=run_on_start, on_schedule=note_next)
            )
            log.info("Scheduler armed: %s", cron)

        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            state.close()

    app = FastAPI(
        title="plex-auto-genres",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.include_router(router)

    @app.exception_handler(PagError)
    async def _pag_error(_: Request, exc: PagError) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"title": type(exc).__name__, "status": 500, "detail": str(exc)},
        )

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"title": _reason(exc.status_code), "status": exc.status_code,
                     "detail": exc.detail},
        )

    _mount_ui(app, resolve_static_dir(static_dir))
    return app


def _reason(status: int) -> str:
    return {404: "Not Found", 503: "Service Unavailable", 400: "Bad Request"}.get(status, "Error")


def _mount_ui(app: FastAPI, static: Path | None) -> None:
    """Serve the SPA: real files as-is, unknown paths fall back to index.html."""
    if static is None:
        @app.get("/{path:path}", include_in_schema=False)
        async def _not_built(path: str) -> JSONResponse:
            if path.startswith("api/"):
                raise HTTPException(status_code=404, detail="No such endpoint.")
            return JSONResponse(
                status_code=503,
                content={
                    "title": "UI not built",
                    "status": 503,
                    "detail": (
                        "The web UI has not been built. Run `pnpm --dir ui build`, or during "
                        "development run `pnpm --dir ui dev` and open http://localhost:5173."
                    ),
                    "api": "/api/docs",
                },
            )
        return

    assets = static / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    index = static / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    async def _spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="No such endpoint.")
        candidate = (static / path).resolve()
        # Only serve files inside the static dir; everything else is a route.
        if path and candidate.is_file() and static.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


async def _scheduled_pass(state: AppState, posters_dir: str | Path) -> None:
    """One scheduled execution over every enabled library."""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log.info("--- scheduled run %s ---", stamp)
    config: AppConfig = state.config()
    runs = [r for r in config.libraries if r.enabled]
    if not runs:
        log.info("No enabled libraries; nothing to do")
        return
    server = await asyncio.to_thread(plex_client.connect, config.plex)
    reports = await run_libraries(
        config, state.store, server, runs, posters_dir=posters_dir,
        on_report=lambda r: log.info(
            "%s/%s: %d written, %d failed, %d skipped",
            r.library, r.action, r.written, r.failed, r.skipped,
        ),
    )
    log.info("Scheduled run finished: %d report(s)", len(reports))
