"""Configuration models and loading.

Everything the user can tune lives here as pydantic models. That buys three
things at once:

* validation with actionable error messages instead of ``KeyError`` at runtime;
* a machine-readable JSON Schema (:func:`config_json_schema`) that a future web
  UI can render as forms without duplicating the field list;
* a stable *fingerprint* per library run, so the cache knows when a settings
  change invalidates previously processed media.

Two on-disk formats are accepted. ``version: 2`` is the native one. Anything
else is treated as the legacy v1 layout (``general_settings`` +
``automation_settings``) and migrated in memory, so existing installs keep
working untouched.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .errors import ConfigError
from .models import MediaType

CONFIG_VERSION = 2

#: Which providers may serve which library type, in default preference order.
DEFAULT_PROVIDERS: dict[MediaType, tuple[str, ...]] = {
    MediaType.ANIME: ("jikan",),
    MediaType.STANDARD_TV: ("tmdb",),
    MediaType.STANDARD_MOVIE: ("tmdb",),
}

ProviderName = Literal["jikan", "anilist", "tmdb"]


class GenreRules(BaseModel):
    """Post-processing applied to the raw genre list a provider returned."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    ignore: list[str] = Field(
        default_factory=list,
        description="Genres to drop entirely. Case-insensitive.",
    )
    replace: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Rename map applied after 'ignore'. Keys are matched "
            "case-insensitively; the value is written verbatim."
        ),
    )
    sorted_prefix: str = Field(
        default="",
        alias="sortedPrefix",
        description="Character prepended to a collection's sort title by --sort.",
    )
    sorted_collections: list[str] = Field(
        default_factory=list,
        alias="sortedCollections",
        description="Collections that --sort should prefix.",
    )
    max_genres: int | None = Field(
        default=None,
        alias="maxGenres",
        ge=1,
        description=(
            "Keep at most N genres per item, in provider order. Mainly useful "
            "with useKeywords, where TMDB can return 50+ keywords."
        ),
    )

    @field_validator("replace")
    @classmethod
    def _normalise_replace_keys(cls, value: dict[str, str]) -> dict[str, str]:
        """Lower-case the lookup keys so config casing stops mattering.

        v1 raised a bare ``KeyError`` when a key was not already lower-case.
        """
        return {k.strip().lower(): v for k, v in value.items()}

    @field_validator("ignore")
    @classmethod
    def _strip_ignore(cls, value: list[str]) -> list[str]:
        return [v.strip() for v in value if v and v.strip()]

    def merge(self, override: "GenreRules | None") -> "GenreRules":
        """Layer a per-library override on top of these type-level defaults."""
        if override is None:
            return self
        return GenreRules(
            ignore=[*self.ignore, *override.ignore],
            replace={**self.replace, **override.replace},
            sortedPrefix=override.sorted_prefix or self.sorted_prefix,
            sortedCollections=override.sorted_collections or self.sorted_collections,
            maxGenres=override.max_genres if override.max_genres is not None else self.max_genres,
        )

    def apply(self, genres: list[str]) -> list[str]:
        """Run ignore -> replace -> dedupe -> cap over a raw provider list."""
        ignore = {g.lower() for g in self.ignore}
        out: list[str] = []
        seen: set[str] = set()
        for genre in genres:
            key = genre.strip().lower()
            if not key or key in ignore:
                continue
            resolved = self.replace.get(key, genre.strip())
            # Re-check ignore against the replacement so a rename cannot
            # resurrect a genre the user asked to drop.
            if resolved.lower() in ignore:
                continue
            dedupe_key = resolved.lower()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            out.append(resolved)
        if self.max_genres is not None:
            out = out[: self.max_genres]
        return out


