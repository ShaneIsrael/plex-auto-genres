"""Process-wide state for the server: config, store, and a cached Plex link."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..config import AppConfig, etag_of, load_config, read_config_etag
from ..errors import ConfigError, PlexConnectionError
from ..jobs import Job, JobManager
from ..models import MediaItem
from ..plexsvc import client as plex_client
from ..scheduler import Scheduler
from ..store import Store

log = logging.getLogger(__name__)


@dataclass(slots=True)
class PlexLink:
    """Result of the last connection attempt."""

    reachable: bool
    server_name: str | None = None
    version: str | None = None
    error: str | None = None
    checked_at: float = 0.0


class AppState:
    """Owned by the app lifespan; handed to routes via ``request.app.state``."""

    def __init__(
        self,
        config_path: str | Path,
        db_path: str | Path,
        *,
        plex_ttl_s: float = 60.0,
        posters_dir: str | Path = "posters",
    ):
        self.config_path = Path(config_path)
        self.db_path = Path(db_path)
        self.store = Store(self.db_path)
        self.started_at = time.time()
        #: Set by the app lifespan when a scheduler runs in this process.
        self.scheduler: Scheduler | None = None
        self.jobs = JobManager(
            self.store, self.config, self.plex, posters_dir=posters_dir,
            on_finish=self._after_job,
        )
        #: Shared client for the poster proxy: one pool for the whole process.
        self.http = httpx.AsyncClient(timeout=15.0)

        self._config: AppConfig | None = None
        self._config_mtime: float | None = None
        self._config_etag: str | None = None
        self._plex = None
        self._link = PlexLink(reachable=False)
        self._plex_ttl = plex_ttl_s
        self._plex_lock = asyncio.Lock()
        self._items: dict[str, tuple[float, list[MediaItem]]] = {}
        self._item_locks: dict[str, asyncio.Lock] = {}
        self.items_ttl_s = 60.0

    async def aclose(self) -> None:
        await self.http.aclose()

    def close(self) -> None:
        self.store.close()

    # -- config -----------------------------------------------------------

    def config(self) -> AppConfig:
        """The current config, re-read whenever the file changes on disk."""
        try:
            mtime = self.config_path.stat().st_mtime
        except OSError as exc:
            raise ConfigError(f"No configuration file at {self.config_path}.") from exc
        if self._config is None or mtime != self._config_mtime:
            self._config = load_config(self.config_path)
            self._config_mtime = mtime
            self._config_etag = etag_of(self.config_path.read_text(encoding="utf-8"))
            log.info("Config loaded from %s", self.config_path)
        return self._config

    def config_etag(self) -> str | None:
        """Hash of the file on disk; ``None`` only if it cannot be read at all.

        A file that no longer parses still has a hash: that is exactly when
        ``If-Match`` must keep a stale form from overwriting it.
        """
        try:
            self.config()
        except ConfigError:
            return read_config_etag(self.config_path)
        return self._config_etag

    def invalidate_config(self) -> None:
        """Force the next :meth:`config` call to re-read the file."""
        self._config = None
        self._config_mtime = None
        self._config_etag = None
        if self.scheduler is not None:
            self.scheduler.replan()

    def schedule_settings(self) -> tuple[str | None, bool] | None:
        """The config's schedule block for the scheduler; ``None`` if unreadable."""
        try:
            schedule = self.config().schedule
        except ConfigError:
            return None
        return schedule.cron, schedule.enabled

    # -- plex -------------------------------------------------------------

    async def plex(self):
        """A connected ``PlexServer``. Raises :class:`PlexConnectionError`.

        The connection is kept for as long as it answers; the TTL only bounds
        how often it is re-checked, so /health polling does not rebuild it.
        """
        async with self._plex_lock:
            now = time.time()
            if now - self._link.checked_at < self._plex_ttl:
                if self._plex is not None:
                    return self._plex
                raise PlexConnectionError(self._link.error or "Plex is unreachable.")

            if self._plex is not None:
                try:
                    await asyncio.to_thread(plex_client.ping, self._plex)
                except PlexConnectionError as exc:
                    log.warning("Plex connection lost (%s); reconnecting", exc)
                    self._drop_plex()
                else:
                    self._link = self._link_for(self._plex, now)
                    return self._plex

            try:
                settings = self.config().plex
                server = await asyncio.to_thread(plex_client.connect, settings)
            except (ConfigError, PlexConnectionError) as exc:
                self._plex = None
                self._link = PlexLink(False, error=str(exc), checked_at=time.time())
                raise PlexConnectionError(str(exc)) from exc

            self._plex = server
            self._link = self._link_for(server, time.time())
            return server

    @staticmethod
    def _link_for(server, checked_at: float) -> PlexLink:
        return PlexLink(
            True,
            server_name=getattr(server, "friendlyName", None),
            version=getattr(server, "version", None),
            checked_at=checked_at,
        )

    def _drop_plex(self) -> None:
        session = getattr(self._plex, "_session", None)
        if session is not None:
            with contextlib.suppress(Exception):
                session.close()
        self._plex = None

    async def plex_link(self) -> PlexLink:
        """Connection status without raising."""
        with contextlib.suppress(PlexConnectionError):
            await self.plex()
        return self._link

    # -- library items ----------------------------------------------------

    async def library_items(self, library: str, *, refresh: bool = False) -> list[MediaItem]:
        """Every item of a library, read from Plex and cached for a minute.

        The browser filters on things Plex does not know (our cache status,
        bindings), so it needs the whole list; reading it once per minute is
        a handful of paged requests and keeps searching and paging instant.
        One lock per library: a cold read of a large section must not stall
        requests for every other library.
        """
        key = library.casefold()
        cached = self._items.get(key)
        if cached and not refresh and time.time() - cached[0] < self.items_ttl_s:
            return cached[1]
        lock = self._item_locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._items.get(key)  # another request may have filled it meanwhile
            if cached and not refresh and time.time() - cached[0] < self.items_ttl_s:
                return cached[1]
            server = await self.plex()
            items = await asyncio.to_thread(plex_client.iter_library, server, library)
            self._items[key] = (time.time(), items)
            return items

    def forget_items(self, library: str | None = None) -> None:
        """Drop the item cache, for one library or all."""
        if library is None:
            self._items.clear()
        else:
            self._items.pop(library.casefold(), None)

    def _after_job(self, job: Job) -> None:
        # The job wrote tags: whatever the browser cached for that library is stale.
        self.forget_items(job.library)
