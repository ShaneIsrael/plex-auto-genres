"""Provider protocol and the shared HTTP plumbing."""

from __future__ import annotations

import abc
import asyncio
import logging
import random
from dataclasses import dataclass, field

import httpx

from ..errors import ProviderError, ProviderNotFound, ProviderRateLimited
from ..models import ExternalId, MediaType, ProviderResult
from ..ratelimit import CompositeLimiter

log = logging.getLogger(__name__)

USER_AGENT = "plex-auto-genres/2.0 (+https://github.com/Dim145/plex-auto-genres)"


@dataclass(slots=True)
class LookupRequest:
    """Everything a provider needs to resolve one Plex item."""

    title: str
    year: int | None
    media_type: MediaType
    use_keywords: bool = False
    #: External ids read straight off the Plex item's GUIDs.
    external_ids: list[ExternalId] = field(default_factory=list)
    #: A manual binding, which overrides both GUIDs and title search.
    pinned: ExternalId | None = None

    def id_for(self, *schemes: str) -> ExternalId | None:
        """The first external id matching any of ``schemes``, in that order."""
        for scheme in schemes:
            match = next((e for e in self.external_ids if e.scheme == scheme), None)
            if match is not None:
                return match
        return None


class HttpTransport:
    """Rate-limited HTTP with retry-on-transient, shared by all providers."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        limiter: CompositeLimiter,
        *,
        max_attempts: int = 3,
        name: str = "provider",
    ) -> None:
        self._client = client
        self._limiter = limiter
        self._max_attempts = max_attempts
        self._name = name
        self.request_count = 0

    async def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        """Perform a rate-limited request, retrying transient failures."""
        last_exc: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            await self._limiter.acquire()
            try:
                self.request_count += 1
                response = await self._client.request(  # type: ignore[arg-type]
                    method, url, **kwargs
                )
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = ProviderError(f"{self._name}: network error: {exc}")
                await self._sleep_backoff(attempt)
                continue

            if response.status_code == 429:
                retry_after = _parse_retry_after(response) or 2.0 * attempt
                await self._limiter.penalise(retry_after)
                last_exc = ProviderRateLimited(
                    f"{self._name}: rate limited", retry_after=retry_after
                )
                log.debug("%s: 429, backing off %.1fs", self._name, retry_after)
                continue

            if response.status_code == 404:
                raise ProviderNotFound(f"{self._name}: no record at {url}")

            if 500 <= response.status_code < 600:
                last_exc = ProviderError(f"{self._name}: HTTP {response.status_code}")
                await self._sleep_backoff(attempt)
                continue

            # v1 never inspected status codes at all: a 429 body became a
            # KeyError that aborted the whole run.
            response.raise_for_status()
            return response

        raise last_exc or ProviderError(f"{self._name}: exhausted retries for {url}")

    async def get_json(self, url: str, **kwargs: object) -> dict:
        response = await self.request("GET", url, **kwargs)
        return response.json()

    @staticmethod
    async def _sleep_backoff(attempt: int) -> None:
        # Full jitter, so parallel workers do not retry in lockstep.
        await asyncio.sleep(random.uniform(0, min(2 ** attempt * 0.25, 8.0)))


def _parse_retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


class Provider(abc.ABC):
    """A metadata source.

    Implementations resolve an item either directly by external id -- which is
    exact and skips a search round trip -- or by title as a fallback.
    """

    #: Stable name used in config, the cache and CLI output.
    name: str = "provider"
    #: Plex GUID schemes this provider can consume without a search.
    guid_schemes: tuple[str, ...] = ()
    #: Library types this provider can serve.
    supports: tuple[MediaType, ...] = ()

    def __init__(self, transport: HttpTransport) -> None:
        self.transport = transport

    @property
    def request_count(self) -> int:
        return self.transport.request_count

    def handles(self, media_type: MediaType) -> bool:
        return media_type in self.supports

    @abc.abstractmethod
    async def fetch_by_id(self, external_id: ExternalId, request: LookupRequest) -> ProviderResult:
        """Resolve an exact provider id. Raises :class:`ProviderNotFound` if absent."""

    @abc.abstractmethod
    async def search(self, request: LookupRequest) -> ProviderResult:
        """Resolve by title. Raises :class:`ProviderNotFound` when nothing matches."""

    async def resolve(self, request: LookupRequest) -> ProviderResult:
        """Preferred path first: pinned binding, then GUID, then title search.

        Reading the id off the Plex GUID is the single biggest accuracy win
        over v1, which always searched by title and blindly took result [0].
        """
        if request.pinned is not None and request.pinned.scheme in self.guid_schemes:
            return await self.fetch_by_id(request.pinned, request)

        direct = request.id_for(*self.guid_schemes)
        if direct is not None:
            try:
                return await self.fetch_by_id(direct, request)
            except ProviderNotFound:
                log.debug("%s: guid %s missing upstream, falling back to search",
                          self.name, direct)

        return await self.search(request)


def pick_best(
    candidates: list[tuple[str, int | None, object]],
    title: str,
    year: int | None,
) -> object | None:
    """Choose the closest candidate by title equality then year proximity.

    v1 took ``results[0]`` unconditionally. Using the year Plex already knows
    removes most of the mismatches that produced nonsense genres.
    """
    if not candidates:
        return None
    target = _normalise(title)

    def score(entry: tuple[str, int | None, object]) -> tuple[int, int]:
        cand_title, cand_year, _ = entry
        name_score = 0 if _normalise(cand_title) == target else 1
        if year is None or cand_year is None:
            year_score = 1
        else:
            year_score = abs(cand_year - year)
            # More than a couple of years apart is almost certainly a different work.
            year_score = year_score if year_score <= 2 else 50 + year_score
        return name_score, year_score

    return min(candidates, key=score)[2]


def _normalise(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch.isalnum())
