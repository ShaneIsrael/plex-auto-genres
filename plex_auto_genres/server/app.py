"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..errors import PagError
from ..jobs import JobOptions
from ..migration import legacy_logs_dir, migrate_install
from ..scheduler import Scheduler
from .auth import AuthMiddleware, AuthRuntime, AuthSettings
from .auth import router as auth_router
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
    auth: AuthSettings | None = None,
) -> FastAPI:
    """Build the app; a scheduler always runs inside the process.

    ``cron`` is the fallback expression (``--cron`` / ``CRON_SCHEDULE``); the
    config file's ``schedule`` block overrides it and can pause it, live.
    ``auth`` defaults to the environment (``PAG_WEB_PASSWORD`` & co). With no
    password the API is open; :func:`auth.check_bind` is what stops that from
    reaching a non-loopback interface unannounced.
    """
    auth_settings = auth if auth is not None else AuthSettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state = AppState(config_path, db_path, posters_dir=posters_dir)
        app.state.pag = state
        # An embedder (or the demo) starting straight from create_app() gets
        # the same v1 upgrade the CLI performs before it hands over.
        try:
            migrate_install(config_path, legacy_logs_dir(db_path), state.store)
        except OSError as exc:
            log.error("Could not complete the v1 upgrade (%s); continuing without it", exc)
        app.state.auth = AuthRuntime.build(auth_settings, state.store)
        if auth_settings.enabled:
            log.info("Authentication enabled (session cookie / bearer)")
        else:
            log.warning("Authentication DISABLED: anyone who can reach this port can use it")
        await state.jobs.start()

        async def scheduled() -> None:
            # Through the manager, so a scheduled pass shows up as jobs
            # with live progress and queues behind anything manual.
            jobs = state.jobs.enqueue_all(JobOptions(source="schedule"))
            log.info("Scheduled pass queued %d job(s)", len(jobs))

        state.scheduler = Scheduler(scheduled, settings=state.schedule_settings, fallback=cron)
        # --now runs the pass only when there is a schedule to run it for,
        # which is what the flag has always meant.
        start_now = run_on_start and state.scheduler.plan.cron is not None
        task = asyncio.create_task(state.scheduler.run_forever(run_now=start_now))
        if state.scheduler.plan.cron:
            log.info("Scheduler armed: %s (%s)",
                     state.scheduler.plan.cron, state.scheduler.plan.source)

        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            await state.jobs.stop()
            await state.aclose()
            state.close()

    app = FastAPI(
        title="plex-auto-genres",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.include_router(auth_router)
    app.include_router(router)
    app.add_middleware(AuthMiddleware)

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
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


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

    def static_file(path: str) -> Path | None:
        """A real file inside the static dir, or None: everything else is a route."""
        candidate = (static / path).resolve()
        if path and candidate.is_file() and static.resolve() in candidate.parents:
            return candidate
        return None

    @app.get("/{path:path}", include_in_schema=False)
    async def _spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="No such endpoint.")
        candidate = await asyncio.to_thread(static_file, path)  # stat() off the loop
        if candidate is not None:
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
