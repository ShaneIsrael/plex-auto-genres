"""Configuration, migration and the per-library fingerprint (bug #6)."""

from __future__ import annotations

import json

import pytest

from plex_auto_genres.config import AppConfig, GenreRules, load_config, migrate_v1
from plex_auto_genres.errors import ConfigError
from plex_auto_genres.models import MediaType

V1 = {
    "general_settings": {"genres": {
        "standard-tv": {"ignore": [], "replace": {"sci-fi": "science fiction"},
                        "sortedPrefix": "", "sortedCollections": ["action", "sci-fi"]},
        "anime": {"ignore": ["kids"], "replace": {"Shoujo Ai": "shoujo"},
                  "sortedPrefix": "*", "sortedCollections": ["action"]},
    }},
    "automation_settings": {"run": [
        {"library": "Shows A", "type": "standard-tv", "setPosters": False,
         "sortCollections": False, "rateAnime": False, "createRatingCollections": False,
         "useKeywords": True, "useGenres": True, "clearGenres": True},
        {"library": "Shows B", "type": "standard-tv", "setPosters": False,
         "sortCollections": False, "rateAnime": False, "createRatingCollections": False,
         "useKeywords": False, "useGenres": False, "clearGenres": False},
    ]},
}


def build(raw: dict) -> AppConfig:
    return AppConfig.model_validate({**migrate_v1(raw), "plex": {}, "providers": {}})


def test_v1_config_migrates():
    config = build(V1)
    assert config.version == 2
    assert [r.library for r in config.libraries] == ["Shows A", "Shows B"]
    assert config.defaults[MediaType.STANDARD_TV].replace == {"sci-fi": "science fiction"}


def test_two_libraries_of_one_type_get_distinct_fingerprints():
    """Bug #6: v1 keyed the cache on media type, so these shared -- and
    poisoned -- a single progress file."""
    config = build(V1)
    a, b = config.find("Shows A"), config.find("Shows B")
    assert config.fingerprint(a) != config.fingerprint(b)


def test_fingerprint_changes_when_a_setting_changes():
    config = build(V1)
    run = config.find("Shows A")
    before = config.fingerprint(run)
    flipped = run.model_copy(update={"use_keywords": False})
    assert config.fingerprint(flipped) != before


def test_fingerprint_is_stable_across_calls():
    config = build(V1)
    run = config.find("Shows A")
    assert config.fingerprint(run) == config.fingerprint(run)


def test_replace_keys_are_lowercased():
    """v1 raised a bare KeyError when a replace key was not already lowercase."""
    config = build(V1)
    rules = config.defaults[MediaType.ANIME]
    assert "shoujo ai" in rules.replace
    assert rules.apply(["Shoujo Ai"]) == ["shoujo"]


def test_per_library_overrides_layer_over_type_defaults():
    config = AppConfig.model_validate({
        "version": 2,
        "defaults": {"standard-tv": {"ignore": ["Reality"], "replace": {"sci-fi": "science fiction"}}},
        "libraries": [{
            "library": "Kids TV", "type": "standard-tv", "useGenres": True,
            "overrides": {"ignore": ["Horror"], "replace": {"animation": "Cartoon"}},
        }],
    })
    rules = config.rules_for(config.find("Kids TV"))
    assert set(rules.ignore) == {"Reality", "Horror"}
    assert rules.apply(["Sci-Fi", "Animation", "Horror", "Reality"]) == [
        "science fiction", "Cartoon"
    ]


def test_genre_rules_cap_the_list():
    rules = GenreRules(maxGenres=2)
    assert rules.apply(["a", "b", "c", "d"]) == ["a", "b"]


def test_genre_rules_drop_a_replacement_that_maps_onto_an_ignored_name():
    rules = GenreRules(ignore=["Cartoon"], replace={"animation": "Cartoon"})
    assert rules.apply(["Animation", "Drama"]) == ["Drama"]


def test_duplicate_libraries_are_rejected():
    with pytest.raises(Exception):
        AppConfig.model_validate({"version": 2, "libraries": [
            {"library": "X", "type": "anime"}, {"library": "x", "type": "anime"},
        ]})


def test_clear_genres_without_use_genres_is_rejected_in_v2():
    """v1 accepted this and silently did nothing (see writer.py's docstring)."""
    with pytest.raises(Exception, match="clearGenres requires useGenres"):
        AppConfig.model_validate({"version": 2, "libraries": [
            {"library": "X", "type": "anime", "useGenres": False, "clearGenres": True},
        ]})


