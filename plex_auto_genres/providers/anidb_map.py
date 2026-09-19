"""AniDB -> MAL/AniList id mapping.

Plex anime libraries scanned with the HAMA agent carry ``anidb://`` GUIDs,
which neither Jikan nor AniList understands. The community-maintained
``Fribb/anime-lists`` table bridges them, turning a GUID we cannot use into an
exact provider id -- no title search, no mismatch.

The upstream file is ~6 MB, so only the two columns we need are kept, and the
condensed map is cached in the store for a week.
"""

from __future__ import annotations

import json
import logging

import httpx

from ..models import ExternalId
from ..store import Store

MAPPING_URL = "https://raw.githubusercontent.com/Fribb/anime-lists/master/anime-list-mini.json"
CACHE_KEY = "anidb_mapping_v1"
CACHE_TTL_S = 7 * 86400.0

log = logging.getLogger(__name__)


class AniDbMapper:
    """Resolves ``anidb://`` ids to ``mal://`` / ``anilist://`` ids."""

    def __init__(self, store: Store, *, enabled: bool = True) -> None:
        self._store = store
        self._enabled = enabled
        self._map: dict[str, dict[str, str]] | None = None
        self._failed = False

    async def _load(self) -> dict[str, dict[str, str]]:
        if self._map is not None:
            return self._map

        cached = self._store.kv_get(CACHE_KEY)
        if cached:
            try:
                self._map = json.loads(cached)
                return self._map
            except json.JSONDecodeError:
                log.debug("cached AniDB mapping was corrupt, refetching")

        log.info("Downloading the AniDB -> MAL mapping table (once a week)...")
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await client.get(MAPPING_URL)
            response.raise_for_status()
            entries = response.json()

        condensed: dict[str, dict[str, str]] = {}
        for entry in entries:
            anidb_id = entry.get("anidb_id")
            if anidb_id is None:
                continue
            ids: dict[str, str] = {}
            if entry.get("mal_id") is not None:
                ids["mal"] = str(entry["mal_id"])
            if entry.get("anilist_id") is not None:
                ids["anilist"] = str(entry["anilist_id"])
            if ids:
                condensed[str(anidb_id)] = ids

        self._store.kv_set(CACHE_KEY, json.dumps(condensed, separators=(",", ":")), CACHE_TTL_S)
        log.info("AniDB mapping ready: %d entries", len(condensed))
        self._map = condensed
        return condensed

    async def expand(self, ids: list[ExternalId]) -> list[ExternalId]:
        """Append any provider ids derivable from an ``anidb://`` GUID."""
        anidb = next((i for i in ids if i.scheme == "anidb"), None)
        if anidb is None or not self._enabled or self._failed:
            return ids
        if any(i.scheme in ("mal", "anilist") for i in ids):
            return ids  # already have something directly usable

        try:
            mapping = await self._load()
        except (httpx.HTTPError, ValueError) as exc:
            # Non-fatal: we simply fall back to searching by title.
            log.warning("AniDB mapping unavailable (%s); falling back to title search", exc)
            self._failed = True
            return ids

        extra = mapping.get(anidb.value)
        if not extra:
            return ids
        return [*ids, *(ExternalId(scheme, value) for scheme, value in extra.items())]
