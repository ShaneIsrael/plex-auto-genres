"""MyAnimeList taxonomy drift.

MAL reorganised its genre list after this project was first written, so configs
carried over from 2021-2022 still reference names the API no longer returns.
Those entries are not errors -- they simply never match anything, silently.
``plex-auto-genres doctor`` uses this table to point them out.
"""

from __future__ import annotations

import json

from .store import Store

GENRES_URL = "https://api.jikan.moe/v4/genres/anime"
CACHE_KEY = "mal_taxonomy_v1"
CACHE_TTL_S = 30 * 86400.0
#: After a failed fetch, do not retry for this long: doctor runs from health
#: probes and page loads, and each retry is a full network timeout.
UNAVAILABLE_KEY = "mal_taxonomy_unavailable"
UNAVAILABLE_TTL_S = 15 * 60.0

#: Old MAL name -> the name that replaced it, or ``None`` when it was retired
#: without a direct successor.
RENAMED: dict[str, str | None] = {
    "cars": "Racing",
    "dementia": None,          # folded into Avant Garde / Psychological
    "demons": None,            # folded into Supernatural / Mythology
    "game": "Strategy Game",   # also split into Video Game
    "magic": None,             # folded into Fantasy
    "thriller": "Suspense",
    "shoujo ai": "Girls Love",
    "shounen ai": "Boys Love",
    "martial arts": "Martial Arts",
    "military": "Military",
    "police": "Organized Crime",
    "super power": "Super Power",
    "space": "Space",
}


def fetch_live_genres(store: Store, *, timeout: float = 20.0) -> list[str] | None:
    """Current MAL genre names, cached for a month. ``None`` if unreachable."""
    cached = store.kv_get(CACHE_KEY)
    if cached:
        try:
            return json.loads(cached)
        except json.JSONDecodeError:
            pass
    if store.kv_get(UNAVAILABLE_KEY):
        return None

    import httpx

    try:
        response = httpx.get(GENRES_URL, timeout=timeout)
        response.raise_for_status()
        names = sorted(
            entry["name"] for entry in response.json().get("data", []) if entry.get("name")
        )
    except Exception:
        store.kv_set(UNAVAILABLE_KEY, "1", UNAVAILABLE_TTL_S)
        return None

    store.kv_set(CACHE_KEY, json.dumps(names), CACHE_TTL_S)
    return names


def check_names(names: list[str], live: list[str]) -> list[tuple[str, str | None]]:
    """Return ``(configured_name, suggested_replacement)`` for stale entries."""
    live_lower = {n.casefold() for n in live}
    stale: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for name in names:
        key = name.strip().casefold()
        # A name can appear in sortedCollections and in replace at once; report
        # it once.
        if not key or key in live_lower or key in seen:
            continue
        seen.add(key)
        stale.append((name, RENAMED.get(key)))
    return stale