def test_migration_drops_the_impossible_v1_combination():
    raw = {"automation_settings": {"run": [
        {"library": "X", "type": "anime", "useGenres": False, "clearGenres": True,
         "useKeywords": True, "setPosters": False, "sortCollections": False,
         "rateAnime": False, "createRatingCollections": False},
    ]}}
    config = build(raw)
    run = config.find("X")
    assert run.clear_genres is False   # was a no-op in v1
    assert run.use_keywords is False   # anime has no TMDB keywords


def test_keywords_on_an_anime_library_is_rejected_in_v2():
    with pytest.raises(Exception, match="useKeywords only applies"):
        AppConfig.model_validate({"version": 2, "libraries": [
            {"library": "X", "type": "anime", "useKeywords": True},
        ]})


def test_default_providers_per_type():
    config = build(V1)
    assert config.find("Shows A").resolved_providers == ("tmdb",)
    anime = AppConfig.model_validate({"version": 2, "libraries": [
        {"library": "A", "type": "anime"}]})
    assert anime.find("A").resolved_providers == ("jikan",)


def test_missing_config_file_gives_an_actionable_error(tmp_path):
    with pytest.raises(ConfigError, match="No configuration file"):
        load_config(tmp_path / "nope.json")


def test_malformed_json_is_reported_clearly(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json")
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config(path)


def test_unknown_field_is_rejected_rather_than_ignored(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"version": 2, "libraries": [
        {"library": "X", "type": "anime", "typo_field": True}]}))
    with pytest.raises(ConfigError, match="typo_field"):
        load_config(path, use_env=False)


def test_json_schema_is_generated_for_web_forms():
    from plex_auto_genres.config import config_json_schema

    schema = config_json_schema()
    assert "libraries" in schema["properties"]
    # Descriptions are what a generated form shows as help text.
    defs = schema["$defs"]["LibraryRun"]["properties"]
    assert defs["useGenres"]["description"]


def test_comment_keys_are_stripped(tmp_path):
    """The shipped example documents itself with '//' keys; strict validation
    would otherwise reject a straight copy of it."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "//": "a note", "version": 2,
        "libraries": [{"//why": "explanation", "library": "X", "type": "anime"}],
    }))
    config = load_config(path, use_env=False)
    assert config.find("X") is not None


def test_the_shipped_example_is_valid():
    """Guards against the example and the models drifting apart."""
    from pathlib import Path

    config = load_config(Path(__file__).parent.parent / "config" / "config.json.example",
                         use_env=False)
    assert [r.library for r in config.libraries] == ["Anime Shows", "TV Shows", "Movies"]
    movies = config.find("Movies")
    assert config.rules_for(movies).max_genres == 8


def test_taxonomy_check_reports_each_stale_name_once():
    from plex_auto_genres.taxonomy import check_names

    live = ["Action", "Racing", "Suspense"]
    stale = check_names(["cars", "Cars", "action", "thriller"], live)
    assert stale == [("cars", "Racing"), ("thriller", "Suspense")]


def test_blank_library_names_are_rejected():
    for bad in ("", "   "):
        with pytest.raises(Exception, match="blank|at least 1"):
            AppConfig.model_validate({"version": 2, "libraries": [{"library": bad, "type": "anime"}]})


def test_library_names_are_stripped():
    config = AppConfig.model_validate({"version": 2, "libraries": [{"library": "  Animes ", "type": "anime"}]})
    assert config.libraries[0].library == "Animes"


def test_migration_skips_malformed_v1_entries():
    """v1 ran one subprocess per entry, so a bad entry only failed itself."""
    raw = {"automation_settings": {"run": [
        {"library": "A", "type": "anime"},
        {"library": "Doc", "type": "some other type"},   # the v1 example ships one of these
        {"type": "anime"},
    ]}}
    assert [r["library"] for r in migrate_v1(raw)["libraries"]] == ["A"]


def test_missing_config_is_fine_when_told_so(tmp_path, monkeypatch):
    monkeypatch.setenv("PLEX_BASE_URL", "http://plex:32400")
    monkeypatch.setenv("PLEX_TOKEN", "t")
    config = load_config(tmp_path / "absent.json", missing_ok=True)
    assert config.libraries == [] and config.plex.base_url == "http://plex:32400"
