"""MyAnimeList, through the public Jikan v4 API."""

from __future__ import annotations

from ..errors import ProviderNotFound
from ..models import ExternalId, MediaType, ProviderResult
from .base import LookupRequest, Provider, pick_best

BASE_URL = "https://api.jikan.moe/v4"


def clean_anime_title(title: str) -> str:
    """Trim the bracketed release tags fansub groups leave in folder names."""
    title = title.split(" [", 1)[0].strip()
    words = title.split()
    if len(words) > 10:
        title = " ".join(words[:10])
    return title


class JikanProvider(Provider):
    """Genres, themes and demographics from MyAnimeList."""

    name = "jikan"
    #: ``mal`` comes straight from Plex's MAL agent; ``anidb`` is translated by
    #: the offline mapping table before it reaches us.
    guid_schemes = ("mal",)
    supports = (MediaType.ANIME,)

    async def fetch_by_id(self, external_id: ExternalId, request: LookupRequest) -> ProviderResult:
        payload = await self.transport.get_json(f"{BASE_URL}/anime/{external_id.value}")
        data = payload.get("data")
        if not data:
            raise ProviderNotFound(f"jikan: MAL id {external_id.value} returned no data")
        return self._to_result(data)

    async def search(self, request: LookupRequest) -> ProviderResult:
        title = clean_anime_title(request.title)
        payload = await self.transport.get_json(
            f"{BASE_URL}/anime", params={"q": title, "limit": 10}
        )
        results = payload.get("data") or []
        if not results:
            raise ProviderNotFound(f"jikan: no anime matching {title!r}")

        candidates = [(_best_title(entry), _year_of(entry), entry) for entry in results]
        best = pick_best(candidates, title, request.year)

        # The search payload already carries genres, so unlike v1 there is no
        # mandatory second request just to read them.
        if best.get("genres") or best.get("themes"):  # type: ignore[union-attr]
            return self._to_result(best)  # type: ignore[arg-type]
        best_id = str(best["mal_id"])  # type: ignore[index]
        return await self.fetch_by_id(ExternalId("mal", best_id), request)

    def _to_result(self, data: dict) -> ProviderResult:
        # MAL splits its taxonomy across three buckets; a user asking for
        # "genres" wants all of them (Isekai and Shounen both live outside
        # "genres" proper).
        names: list[str] = []
        for bucket in ("genres", "explicit_genres", "themes", "demographics"):
            names.extend(entry["name"] for entry in (data.get(bucket) or []) if entry.get("name"))

        score = data.get("score")
        return ProviderResult(
            provider=self.name,
            provider_id=str(data.get("mal_id")),
            title=_best_title(data),
            genres=names,
            score=float(score) if isinstance(score, (int, float)) and score > 0 else None,
            url=data.get("url"),
        )


def _best_title(entry: dict) -> str:
    return entry.get("title") or entry.get("title_english") or entry.get("title_japanese") or ""


def _year_of(entry: dict) -> int | None:
    if isinstance(entry.get("year"), int):
        return entry["year"]
    aired_from = ((entry.get("aired") or {}).get("from") or "")[:4]
    return int(aired_from) if aired_from.isdigit() else None
