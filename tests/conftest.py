"""Shared fixtures and fakes."""

from __future__ import annotations

import json

import pytest

from plex_auto_genres.models import ExternalId, MediaItem
from plex_auto_genres.store import Store


class FakePlexItem:
    """Stands in for a plexapi video object, recording every edit payload."""

    def __init__(self, rating_key=1, title="Test", year=2020, genres=(), collections=()):
        self.ratingKey = rating_key
        self.title = title
        self.year = year
        self.genres = [FakeTag(g) for g in genres]
        self.collections = [FakeTag(c) for c in collections]
        self.edits: list[dict] = []
        self.ratings: list[float] = []

    def edit(self, **kwargs):
        self.edits.append(kwargs)
        return self

    def rate(self, rating=None):
        # Mirrors plexapi's own validation, which v1 tripped over: None
        # clears the rating (-1 on the wire), anything else must be 0-10.
        if rating is None:
            self.ratings.append(-1.0)
            return self
        if not isinstance(rating, (int, float)) or not 0 <= rating <= 10:
            raise ValueError("Rating must be between 0 to 10.")
        self.ratings.append(float(rating))
        return self

    @property
    def last_tags(self) -> list[str]:
        """The tag list the most recent edit would leave on the server."""
        if not self.edits:
            return []
        edit = self.edits[-1]
        indexed = sorted(
            (int(k.split("[")[1].split("]")[0]), v)
            for k, v in edit.items()
            # 'genre[].tag.tag-' is the removal param, not a positional tag.
            if "].tag.tag" in k and not k.endswith("-")
        )
        return [v for _, v in indexed]

    @property
    def last_removed(self) -> list[str]:
        """Tags the most recent edit explicitly removed."""
        if not self.edits:
            return []
        raw = next((v for k, v in self.edits[-1].items() if k.endswith(".tag.tag-")), "")
        from urllib.parse import unquote
        return [unquote(t) for t in raw.split(",")] if raw else []


class FakeTag:
    def __init__(self, tag: str) -> None:
        self.tag = tag


@pytest.fixture
def store(tmp_path) -> Store:
    with Store(tmp_path / "state.db") as s:
        yield s


@pytest.fixture
def item() -> MediaItem:
    handle = FakePlexItem(rating_key=42, title="Cowboy Bebop", year=1998,
                          genres=("Animation", "Comedy"))
    return MediaItem(
        rating_key=42,
        title="Cowboy Bebop",
        year=1998,
        guids=[ExternalId("mal", "1")],
        current_genres=["Animation", "Comedy"],
        current_collections=[],
        handle=handle,
    )


@pytest.fixture
def config_file(tmp_path):
    """A small v2 config on disk, shared by the API test modules."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "version": 2,
        "defaults": {"anime": {"ignore": ["Kids"]}},
        "libraries": [
            {"library": "Animes", "type": "anime", "useGenres": True, "clearGenres": True},
            {"library": "Films", "type": "standard-movie", "enabled": False},
        ],
    }))
    return path
