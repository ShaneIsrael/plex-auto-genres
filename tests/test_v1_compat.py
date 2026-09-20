"""The v1 entry point still has to work: people have cron jobs pointing at it."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SHIM = Path(__file__).parent.parent / "plex-auto-genres.py"


def load_shim():
    spec = importlib.util.spec_from_file_location("v1_shim", SHIM)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("v1,v2", [
    (["--library", "Animes", "--type", "anime"],
     ["run", "--library", "Animes", "--type", "anime"]),
    (["--library", "Animes", "--type", "anime", "--set-posters"],
     ["run", "--library", "Animes", "--type", "anime", "--only", "posters"]),
    (["--library", "M", "--type", "standard-movie", "--sort"],
     ["run", "--library", "M", "--type", "standard-movie", "--only", "sort"]),
    (["--library", "A", "--type", "anime", "--rate-anime"],
     ["run", "--library", "A", "--type", "anime", "--only", "ratings"]),
    (["--library", "A", "--type", "anime", "--create-rating-collections"],
     ["run", "--library", "A", "--type", "anime", "--only", "rating-collections"]),
    (["--library", "A", "--type", "anime", "-y", "--no-progress"],
     ["run", "--library", "A", "--type", "anime", "-y", "--no-progress"]),
])
def test_v1_flags_are_translated(v1, v2):
    assert load_shim().translate(v1) == v2


def test_query_keeps_the_title_and_drops_the_type_value():
    """--type's value must not be mistaken for part of the title."""
    assert load_shim().translate(["--query", "Cowboy", "Bebop", "--type", "anime"]) == [
        "query", "Cowboy", "Bebop", "--type", "anime"
    ]


def test_v2_subcommands_pass_through_untouched():
    shim = load_shim()
    for argv in (["run", "--library", "A"], ["doctor"], ["undo", "abc123"], ["--help"]):
        assert shim.translate(argv) == argv


def test_flags_that_became_config_settings_are_dropped(capsys):
    """v1 had --use-genres/--clear-genres on the command line; they are now
    per-library config, so the shim warns rather than silently ignoring them."""
    shim = load_shim()
    out = shim.translate(["--library", "A", "--type", "anime", "--use-genres"])
    assert out == ["run", "--library", "A", "--type", "anime"]
    assert "--use-genres" in capsys.readouterr().err


def test_type_before_query_is_understood_too():
    """The v1 README's own ordering: --type first, then --query."""
    assert load_shim().translate(["--type", "anime", "--query", "chihayafuru"]) == [
        "query", "chihayafuru", "--type", "anime"
    ]


def test_the_default_command_keeps_the_global_options(monkeypatch, tmp_path):
    """`plex-auto-genres --config X --db Y` with no subcommand must honour both."""
    import sys

    from plex_auto_genres import cli
    from plex_auto_genres.errors import ConfigError

    seen: dict[str, str] = {}

    def fake_load(path, **_kwargs):
        seen["path"] = str(path)
        raise ConfigError("stop here")

    monkeypatch.setattr(cli, "load_config", fake_load)
    config, db = tmp_path / "custom.json", tmp_path / "custom.db"
    monkeypatch.setattr(sys, "argv", ["plex-auto-genres", "--config", str(config), "--db", str(db)])

    assert cli.main() == 2
    assert seen["path"] == str(config) and db.exists()
