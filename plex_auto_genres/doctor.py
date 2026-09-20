"""Configuration and environment checks.

Shared by ``plex-auto-genres doctor`` and the web API: the CLI renders a
:class:`DoctorReport` with colours, the API returns it as JSON.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from .config import CONFIG_VERSION, AppConfig, load_config
from .errors import ConfigError
from .models import MediaType
from .store import Store
from .taxonomy import check_names, fetch_live_genres

Level = Literal["ok", "warn", "error"]


@dataclass(slots=True)
class Check:
    """One diagnostic result."""

    id: str
    level: Level
    title: str
    detail: str | None = None
    items: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DoctorReport:
    """Every check, plus whether the config can be used at all."""

    checks: list[Check]
    config: AppConfig | None = None

    @property
    def ok(self) -> bool:
        return not any(c.level == "error" for c in self.checks)

    @property
    def warnings(self) -> int:
        return sum(1 for c in self.checks if c.level == "warn")

    @property
    def errors(self) -> int:
        return sum(1 for c in self.checks if c.level == "error")

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "checks": [asdict(c) for c in self.checks],
        }


def run_doctor(config_path: str, store: Store, *, check_taxonomy: bool = True) -> DoctorReport:
    """Run every check. Never raises: a broken config is itself a finding."""
    checks: list[Check] = []

    try:
        config = load_config(config_path)
    except ConfigError as exc:
        checks.append(Check("config", "error", "Config does not load", str(exc)))
        return DoctorReport(checks)

    checks.append(Check(
        "config", "ok", "Config parses",
        f"{len(config.libraries)} librar{'y' if len(config.libraries) == 1 else 'ies'}",
    ))
    if _is_v1_layout(config_path):
        checks.append(Check(
            "config-layout", "warn", "Config is in the v1 layout",
            "It is converted on disk at the next run/serve/schedule; the original "
            "is kept as config.json.v1.",
        ))

    try:
        config.plex.validate_usable()
        mode = "token" if config.plex.uses_token_auth else "username/password"
        checks.append(Check("plex-credentials", "ok", "Plex credentials present", mode))
    except ConfigError as exc:
        checks.append(Check("plex-credentials", "error", "Plex credentials missing", str(exc)))

    # Key off what will actually run: a disabled library needs nothing, and
    # an anime library can be pointed at TMDB just as a TV one at Jikan.
    needs_tmdb = any(
        "tmdb" in r.resolved_providers for r in config.libraries if r.enabled
    )
    if needs_tmdb and not config.providers.tmdb_api_key:
        checks.append(Check(
            "tmdb-key", "error", "TMDB_API_KEY is unset",
            "Required for standard-tv and standard-movie libraries.",
        ))
    elif needs_tmdb:
        checks.append(Check("tmdb-key", "ok", "TMDB API key present"))

    if not config.libraries:
        checks.append(Check("libraries", "warn", "No libraries configured"))
    elif not any(r.enabled for r in config.libraries):
        checks.append(Check("libraries", "warn", "Every library is disabled"))

    if check_taxonomy:
        checks.append(_taxonomy_check(config, store))

    for run in config.libraries:
        defaults = config.defaults.get(run.type)
        capped = (run.overrides is not None and run.overrides.max_genres is not None) or (
            defaults is not None and defaults.max_genres is not None
        )
        if run.use_keywords and not capped:
            checks.append(Check(
                f"keywords-uncapped:{run.library}", "warn",
                f"{run.library}: useKeywords without maxGenres",
                "TMDB can return 50+ keywords per title. Set overrides.maxGenres.",
            ))

    return DoctorReport(checks, config)


def _is_v1_layout(config_path: str) -> bool:
    try:
        raw = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(raw, dict) and int(raw.get("version", 1)) < CONFIG_VERSION


def _taxonomy_check(config: AppConfig, store: Store) -> Check:
    anime_rules = config.defaults.get(MediaType.ANIME)
    if anime_rules is None:
        return Check("mal-taxonomy", "ok", "No anime genre rules to check")

    live = fetch_live_genres(store)
    if live is None:
        return Check(
            "mal-taxonomy", "warn", "Could not reach MyAnimeList",
            "Genre names were not verified against the live taxonomy.",
        )

    names = [*anime_rules.sorted_collections, *anime_rules.ignore, *anime_rules.replace.keys()]
    stale = check_names(names, live)
    if not stale:
        return Check("mal-taxonomy", "ok", "Anime genre names match the current MAL taxonomy")

    return Check(
        "mal-taxonomy", "warn",
        f"{len(stale)} anime genre name{'s' if len(stale) != 1 else ''} MAL no longer uses",
        "These entries never match anything. Rename or remove them.",
        items=[f"{name} -> {repl}" if repl else f"{name} (retired)" for name, repl in stale],
    )
