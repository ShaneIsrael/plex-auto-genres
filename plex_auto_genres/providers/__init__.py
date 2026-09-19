"""Provider registry and construction."""

from __future__ import annotations

import httpx

from ..config import ProviderSettings
from ..errors import ConfigError
from ..models import MediaType
from ..ratelimit import ANILIST_LIMITS, JIKAN_LIMITS, TMDB_LIMITS
from .anidb_map import AniDbMapper
from .anilist import AniListProvider
from .base import HttpTransport, LookupRequest, Provider, USER_AGENT
from .jikan import JikanProvider
from .tmdb import TmdbProvider

__all__ = [
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
    """Instantiate the requested providers, sharing one connection pool."""
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(20.0, connect=10.0),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
        limits=httpx.Limits(max_connections=settings.concurrency * 2),
    )

    built: list[Provider] = []
    for name in names:
        spec = _LIMITS.get(name)
        if spec is None:
            raise ConfigError(f"Unknown provider {name!r}. Known: {', '.join(sorted(_LIMITS))}.")
        transport = HttpTransport(
            client, spec.build(), max_attempts=settings.max_attempts, name=name
        )
        if name == "tmdb":
            provider: Provider = TmdbProvider(
                transport, settings.tmdb_api_key or "", settings.tmdb_language
            )
        elif name == "jikan":
            provider = JikanProvider(transport)
        else:
            provider = AniListProvider(transport)

        if not provider.handles(media_type):
            raise ConfigError(
                f"Provider {name!r} cannot serve a {media_type.value} library. "
                f"It supports: {', '.join(t.value for t in provider.supports)}."
            )
        built.append(provider)

    if not built:
        raise ConfigError(f"No providers configured for a {media_type.value} library.")
    return ProviderPool(built, client)
