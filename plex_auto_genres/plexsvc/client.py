"""Connecting to Plex and reading libraries."""

from __future__ import annotations

import logging
import re

from plexapi.exceptions import NotFound, Unauthorized
from plexapi.myplex import MyPlexAccount
from plexapi.server import PlexServer

from ..config import PlexSettings
from ..errors import PlexConnectionError
from ..models import KNOWN_GUID_SCHEMES, ExternalId, MediaItem

log = logging.getLogger(__name__)

#: Plex's pre-2021 agents encoded the source inside the GUID scheme.
_LEGACY_AGENTS = {
    "com.plexapp.agents.themoviedb": "tmdb",
    "com.plexapp.agents.thetvdb": "tvdb",
    "com.plexapp.agents.imdb": "imdb",
    "com.plexapp.agents.hama": None,  # source is encoded in the value instead
    "com.plexapp.agents.myanimelist": "mal",
}
#: HAMA writes e.g. ``com.plexapp.agents.hama://anidb-12345?lang=en``.
_HAMA_VALUE = re.compile(r"^(?P<source>anidb|tvdb|tmdb|mal|anilist)-(?P<id>[\w]+)")


def parse_guid(raw: str) -> ExternalId | None:
    """Turn any Plex GUID string into a normalised external id.

    Handles the modern ``tmdb://1234`` form, the legacy
    ``com.plexapp.agents.*`` schemes, and HAMA's ``hama://anidb-1234`` values.
    Returns ``None`` for ids we cannot use, such as ``plex://show/5d9c...``.
    """
    if not raw or "://" not in raw:
        return None
    scheme, _, value = raw.partition("://")
    scheme = scheme.strip().lower()
    value = value.split("?", 1)[0].strip()

    if scheme in _LEGACY_AGENTS:
        mapped = _LEGACY_AGENTS[scheme]
        if mapped is None:  # HAMA
            match = _HAMA_VALUE.match(value)
            if not match:
                return None
            return ExternalId(match.group("source"), match.group("id"))
        return ExternalId(mapped, value.split("/", 1)[0])

    # Whitelist rather than blacklist: 'plex://', 'local://' and any future
    # Plex-internal scheme are useless to a metadata provider, and silently
    # passing one through would send garbage to the providers.
    parsed = ExternalId.parse(raw)
    if parsed is None or parsed.scheme not in KNOWN_GUID_SCHEMES:
        return None
    return parsed


def connect(settings: PlexSettings) -> PlexServer:
    """Open a Plex connection, preferring token auth."""
    settings.validate_usable()
    try:
        if settings.uses_token_auth:
            log.debug("Connecting to Plex at %s with a token", settings.base_url)
            return PlexServer(settings.base_url, settings.token, timeout=int(settings.timeout_s))
        log.debug("Connecting to Plex as %s via plex.tv", settings.username)
        account = MyPlexAccount(settings.username, settings.password)
        return account.resource(settings.server_name).connect(timeout=int(settings.timeout_s))
    except Unauthorized as exc:
        raise PlexConnectionError(
            "Plex rejected the credentials. Check PLEX_TOKEN (or username/password)."
        ) from exc
    except NotFound as exc:
        raise PlexConnectionError(
            f"Plex server {settings.server_name!r} not found on this account."
        ) from exc
    except Exception as exc:  # plexapi raises bare requests errors too
        raise PlexConnectionError(f"Could not connect to Plex: {exc}") from exc


def get_section(server: PlexServer, library: str):
    """Look up a library section, listing the available ones if it is missing."""
    try:
        return server.library.section(library)
    except NotFound as exc:
        available = ", ".join(sorted(s.title for s in server.library.sections()))
        raise PlexConnectionError(
            f"No Plex library named {library!r}. Available: {available}."
        ) from exc


def iter_library(server: PlexServer, library: str, *, page_size: int = 200) -> list[MediaItem]:
    """Read a whole library into flat :class:`MediaItem` records.

    ``includeGuids=1`` makes Plex return the external ids inline, so the whole
    library costs a handful of paged requests instead of one extra metadata
    GET per item (which is what lazily touching ``item.guids`` would do).
    """
    section = get_section(server, library)
    try:
        raw_items = server.fetchItems(
            f"/library/sections/{section.key}/all",
            params={"includeGuids": 1},
            container_size=page_size,
        )
    except Exception as exc:
        log.debug("includeGuids listing failed (%s); falling back to section.all()", exc)
        raw_items = section.all()

    items: list[MediaItem] = []
    for raw in raw_items:
        guids: list[ExternalId] = []
        for guid_obj in getattr(raw, "guids", None) or []:
            parsed = parse_guid(getattr(guid_obj, "id", "") or "")
            if parsed is not None:
                guids.append(parsed)
        # The primary agent GUID sometimes carries the only usable id.
        primary = parse_guid(getattr(raw, "guid", "") or "")
        if primary is not None and primary not in guids:
            guids.append(primary)

        items.append(
            MediaItem(
                rating_key=int(raw.ratingKey),
                title=raw.title,
                year=getattr(raw, "year", None),
                guids=guids,
                current_genres=[t.tag for t in (getattr(raw, "genres", None) or [])],
                current_collections=[t.tag for t in (getattr(raw, "collections", None) or [])],
                thumb=getattr(raw, "thumb", None) or None,
                handle=raw,
            )
        )
    return items
