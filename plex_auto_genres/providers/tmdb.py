"""The Movie Database.

Written directly against the v3 REST API rather than through ``tmdbv3api``.
That wrapper was synchronous, and its ``AsObj`` unwrapping differed between
movies (``.keywords``) and TV (``.results``) -- the mismatch that made
``--use-keywords`` raise ``AttributeError`` on every movie in v1.
"""

from __future__ import annotations

from ..errors import ProviderAuthError, ProviderNotFound
from ..models import ExternalId, MediaType, ProviderResult
from .base import LookupRequest, Provider, pick_best

BASE_URL = "https://api.themoviedb.org/3"


def split_compound_genres(names: list[str]) -> list[str]:
    """Expand TMDB's compound TV genres into their parts.

    TMDB's TV taxonomy bundles pairs: ``Sci-Fi & Fantasy``,
    ``Action & Adventure``, ``War & Politics``. v1 split on ``' & '`` and then
    kept only ``[0]``, silently discarding Fantasy, Adventure and Politics --
    so those collections could never be created for a TV library.
    """
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        for part in (p.strip() for p in name.split(" & ")):
            if part and part.casefold() not in seen:
                seen.add(part.casefold())
                out.append(part)
    return out


class TmdbProvider(Provider):
    """Genres and keywords from The Movie Database."""

    name = "tmdb"
    guid_schemes = ("tmdb",)
    supports = (MediaType.STANDARD_TV, MediaType.STANDARD_MOVIE)

    def __init__(self, transport, api_key: str, language: str = "en-US") -> None:
        super().__init__(transport)
        if not api_key:
            raise ProviderAuthError(
                "TMDB_API_KEY is not set. It is required for standard-tv and "
                "standard-movie libraries."
            )
        self._api_key = api_key
        self._language = language

    def _params(self, **extra: object) -> dict:
        return {"api_key": self._api_key, "language": self._language, **extra}

    @staticmethod
    def _segment(media_type: MediaType) -> str:
        return "movie" if media_type.is_movie else "tv"

    async def fetch_by_id(self, external_id: ExternalId, request: LookupRequest) -> ProviderResult:
        segment = self._segment(request.media_type)
        # append_to_response folds the keywords lookup into the details call,
        # halving the round trips v1 needed.
        payload = await self.transport.get_json(
            f"{BASE_URL}/{segment}/{external_id.value}",
            params=self._params(append_to_response="keywords"),
        )
        return self._to_result(payload, request, str(external_id.value))

    async def search(self, request: LookupRequest) -> ProviderResult:
        segment = self._segment(request.media_type)
        params = self._params(query=request.title, include_adult=True)
        if request.year:
            # TMDB spells the year filter differently per endpoint.
            params["year" if request.media_type.is_movie else "first_air_date_year"] = request.year

        payload = await self.transport.get_json(f"{BASE_URL}/search/{segment}", params=params)
        results = payload.get("results") or []
        if not results and request.year:
            # The year filter is strict; retry without it before giving up.
            params.pop("year", None)
            params.pop("first_air_date_year", None)
            payload = await self.transport.get_json(f"{BASE_URL}/search/{segment}", params=params)
            results = payload.get("results") or []
        if not results:
            raise ProviderNotFound(f"tmdb: no {segment} matching {request.title!r}")

        candidates = [
            (
                entry.get("title") or entry.get("name") or "",
                _year_of(entry),
                entry,
            )
            for entry in results
        ]
        best = pick_best(candidates, request.title, request.year)
        tmdb_id = best["id"]  # type: ignore[index]

        details = await self.transport.get_json(
            f"{BASE_URL}/{segment}/{tmdb_id}",
            params=self._params(append_to_response="keywords"),
        )
        return self._to_result(details, request, str(tmdb_id))

    def _to_result(self, payload: dict, request: LookupRequest, tmdb_id: str) -> ProviderResult:
        segment = self._segment(request.media_type)
        title = payload.get("title") or payload.get("name") or request.title

        if request.use_keywords:
            block = payload.get("keywords") or {}
            # /movie/{id} nests the list under "keywords"; /tv/{id} under "results".
            raw = block.get("keywords") if request.media_type.is_movie else block.get("results")
            names = [k["name"] for k in (raw or []) if k.get("name")]
        else:
            names = [g["name"] for g in (payload.get("genres") or []) if g.get("name")]
            names = split_compound_genres(names)

        score = payload.get("vote_average")
        return ProviderResult(
            provider=self.name,
            provider_id=tmdb_id,
            title=title,
            genres=names,
            score=float(score) if isinstance(score, (int, float)) and score > 0 else None,
            url=f"https://www.themoviedb.org/{segment}/{tmdb_id}",
        )


def _year_of(entry: dict) -> int | None:
    raw = entry.get("release_date") or entry.get("first_air_date") or ""
    head = raw.split("-", 1)[0]
    return int(head) if head.isdigit() else None