class LibraryRun(BaseModel):
    """One library and what to do with it."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    library: str = Field(description="Exact Plex library name.")
    type: MediaType = Field(description="Which metadata taxonomy applies.")
    enabled: bool = Field(default=True, description="Skip this entry when false.")

    providers: list[ProviderName] | None = Field(
        default=None,
        description=(
            "Metadata sources, tried in order until one returns genres. "
            "Defaults to ['jikan'] for anime and ['tmdb'] otherwise."
        ),
    )

    use_genres: bool = Field(
        default=False,
        alias="useGenres",
        description="Write into Plex's genre field. When false, writes collections instead.",
    )
    use_keywords: bool = Field(
        default=False,
        alias="useKeywords",
        description="TMDB only: use keywords instead of genres. Much noisier.",
    )
    clear_genres: bool = Field(
        default=False,
        alias="clearGenres",
        description="Remove existing tags before writing the new set.",
    )

    set_posters: bool = Field(default=False, alias="setPosters")
    sort_collections: bool = Field(default=False, alias="sortCollections")
    rate_media: bool = Field(
        default=False,
        alias="rateAnime",
        description="Overwrite Plex ratings with the provider score.",
    )
    create_rating_collections: bool = Field(default=False, alias="createRatingCollections")

    overrides: GenreRules | None = Field(
        default=None,
        description="Per-library genre rules layered over the type defaults.",
    )

    @model_validator(mode="after")
    def _check_coherent(self) -> "LibraryRun":
        if self.use_keywords and self.type is MediaType.ANIME:
            raise ValueError(
                "useKeywords only applies to TMDB-backed libraries; "
                "anime libraries have no keyword concept."
            )
        if self.clear_genres and not self.use_genres:
            # v1 silently ignored this combination. Say so instead.
            raise ValueError(
                "clearGenres requires useGenres: true. To reset collections "
                "instead, use the 'undo' command."
            )
        return self

    @property
    def resolved_providers(self) -> tuple[str, ...]:
        if self.providers:
            return tuple(self.providers)
        return DEFAULT_PROVIDERS[self.type]


class PlexSettings(BaseModel):
    """Connection details. Populated from the environment, not the JSON file."""

    model_config = ConfigDict(extra="forbid")

    base_url: str | None = None
    token: str | None = None
    username: str | None = None
    password: str | None = None
    server_name: str | None = None
    collection_prefix: str = ""
    timeout_s: float = Field(default=30.0, gt=0)

    @property
    def uses_token_auth(self) -> bool:
        return bool(self.base_url and self.token)

    @property
    def uses_account_auth(self) -> bool:
        return bool(self.username and self.password and self.server_name)

    def validate_usable(self) -> None:
        if self.uses_token_auth or self.uses_account_auth:
            return
        raise ConfigError(
            "No usable Plex credentials.\n"
            "  Preferred: set PLEX_BASE_URL and PLEX_TOKEN.\n"
            "  Legacy:    set PLEX_USERNAME, PLEX_PASSWORD and PLEX_SERVER_NAME."
        )


class ProviderSettings(BaseModel):
    """API keys and tunables for the metadata sources."""

    model_config = ConfigDict(extra="forbid")

    tmdb_api_key: str | None = None
    tmdb_language: str = "en-US"
    #: Concurrent in-flight requests per provider.
    concurrency: int = Field(default=4, ge=1, le=32)
    #: Total attempts per item before it is recorded as failed.
    max_attempts: int = Field(default=3, ge=1, le=10)


class AppConfig(BaseModel):
    """The whole configuration tree."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: int = CONFIG_VERSION
    defaults: dict[MediaType, GenreRules] = Field(default_factory=dict)
    libraries: list[LibraryRun] = Field(default_factory=list)
    plex: PlexSettings = Field(default_factory=PlexSettings)
    providers: ProviderSettings = Field(default_factory=ProviderSettings)

    @model_validator(mode="after")
    def _unique_libraries(self) -> "AppConfig":
        seen: set[str] = set()
        for run in self.libraries:
            key = run.library.casefold()
            if key in seen:
                raise ValueError(f"Library {run.library!r} is listed more than once.")
            seen.add(key)
        return self

    def rules_for(self, run: LibraryRun) -> GenreRules:
        """Type defaults merged with this library's overrides."""
        base = self.defaults.get(run.type, GenreRules())
        return base.merge(run.overrides)

    def find(self, library: str) -> LibraryRun | None:
        target = library.casefold()
        return next((r for r in self.libraries if r.library.casefold() == target), None)

    def fingerprint(self, run: LibraryRun) -> str:
        """Hash of everything that affects the genre list written for a library.

        When any of it changes, cached entries for that library become stale and
        the pipeline reprocesses them. This is what v1 got wrong by keying the
        progress files on media *type* alone, so two libraries sharing a type
        also shared -- and poisoned -- one cache.
        """
        rules = self.rules_for(run)
        payload = {
            "library": run.library,
            "type": run.type.value,
            "providers": list(run.resolved_providers),
            "use_genres": run.use_genres,
            "use_keywords": run.use_keywords,
            "clear_genres": run.clear_genres,
            "prefix": self.plex.collection_prefix,
            "ignore": sorted(g.lower() for g in rules.ignore),
            "replace": dict(sorted(rules.replace.items())),
            "max_genres": rules.max_genres,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------
# Loading and legacy migration
# --------------------------------------------------------------------------


def _plex_from_env() -> PlexSettings:
    return PlexSettings(
        base_url=os.getenv("PLEX_BASE_URL") or None,
        token=os.getenv("PLEX_TOKEN") or None,
        username=os.getenv("PLEX_USERNAME") or None,
        password=os.getenv("PLEX_PASSWORD") or None,
        server_name=os.getenv("PLEX_SERVER_NAME") or None,
        collection_prefix=os.getenv("PLEX_COLLECTION_PREFIX", ""),
        timeout_s=float(os.getenv("PLEX_TIMEOUT", "30")),
    )


def _providers_from_env() -> ProviderSettings:
    return ProviderSettings(
        tmdb_api_key=os.getenv("TMDB_API_KEY") or None,
        tmdb_language=os.getenv("TMDB_LANGUAGE", "en-US"),
        concurrency=int(os.getenv("PAG_CONCURRENCY", "4")),
        max_attempts=int(os.getenv("PAG_MAX_ATTEMPTS", "3")),
    )


def migrate_v1(raw: dict[str, Any]) -> dict[str, Any]:
    """Translate the legacy config layout into the v2 shape.

    v1 kept genre rules under ``general_settings.genres.<type>`` and the run
    list under ``automation_settings.run``. Rules were therefore per *type*,
    which meant two libraries of the same type could not be configured
    separately. The migration keeps those rules as type-level defaults, which
    preserves existing behaviour exactly; per-library overrides are opt-in.
    """
    general = raw.get("general_settings") or {}
    defaults: dict[str, Any] = {}
    for type_name, rules in (general.get("genres") or {}).items():
        if type_name not in {m.value for m in MediaType}:
            continue
        defaults[type_name] = {
            "ignore": rules.get("ignore") or [],
            "replace": rules.get("replace") or {},
            "sortedPrefix": rules.get("sortedPrefix", "") or "",
            "sortedCollections": rules.get("sortedCollections") or [],
        }

    libraries: list[dict[str, Any]] = []
    for entry in (raw.get("automation_settings") or {}).get("run") or []:
        migrated = {
            "library": entry["library"],
            "type": entry["type"],
            "useGenres": bool(entry.get("useGenres", False)),
            "useKeywords": bool(entry.get("useKeywords", False)),
            "clearGenres": bool(entry.get("clearGenres", False)),
            "setPosters": bool(entry.get("setPosters", False)),
            "sortCollections": bool(entry.get("sortCollections", False)),
            "rateAnime": bool(entry.get("rateAnime", False)),
            "createRatingCollections": bool(entry.get("createRatingCollections", False)),
        }
        # v1 accepted clearGenres without useGenres and quietly did nothing
        # (see plex.py:77). Drop the flag rather than fail the migration.
        if migrated["clearGenres"] and not migrated["useGenres"]:
            migrated["clearGenres"] = False
        # v1 ran keyword lookups against anime libraries too; they were ignored.
        if migrated["useKeywords"] and migrated["type"] == MediaType.ANIME.value:
            migrated["useKeywords"] = False
        libraries.append(migrated)

    return {"version": CONFIG_VERSION, "defaults": defaults, "libraries": libraries}


def strip_comments(value: Any) -> Any:
    """Drop ``"//"``-prefixed keys, the JSON-with-comments convention.

    The shipped example documents itself with them, and validation is strict
    (``extra="forbid"``), so they have to go before the models see the data.
    """
    if isinstance(value, dict):
        return {k: strip_comments(v) for k, v in value.items() if not k.startswith("//")}
    if isinstance(value, list):
        return [strip_comments(v) for v in value]
    return value


def load_config(path: str | Path = "config/config.json", *, use_env: bool = True) -> AppConfig:
    """Read, migrate if needed, and validate the configuration file."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(
            f"No configuration file at {path.resolve()}.\n"
            f"Copy config/config.json.example to {path} and edit it."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a JSON object at the top level.")
    raw = strip_comments(raw)

    if int(raw.get("version", 1)) < CONFIG_VERSION:
        raw = migrate_v1(raw)

    if use_env:
        raw = {
            **raw,
            "plex": _plex_from_env().model_dump(),
            "providers": _providers_from_env().model_dump(),
        }

    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(path, exc)) from exc


def _format_validation_error(path: Path, exc: ValidationError) -> str:
    lines = [f"{path} is invalid:"]
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        lines.append(f"  - {loc}: {err['msg']}")
    return "\n".join(lines)


def config_json_schema() -> dict[str, Any]:
    """JSON Schema for the config file.

    Exposed through ``plex-auto-genres schema`` so a web UI can build its forms
    from the same source of truth the CLI validates against.
    """
    return AppConfig.model_json_schema(by_alias=True)
