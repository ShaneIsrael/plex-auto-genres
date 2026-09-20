"""AniList, via its public GraphQL API.

Worth having alongside Jikan: it allows 90 requests/minute against Jikan's
60, returns genres *and* community tags in the same call, accepts a MAL id
directly (so a Plex ``mal://`` GUID resolves without a search), and tends to
carry entries Jikan's search ranks poorly.
"""

from __future__ import annotations

import re

from ..errors import ProviderNotFound
from ..models import Candidate, ExternalId, MediaType, ProviderResult
from .base import LookupRequest, Provider, pick_best, rank_candidates, score_of, short

ENDPOINT = "https://graphql.anilist.co"

_MEDIA_FIELDS = """
    id
    idMal
    title { romaji english native }
    genres
    tags { name rank isGeneralSpoiler }
    averageScore
    startDate { year }
    siteUrl
    coverImage { medium }
    description(asHtml: false)
"""

_TAG_RE = re.compile(r"<[^>]+>")

_BY_ID_QUERY = f"""
query ($id: Int, $malId: Int) {{
  Media(id: $id, idMal: $malId, type: ANIME) {{ {_MEDIA_FIELDS} }}
}}
"""

_SEARCH_QUERY = f"""
query ($search: String, $seasonYear: Int) {{
  Page(page: 1, perPage: 10) {{
    media(search: $search, seasonYear: $seasonYear, type: ANIME) {{ {_MEDIA_FIELDS} }}
  }}
}}
"""

#: Community tags below this agreement percentage are noise.
TAG_RANK_THRESHOLD = 70


class AniListProvider(Provider):
    """Genres and high-agreement community tags from AniList."""

    name = "anilist"
    guid_schemes = ("anilist", "mal")
    supports = (MediaType.ANIME,)

    async def _graphql(self, query: str, variables: dict) -> dict:
        response = await self.transport.request(
            "POST", ENDPOINT, json={"query": query, "variables": variables}
        )
        payload = response.json()
        if payload.get("errors"):
            message = "; ".join(e.get("message", "?") for e in payload["errors"])
            raise ProviderNotFound(f"anilist: {message}")
        return payload.get("data") or {}

    async def fetch_by_id(self, external_id: ExternalId, request: LookupRequest) -> ProviderResult:
        if not external_id.value.isdigit():
            raise ProviderNotFound(f"anilist: non-numeric id {external_id.value!r}")
        variables = (
            {"id": int(external_id.value)}
            if external_id.scheme == "anilist"
            else {"malId": int(external_id.value)}
        )
        data = await self._graphql(_BY_ID_QUERY, variables)
        media = data.get("Media")
        if not media:
            raise ProviderNotFound(f"anilist: nothing for {external_id}")
        return self._to_result(media)

    async def search(self, request: LookupRequest) -> ProviderResult:
        from .jikan import clean_anime_title

        title = clean_anime_title(request.title)
        variables: dict = {"search": title}
        if request.year:
            variables["seasonYear"] = request.year

        data = await self._graphql(_SEARCH_QUERY, variables)
        entries = ((data.get("Page") or {}).get("media")) or []
        if not entries and request.year:
            data = await self._graphql(_SEARCH_QUERY, {"search": title})
            entries = ((data.get("Page") or {}).get("media")) or []
        if not entries:
            raise ProviderNotFound(f"anilist: no anime matching {title!r}")

        candidates: list[tuple[str, int | None, dict]] = [
            (_best_title(e), (e.get("startDate") or {}).get("year"), e) for e in entries
        ]
        best = pick_best(candidates, title, request.year)
        if best is None:
            raise ProviderNotFound(f"anilist: no anime matching {title!r}")
        return self._to_result(best)

    async def search_candidates(self, request: LookupRequest, limit: int = 8) -> list[Candidate]:
        from .jikan import clean_anime_title

        title = clean_anime_title(request.title)
        data = await self._graphql(_SEARCH_QUERY, {"search": title})
        entries: list[dict] = ((data.get("Page") or {}).get("media")) or []
        ranked = rank_candidates(
            [(_best_title(e), (e.get("startDate") or {}).get("year"), e) for e in entries],
            title, request.year,
        )
        out: list[Candidate] = []
        for media in ranked[:limit]:
            description = media.get("description") or ""
            out.append(Candidate(
                provider=self.name,
                provider_id=str(media.get("id")),
                title=_best_title(media),
                year=(media.get("startDate") or {}).get("year"),
                url=media.get("siteUrl"),
                image=(media.get("coverImage") or {}).get("medium"),
                synopsis=short(_TAG_RE.sub(" ", description)),
                # AniList scores out of 100; Plex's rate() wants 0-10.
                score=score_of(media.get("averageScore"), scale=10),
                genres=list(media.get("genres") or []),
            ))
        return out

    def _to_result(self, media: dict) -> ProviderResult:
        genres = list(media.get("genres") or [])
        for tag in media.get("tags") or []:
            if tag.get("isGeneralSpoiler"):
                continue
            if (tag.get("rank") or 0) >= TAG_RANK_THRESHOLD and tag.get("name"):
                genres.append(tag["name"])

        return ProviderResult(
            provider=self.name,
            provider_id=str(media.get("id")),
            title=_best_title(media),
            # AniList scores out of 100; Plex's rate() wants 0-10.
            score=score_of(media.get("averageScore"), scale=10),
            genres=genres,
            url=media.get("siteUrl"),
        )


def _best_title(media: dict) -> str:
    title = media.get("title") or {}
    return title.get("romaji") or title.get("english") or title.get("native") or ""
