"""Plex GUID parsing -- the input to the 'use the id Plex already has' path."""

from __future__ import annotations

import pytest

from plex_auto_genres.models import ExternalId
from plex_auto_genres.plexsvc.client import parse_guid


@pytest.mark.parametrize("raw,expected", [
    # Modern Plex agents
    ("tmdb://1396", ExternalId("tmdb", "1396")),
    ("tvdb://81189", ExternalId("tvdb", "81189")),
    ("imdb://tt0903747", ExternalId("imdb", "tt0903747")),
    # Legacy com.plexapp.agents.* schemes
    ("com.plexapp.agents.themoviedb://1396?lang=en", ExternalId("tmdb", "1396")),
    ("com.plexapp.agents.thetvdb://81189/1/1?lang=en", ExternalId("tvdb", "81189")),
    ("com.plexapp.agents.imdb://tt0903747?lang=en", ExternalId("imdb", "tt0903747")),
    # HAMA, which anime libraries overwhelmingly use
    ("com.plexapp.agents.hama://anidb-4521?lang=en", ExternalId("anidb", "4521")),
    ("com.plexapp.agents.hama://tvdb-81189?lang=en", ExternalId("tvdb", "81189")),
    ("com.plexapp.agents.hama://mal-19?lang=fr", ExternalId("mal", "19")),
])
def test_recognised_guids(raw, expected):
    assert parse_guid(raw) == expected


@pytest.mark.parametrize("raw", [
    "plex://show/5d9c08254eefaa001f5d6dcb",   # Plex's opaque internal id
    "local://12345",
    "",
    "garbage",
    None,
])
def test_unusable_guids_return_none(raw):
    assert parse_guid(raw or "") is None


def test_external_id_renders_as_a_plex_style_guid():
    assert str(ExternalId("mal", "19")) == "mal://19"
