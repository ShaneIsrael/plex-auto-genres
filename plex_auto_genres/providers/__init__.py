"""Provider registry and construction."""

from __future__ import annotations

import httpx

from ..config import ProviderSettings
from ..errors import ConfigError, ProviderAuthError
from ..models import MediaType
from ..ratelimit import ANILIST_LIMITS, JIKAN_LIMITS, TMDB_LIMITS, shared_limiter
from .anidb_map import AniDbMapper
from .anilist import AniListProvider
from .base import HttpTransport, LookupRequest, Provider, USER_AGENT
from .jikan import JikanProvider
from .tmdb import TmdbProvider

__all__ = [
    "GUID_SCHEMES",
    "AniDbMapper",
    "AniListProvider",
    "JikanProvider",
    "LookupRequest",
    "Provider",
    "ProviderPool",
    "TmdbProvider",
    "build_providers",
]

_LIMITS = {"jikan": JIKAN_LIMITS, "anilist": ANILIST_LIMITS, "tmdb": TMDB_LIMITS}
_CLASSES: dict[str, type[Provider]] = {
    "jikan": JikanProvider, "anilist": AniListProvider, "tmdb": TmdbProvider,
}

#: Which Plex GUID schemes each provider resolves without a search. Class
#: attributes, so no credentials are needed to consult this.
GUID_SCHEMES: dict[str, tuple[str, ...]] = {
    "jikan": JikanProvider.guid_schemes,
    "anilist": AniListProvider.guid_schemes,
    "tmdb": TmdbProvider.guid_schemes,
}


class ProviderPool:
    """Owns the HTTP client and the provider instances for one run."""

    def __init__(self, providers: list[Provider], client: httpx.AsyncClient) -> None:
        self.providers = providers
        self._client = client

    @property
    def request_count(self) -> int:
        return sum(p.request_count for p in self.providers)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "ProviderPool":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


def build_providers(
    names: tuple[str, ...],
    media_type: MediaType,
    settings: ProviderSettings,
) -> ProviderPool:
    """Instantiate the requested providers, sharing one connection pool.

    Everything that can be refused is checked before the client exists, so a
    bad request (unknown provider, wrong type, missing TMDB key) never leaves
    an unclosed pool behind.
    """
    if not names:
        raise ConfigError(f"No providers configured for a {media_type.value} library.")
    for name in names:
        cls = _CLASSES.get(name)
        if cls is None:
            raise ConfigError(f"Unknown provider {name!r}. Known: {', '.join(sorted(_LIMITS))}.")
        if media_type not in cls.supports:
            raise ConfigError(
                f"Provider {name!r} cannot serve a {media_type.value} library. "
                f"It supports: {', '.join(t.value for t in cls.supports)}."
            )
        if name == "tmdb" and not settings.tmdb_api_key:
            raise ProviderAuthError(
                "TMDB_API_KEY is not set. It is required for standard-tv and "
                "standard-movie libraries."
            )

    client = httpx.AsyncClient(
        timeout=httpx.Timeout(20.0, connect=10.0),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
        limits=httpx.Limits(max_connections=settings.concurrency * 2),
    )
    built: list[Provider] = []
    for name in names:
        transport = HttpTransport(
            client, shared_limiter(name, _LIMITS[name]),
            max_attempts=settings.max_attempts, name=name,
        )
        if name == "tmdb":
            built.append(
                TmdbProvider(transport, settings.tmdb_api_key or "", settings.tmdb_language)
            )
        else:
            built.append(_CLASSES[name](transport))
    return ProviderPool(built, client)
