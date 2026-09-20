"""Editing the config: validation, comment preservation, atomic writes."""

from __future__ import annotations

import json
import os

from plex_auto_genres.config import (
    AppConfig,
    editable_document,
    etag_of,
    load_config,
    merge_preserving_comments,
    validate_document,
    write_config,
)


# -- validate ---------------------------------------------------------------


def test_validate_reports_field_locations_in_file_key_names():
    config, errors = validate_document({
        "version": 2,
        "libraries": [{"library": "X", "type": "anime", "useKeywords": True}],
    })
    assert config is None
    assert errors and errors[0]["loc"] == ["libraries", 0]
    assert errors[0]["msg"].startswith("useKeywords only applies")   # no "Value error, " prefix


def test_validate_accepts_a_good_document_and_ignores_env_only_keys():
    config, errors = validate_document({
        "version": 2,
        "libraries": [{"library": "X", "type": "anime"}],
        "plex": {"token": "should be ignored"},
        "//": "a comment",
    })
    assert errors == []
    assert config is not None and config.find("X") is not None
    assert config.plex.token != "should be ignored"


def test_unknown_field_is_an_error_with_a_location():
    _, errors = validate_document({
        "version": 2, "libraries": [{"library": "X", "type": "anime", "typo": 1}],
    })
    assert any(e["loc"] == ["libraries", 0, "typo"] for e in errors)


def test_editable_document_round_trips_through_validation():
    config = AppConfig.model_validate({
        "version": 2,
        "defaults": {"anime": {"ignore": ["Kids"], "replace": {"cars": "Racing"}}},
        "libraries": [{"library": "A", "type": "anime", "useGenres": True,
                       "overrides": {"maxGenres": 5}}],
    })
    doc = editable_document(config)
    assert doc["libraries"][0]["useGenres"] is True          # file key names
    assert doc["defaults"]["anime"]["replace"] == {"cars": "Racing"}
    again, errors = validate_document(doc)
    assert errors == [] and again.rules_for(again.find("A")).max_genres == 5


# -- merge ------------------------------------------------------------------


def test_comments_survive_a_rewrite_at_their_position():
    old = {"//": "top note", "version": 2, "defaults": {"//anime": "why", "anime": {"ignore": ["Kids"]}}}
    new = {"version": 2, "defaults": {"anime": {"ignore": ["Kids", "Erotica"]}}, "libraries": []}
    merged = merge_preserving_comments(old, new)
    assert list(merged) == ["//", "version", "defaults", "libraries"]
    assert merged["defaults"]["//anime"] == "why"
    assert merged["defaults"]["anime"]["ignore"] == ["Kids", "Erotica"]


def test_library_comments_follow_the_library_not_the_index():
    old = {"libraries": [
        {"//": "first note", "library": "A", "type": "anime"},
        {"//": "second note", "library": "B", "type": "anime"},
    ]}
    new = {"libraries": [
        {"library": "B", "type": "anime"},            # A was removed, B moved up
        {"library": "C", "type": "standard-tv"},      # new
    ]}
    merged = merge_preserving_comments(old, new)
    assert merged["libraries"][0]["//"] == "second note"
    assert "//" not in merged["libraries"][1]


def test_removed_keys_stay_removed():
    old = {"libraries": [{"library": "A", "type": "anime", "overrides": {"ignore": ["x"]}}]}
    new = {"libraries": [{"library": "A", "type": "anime"}]}
    assert "overrides" not in merge_preserving_comments(old, new)["libraries"][0]


# -- write ------------------------------------------------------------------


def test_write_is_atomic_keeps_a_backup_and_returns_a_matching_etag(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"//": "keep me", "version": 2, "libraries": []}))
    before = path.read_text()

    etag, backup = write_config(path, {"version": 2, "libraries": [{"library": "A", "type": "anime"}]})

    text = path.read_text()
    assert etag == etag_of(text)
    assert backup == tmp_path / "config.json.bak" and backup.read_text() == before
    written = json.loads(text)
    assert written["//"] == "keep me"
    assert written["libraries"][0]["library"] == "A"
    assert not [p for p in os.listdir(tmp_path) if p.endswith(".tmp")], "no temp file left behind"


def test_write_migrates_a_v1_file_and_backs_up_the_original(tmp_path):
    path = tmp_path / "config.json"
    v1 = {"general_settings": {"genres": {}}, "automation_settings": {"run": []}}
    path.write_text(json.dumps(v1))

    write_config(path, {"version": 2, "libraries": [{"library": "A", "type": "anime"}]})

    written = json.loads(path.read_text())
    assert "general_settings" not in written and written["version"] == 2
    assert json.loads((tmp_path / "config.json.bak").read_text()) == v1
    assert load_config(path, use_env=False).find("A") is not None


def test_write_never_stores_env_only_sections(tmp_path):
    path = tmp_path / "config.json"
    write_config(path, {"version": 2, "libraries": [], "plex": {"token": "nope"},
                        "providers": {"tmdb_api_key": "nope"}})
    written = json.loads(path.read_text())
    assert "plex" not in written and "providers" not in written


def test_write_to_a_new_path_has_no_backup(tmp_path):
    etag, backup = write_config(tmp_path / "fresh" / "config.json", {"version": 2, "libraries": []})
    assert backup is None and etag


def test_write_through_a_symlink_keeps_the_link(tmp_path):
    real = tmp_path / "real" / "config.json"
    real.parent.mkdir()
    real.write_text(json.dumps({"version": 2, "libraries": []}))
    link = tmp_path / "config.json"
    link.symlink_to(real)

    _, backup = write_config(link, {"version": 2, "defaults": {}, "libraries": [
        {"library": "A", "type": "anime"}]})

    assert link.is_symlink(), "the link itself was not replaced by a regular file"
    assert json.loads(real.read_text())["libraries"][0]["library"] == "A"
    assert backup == real.with_name("config.json.bak") and backup.is_file()


def test_write_keeps_the_file_mode(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"version": 2, "libraries": []}))
    path.chmod(0o664)
    write_config(path, {"version": 2, "defaults": {}, "libraries": []})
    assert path.stat().st_mode & 0o777 == 0o664
