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


RunStatus = Literal["running", "interrupted", "ok", "partial", "failed", "undone"]


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
