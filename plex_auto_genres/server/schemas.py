"""Response models. Request models arrive with the write endpoints in phase 2."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..config import GenreRules, LibraryRun, ScheduleSettings
from ..models import MediaType


class PlexStatus(BaseModel):
    """Whether the last connection attempt reached the server."""

    reachable: bool
    server_name: str | None = None
    version: str | None = None
    error: str | None = None
    checked_at: float | None = None


class SchedulerStatus(BaseModel):
    """What the in-process scheduler is set to do right now."""

    cron: str | None = None
    enabled: bool = True
    #: ``config`` (the file's schedule block), ``env`` (``--cron`` / ``CRON_SCHEDULE``), ``none``.
    source: Literal["config", "env", "none"] = "none"
    next_fire_at: float | None = None


class CronPreview(BaseModel):
    """Whether an expression parses, and when it would fire next."""

    ok: bool
    error: str | None = None
    next_fire_at: list[float] = Field(default_factory=list)


class Health(BaseModel):
    """Liveness, versions and the operator's three glance-values."""

    status: Literal["ok", "degraded"]
    version: str
    uptime_s: float
    started_at: float
    config_path: str
    db_path: str
    plex: PlexStatus
    scheduler: SchedulerStatus | None = None


class Secrets(BaseModel):
    """Which secrets are set. Never their values."""

    plex_token: bool
    plex_password: bool
    tmdb_api_key: bool
    plex_base_url: str | None
    plex_server_name: str | None
    collection_prefix: str


class ConfigView(BaseModel):
    """The config as the UI sees it: everything but secret values."""

    model_config = ConfigDict(populate_by_name=True)

    path: str
    #: Content hash of the file; send it back as If-Match when saving.
    etag: str | None = None
    version: int
    defaults: dict[MediaType, GenreRules]
    libraries: list[LibraryRun]
    schedule: ScheduleSettings
    secrets: Secrets
    providers: dict[str, Any] = Field(description="Non-secret provider settings.")


class PlexSection(BaseModel):
    """What Plex reports about one library section."""

    key: int
    section_type: str
    item_count: int | None = None
    agent: str | None = None


RunStatus = Literal["running", "interrupted", "ok", "partial", "failed", "undone", "cancelled"]
JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]
Action = Literal["tags", "posters", "sort", "ratings", "rating-collections"]


class RunView(BaseModel):
    """One row of run history with a derived status."""

    run_id: str
    library: str
    action: str
    dry_run: bool
    status: RunStatus
    started_at: float
    finished_at: float | None = None
    undone_at: float | None = None
    report: dict[str, Any] | None = None
    #: The job that produced this run, while that job is still remembered.
    job_id: str | None = None


class LibraryView(BaseModel):
    """A configured library and/or a Plex section, merged."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    configured: bool
    enabled: bool | None = None
    type: MediaType | None = None
    providers: list[str] = Field(default_factory=list)
    use_genres: bool | None = Field(default=None, alias="useGenres")
    clear_genres: bool | None = Field(default=None, alias="clearGenres")
    plex: PlexSection | None = None
    stats: dict[str, int] = Field(default_factory=dict)
    last_run: RunView | None = None


class BindingView(BaseModel):
    """A manual provider binding for one item."""

    library: str
    media_key: str
    provider: str
    provider_id: str
    note: str | None = None
    created_at: float


class Problem(BaseModel):
    """RFC 9457-shaped error body."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None


class JobProgress(BaseModel):
    """Where a job's current action stands."""

    action: str | None = None
    run_id: str | None = None
    total: int = 0
    pending: int = 0
    done: int = 0
    written: int = 0
    unchanged: int = 0
    failed: int = 0
    title: str | None = None


class JobView(BaseModel):
    """A queued, running or recently finished job."""

    job_id: str
    library: str
    status: JobStatus
    source: str
    dry_run: bool
    force: bool
    only: list[str]
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    run_ids: list[str]
    progress: JobProgress
    reports: list[dict[str, Any]]


class RunOptions(BaseModel):
    """Options for a manually started job. Mirrors the CLI flags."""

    model_config = ConfigDict(extra="forbid")

    dry_run: bool = Field(default=False, description="Report what would change; write nothing.")
    force: bool = Field(default=False, description="Ignore the cache and reprocess every item.")
    only: list[Action] = Field(default_factory=list, description="Run only these actions.")


class StartJobs(RunOptions):
    """Start jobs for several libraries at once."""

    libraries: list[str] | None = Field(
        default=None, description="Library names, or null for every enabled library."
    )


class UndoResult(BaseModel):
    """Outcome of restoring a run's previous tags."""

    run_id: str
    restored: int
    skipped: int


class ConfigDocument(BaseModel):
    """The editable part of the config, in the file's key names.

    Validation happens against the full model on the server; this shape only
    documents the envelope. Unknown keys (including ``//`` comments) pass
    through here and are rejected or stripped there.
    """

    model_config = ConfigDict(extra="allow")

    version: int = 2
    defaults: dict[str, Any] = Field(default_factory=dict)
    libraries: list[dict[str, Any]] = Field(default_factory=list)
    schedule: dict[str, Any] = Field(default_factory=dict)


class ValidationIssue(BaseModel):
    """One problem with a submitted document."""

    loc: list[str | int]
    msg: str
    type: str


class ValidationResult(BaseModel):
    """Outcome of a dry validation."""

    ok: bool
    errors: list[ValidationIssue] = Field(default_factory=list)


class SaveResult(BaseModel):
    """Outcome of writing the config."""

    ok: bool
    etag: str | None = None
    path: str
    backup: str | None = None
    errors: list[ValidationIssue] = Field(default_factory=list)


MatchSource = Literal["binding", "guid", "search"]


class ItemState(BaseModel):
    """What the cache remembers about one item."""

    status: Literal["ok", "failed"]
    provider: str | None = None
    provider_id: str | None = None
    genres: list[str] = Field(default_factory=list)
    attempts: int = 0
    last_error: str | None = None
    updated_at: float


class ItemView(BaseModel):
    """One library item, joined with how it matched and what was written."""

    rating_key: int
    media_key: str
    title: str
    year: int | None = None
    thumb: str | None = Field(default=None, description="Proxied poster path, or null.")
    guids: list[str] = Field(default_factory=list)
    match: MatchSource
    binding: BindingView | None = None
    state: ItemState | None = None
    current_genres: list[str] = Field(default_factory=list)
    current_collections: list[str] = Field(default_factory=list)


class ItemsPage(BaseModel):
    """A page of a library's items."""

    library: str
    total: int
    page: int
    size: int
    counts: dict[str, int] = Field(
        default_factory=dict,
        description="all / ok / failed / unprocessed / bound, over the whole library.",
    )
    items: list[ItemView]


class CandidateView(BaseModel):
    """A provider's suggestion for a title."""

    provider: str
    provider_id: str
    title: str
    year: int | None = None
    url: str | None = None
    image: str | None = None
    synopsis: str | None = None
    score: float | None = None
    genres: list[str] = Field(default_factory=list)


class BindingIn(BaseModel):
    """Pin an item to a provider id."""

    model_config = ConfigDict(extra="forbid")

    library: str = Field(min_length=1)
    media_key: str = Field(min_length=1)
    provider: Literal["tmdb", "mal", "anilist", "anidb", "tvdb", "imdb"]
    provider_id: str = Field(min_length=1)
    note: str | None = Field(default=None, max_length=500)
