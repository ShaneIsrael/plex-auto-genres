"""Response models. Request models arrive with the write endpoints in phase 2."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..config import GenreRules, LibraryRun
from ..models import MediaType


class PlexStatus(BaseModel):
    """Whether the last connection attempt reached the server."""

    reachable: bool
    server_name: str | None = None
    version: str | None = None
    error: str | None = None
    checked_at: float | None = None


class SchedulerStatus(BaseModel):
    cron: str
    next_fire_at: float | None = None


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
    version: int
    defaults: dict[MediaType, GenreRules]
    libraries: list[LibraryRun]
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
