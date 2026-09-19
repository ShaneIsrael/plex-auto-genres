"""Domain types shared by the providers, the Plex layer and the pipeline.

These are deliberately plain dataclasses rather than pydantic models: they are
internal wire types, not user-facing configuration, and they are created in hot
loops. The pydantic models live in :mod:`plex_auto_genres.config`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class MediaType(str, Enum):
    """The three library flavours the tool knows how to process."""

    ANIME = "anime"
    STANDARD_TV = "standard-tv"
    STANDARD_MOVIE = "standard-movie"

    @property
    def is_movie(self) -> bool:
        return self is MediaType.STANDARD_MOVIE

    @property
    def is_anime(self) -> bool:
        return self is MediaType.ANIME


class TagField(str, Enum):
    """Which Plex tag field a run writes into."""

    GENRE = "genre"
    COLLECTION = "collection"


#: Provider id schemes we can read straight out of a Plex GUID, in the order we
#: prefer them. ``mal`` and ``anilist`` are exact anime matches; ``anidb`` needs
#: the offline mapping table; the rest are for standard libraries.
KNOWN_GUID_SCHEMES = ("mal", "anilist", "anidb", "tmdb", "tvdb", "imdb")


@dataclass(frozen=True, slots=True)
class ExternalId:
    """An id from a metadata provider, e.g. ``tmdb://1234``."""

    scheme: str
    value: str

    def __str__(self) -> str:
        return f"{self.scheme}://{self.value}"

    @classmethod
    def parse(cls, raw: str) -> "ExternalId | None":
        """Parse a Plex GUID string. Returns ``None`` for unknown shapes."""
        if "://" not in raw:
            return None
        scheme, _, value = raw.partition("://")
        scheme = scheme.strip().lower()
        # Plex appends things like '?lang=en' and HAMA uses 'anidb://1234/5'.
        value = value.split("?", 1)[0].split("/", 1)[0].strip()
        if not scheme or not value:
            return None
        return cls(scheme, value)


@dataclass(slots=True)
class MediaItem:
    """A single Plex library entry, flattened into what the pipeline needs.

    ``plexapi`` objects are lazily-reloading proxies; snapshotting the fields we
    care about up front keeps the hot loop free of surprise HTTP round trips.
    """

    rating_key: int
    title: str
    year: int | None
    guids: list[ExternalId] = field(default_factory=list)
    current_genres: list[str] = field(default_factory=list)
    current_collections: list[str] = field(default_factory=list)
    #: Plex poster path, e.g. ``/library/metadata/123/thumb/456``.
    thumb: str | None = None
    #: The live plexapi object, kept so the writer can edit it.
    handle: object | None = None

    @property
    def identifier(self) -> str:
        """Stable, human-readable key. Matches the v1 progress-file format."""
        return f"{self.title} ({self.year})" if self.year else self.title

    def find_id(self, scheme: str) -> ExternalId | None:
        return next((g for g in self.guids if g.scheme == scheme), None)

    def current_tags(self, field_: TagField) -> list[str]:
        if field_ is TagField.GENRE:
            return self.current_genres
        return self.current_collections


@dataclass(slots=True)
class ProviderResult:
    """Normalised metadata returned by any provider."""

    provider: str
    provider_id: str
    title: str
    genres: list[str] = field(default_factory=list)
    #: 0-10 scale, matching what Plex's ``rate()`` expects.
    score: float | None = None
    url: str | None = None


@dataclass(slots=True)
class Candidate:
    """One possible match a provider offers for a title, for a human to pick."""

    provider: str
    provider_id: str
    title: str
    year: int | None = None
    url: str | None = None
    image: str | None = None
    synopsis: str | None = None
    score: float | None = None
    genres: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "provider_id": self.provider_id,
            "title": self.title,
            "year": self.year,
            "url": self.url,
            "image": self.image,
            "synopsis": self.synopsis,
            "score": self.score,
            "genres": list(self.genres),
        }


@dataclass(slots=True)
class ItemOutcome:
    """What happened to one media item during a run."""

    item: MediaItem
    status: str  # "written" | "skipped" | "failed" | "unchanged"
    genres: list[str] = field(default_factory=list)
    provider: str | None = None
    provider_id: str | None = None
    error: str | None = None
    retryable: bool = True


@dataclass(slots=True)
class RunReport:
    """Aggregate result of processing one library."""

    run_id: str
    library: str
    action: str
    dry_run: bool = False
    written: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0
    plex_requests: int = 0
    provider_requests: int = 0
    duration_s: float = 0.0
    failures: list[tuple[str, str]] = field(default_factory=list)
    #: True when the run was cancelled before every item was processed.
    cancelled: bool = False

    @property
    def total(self) -> int:
        return self.written + self.unchanged + self.skipped + self.failed

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "library": self.library,
            "action": self.action,
            "dry_run": self.dry_run,
            "written": self.written,
            "unchanged": self.unchanged,
            "skipped": self.skipped,
            "failed": self.failed,
            "total": self.total,
            "plex_requests": self.plex_requests,
            "provider_requests": self.provider_requests,
            "duration_s": round(self.duration_s, 2),
            "failures": self.failures[:50],
            "cancelled": self.cancelled,
        }
