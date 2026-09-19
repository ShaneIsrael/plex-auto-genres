"""Process-wide state for the server: config, store, and a cached Plex link."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import AppConfig, etag_of, load_config
from ..errors import ConfigError, PlexConnectionError
from ..jobs import JobManager
from ..models import MediaItem
from ..plexsvc import client as plex_client
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
        self.scheduler_cron: str | None = None
        self.scheduler_next: float | None = None
        self.jobs = JobManager(self.store, self.config, self.plex, posters_dir=posters_dir)

        self._config: AppConfig | None = None
        self._config_mtime: float | None = None
        self._config_etag: str | None = None
        self._plex = None
        self._link = PlexLink(reachable=False)
        self._plex_ttl = plex_ttl_s
        self._plex_lock = asyncio.Lock()
        self._items: dict[str, tuple[float, list[MediaItem]]] = {}
        self._items_lock = asyncio.Lock()
        self.items_ttl_s = 60.0

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
        """Hash of the file as last loaded; ``None`` if it cannot be read."""
        try:
            self.config()
        except ConfigError:
            return None
        return self._config_etag

    def invalidate_config(self) -> None:
        """Force the next :meth:`config` call to re-read the file."""
        self._config = None
        self._config_mtime = None
        self._config_etag = None

    # -- plex -------------------------------------------------------------

    async def plex(self):
        """A connected ``PlexServer``. Raises :class:`PlexConnectionError`."""
        async with self._plex_lock:
            fresh = time.time() - self._link.checked_at < self._plex_ttl
            if self._plex is not None and fresh:
                return self._plex
            if self._plex is None and fresh and self._link.error:
                raise PlexConnectionError(self._link.error)

            try:
                settings = self.config().plex
                server = await asyncio.to_thread(plex_client.connect, settings)
            except (ConfigError, PlexConnectionError) as exc:
                self._plex = None
                self._link = PlexLink(False, error=str(exc), checked_at=time.time())
                raise PlexConnectionError(str(exc)) from exc

            self._plex = server
            self._link = PlexLink(
                True,
                server_name=getattr(server, "friendlyName", None),
                version=getattr(server, "version", None),
                checked_at=time.time(),
            )
            return server

    async def plex_link(self) -> PlexLink:
        """Connection status without raising."""
        try:
            await self.plex()
        except PlexConnectionError:
            pass
        return self._link

    # -- library items ----------------------------------------------------

    async def library_items(self, library: str, *, refresh: bool = False) -> list[MediaItem]:
        """Every item of a library, read from Plex and cached for a minute.

        The browser filters on things Plex does not know (our cache status,
        bindings), so it needs the whole list; reading it once per minute is
        a handful of paged requests and keeps searching and paging instant.
        """
        key = library.casefold()
        async with self._items_lock:
            cached = self._items.get(key)
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
