"""Upgrading a v1 install: the config file, the progress files, the story in the log."""

from __future__ import annotations

import json
import logging

from pathlib import Path

from plex_auto_genres.config import load_config
from plex_auto_genres.doctor import run_doctor
from plex_auto_genres.migration import migrate_install
from plex_auto_genres.store import Store

V1 = {
    "general_settings": {"genres": {
        "anime": {"ignore": ["kids"], "replace": {"cars": "Racing"},
                  "sortedPrefix": "*", "sortedCollections": ["action"]},
    }},
    "automation_settings": {"run": [
        {"library": "Animes", "type": "anime", "useGenres": True, "clearGenres": True,
         "setPosters": False, "sortCollections": False, "rateAnime": True,
         "createRatingCollections": False, "useKeywords": False},
        {"library": "Anime Films", "type": "anime", "useGenres": False, "clearGenres": False,
         "setPosters": False, "sortCollections": True, "rateAnime": False,
         "createRatingCollections": False, "useKeywords": False},
        {"library": "some other library", "type": "some other type"},
    ]},
}


def v1_install(tmp_path):
    config = tmp_path / "config" / "config.json"
    config.parent.mkdir()
    config.write_text(json.dumps(V1, indent=2))
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "plex-anime-successful.txt").write_text(json.dumps(["Naruto (2002)", "Bleach (2004)"]))
    (logs / "plex-anime-failures.txt").write_text(json.dumps(["Obscure OVA (1994)"]))
    (logs / "plex-anime-ratings-progress.txt").write_text("[]")
    (logs / "plex-auto-genres-automate.log").write_text("old log")
    return config, logs


def test_a_v1_install_is_converted_backed_up_and_narrated(tmp_path, caplog):
    config, logs = v1_install(tmp_path)
    caplog.set_level(logging.INFO, logger="plex_auto_genres.migration")

    with Store(logs / "state.db") as store:
        report = migrate_install(config, logs, store)

        assert report.config_converted and report.config_backup == config.with_name("config.json.v1")
        assert json.loads(report.config_backup.read_text()) == V1, "the original is kept verbatim"
        on_disk = json.loads(config.read_text())
        assert on_disk["version"] == 2
        assert [lib["library"] for lib in on_disk["libraries"]] == ["Animes", "Anime Films"]
        assert on_disk["defaults"]["anime"]["replace"] == {"cars": "Racing"}

        assert report.imported == {"Animes": 3, "Anime Films": 3}
        assert not (logs / "plex-anime-successful.txt").exists()
        assert (logs / "plex-anime-successful.txt.imported").is_file()
        assert (logs / "plex-anime-failures.txt.imported").is_file()
        assert store.should_process("Animes", "Naruto (2002)", load_config(config).fingerprint(
            load_config(config).find("Animes"))) is False
        assert store.get_state("Anime Films", "Obscure OVA (1994)").status == "failed"
        # Not needed, not touched.
        assert (logs / "plex-anime-ratings-progress.txt").is_file()
        assert (logs / "plex-auto-genres-automate.log").is_file()

    text = caplog.text
    assert "was in the v1 layout: converted to v2" in text
    assert "library 'Animes' (anime): genre field, replace existing genres, ratings" in text
    assert "library 'Anime Films' (anime): collections, sort" in text
    assert "Imported 3 v1 progress entries for 'Animes'" in text
    assert "Renamed plex-anime-successful.txt" in text
    assert "not needed by v2, left untouched" in text


def test_a_second_start_does_nothing(tmp_path, caplog):
    config, logs = v1_install(tmp_path)
    with Store(logs / "state.db") as store:
        migrate_install(config, logs, store)
        backup_text = config.with_name("config.json.v1").read_text()
        caplog.clear()
        caplog.set_level(logging.INFO, logger="plex_auto_genres.migration")

        report = migrate_install(config, logs, store)

    assert not report.did_anything
    assert config.with_name("config.json.v1").read_text() == backup_text
    assert not [p for p in config.parent.iterdir() if p.name.startswith("config.json.v1.")]
    assert "Imported" not in caplog.text and "converted" not in caplog.text


def test_an_existing_backup_is_never_overwritten(tmp_path):
    config, logs = v1_install(tmp_path)
    config.with_name("config.json.v1").write_text("an older backup")
    with Store(logs / "state.db") as store:
        report = migrate_install(config, logs, store)
    assert config.with_name("config.json.v1").read_text() == "an older backup"
    assert report.config_backup is not None and report.config_backup.name.startswith("config.json.v1.")


def test_progress_files_for_an_unconfigured_type_are_left_in_place(tmp_path, caplog):
    config, logs = v1_install(tmp_path)
    (logs / "plex-standard-movie-successful.txt").write_text(json.dumps(["Heat (1995)"]))
    caplog.set_level(logging.INFO, logger="plex_auto_genres.migration")
    with Store(logs / "state.db") as store:
        report = migrate_install(config, logs, store)
    assert (logs / "plex-standard-movie-successful.txt").is_file()
    assert any("none is configured" in note for note in report.notes)


def test_a_broken_v1_file_is_left_for_the_command_to_report(tmp_path):
    config, logs = v1_install(tmp_path)
    config.write_text("{not json")
    with Store(logs / "state.db") as store:
        report = migrate_install(config, logs, store)
    assert not report.config_converted and config.read_text() == "{not json"
    assert (logs / "plex-anime-successful.txt").is_file()


def test_a_v2_install_is_untouched(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"version": 2, "libraries": []}))
    logs = tmp_path / "logs"
    logs.mkdir()
    with Store(logs / "state.db") as store:
        report = migrate_install(config, logs, store)
    assert not report.did_anything and not config.with_name("config.json.v1").exists()


def test_doctor_warns_about_a_v1_layout_before_anything_is_touched(tmp_path, monkeypatch):
    monkeypatch.setenv("PLEX_BASE_URL", "http://plex:32400")
    monkeypatch.setenv("PLEX_TOKEN", "t")
    config, logs = v1_install(tmp_path)
    with Store(logs / "state.db") as store:
        report = run_doctor(str(config), store, check_taxonomy=False)
    layout = next(c for c in report.checks if c.id == "config-layout")
    assert layout.level == "warn" and "config.json.v1" in layout.detail
    assert json.loads(config.read_text()) == V1, "doctor is read-only"


def test_the_cli_migrates_before_running(tmp_path, monkeypatch, caplog):
    from plex_auto_genres import cli

    monkeypatch.setenv("PLEX_BASE_URL", "http://plex:32400")
    monkeypatch.setenv("PLEX_TOKEN", "t")
    config, logs = v1_install(tmp_path)

    async def fake_run(args, config_obj, store, style):
        return 0

    monkeypatch.setattr(cli, "cmd_run", fake_run)
    # Default verbosity (no -v) leaves the root logger at WARNING; the migration
    # logger is pinned to INFO so its story gets through regardless.
    caplog.handler.setLevel(logging.INFO)
    assert cli.main(["--config", str(config), "--db", str(logs / "state.db"), "run", "-y"]) == 0
    assert json.loads(config.read_text())["version"] == 2
    assert config.with_name("config.json.v1").is_file()
    assert (logs / "plex-anime-successful.txt.imported").is_file()
    assert "converted to v2" in caplog.text


# -- guards ---------------------------------------------------------------------


def test_a_v2_config_without_a_version_key_is_not_touched(tmp_path):
    """The v1 gate is the *shape*: migrating a v2 file would empty it."""
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "defaults": {"anime": {"ignore": ["Kids"]}},
        "libraries": [{"library": "Animes", "type": "anime", "useGenres": True}],
    }))
    before = config.read_text()
    logs = tmp_path / "logs"
    logs.mkdir()

    with Store(logs / "state.db") as store:
        report = migrate_install(config, logs, store)

    assert not report.config_converted
    assert config.read_text() == before
    assert not config.with_name("config.json.v1").exists()
    assert [r.library for r in load_config(config).libraries] == ["Animes"]


def test_a_config_that_cannot_be_rewritten_is_reported_not_raised(tmp_path, caplog):
    config, logs = v1_install(tmp_path)
    config.parent.chmod(0o555)          # read-only directory
    caplog.set_level(logging.ERROR, logger="plex_auto_genres.migration")
    try:
        with Store(logs / "state.db") as store:
            report = migrate_install(config, logs, store)
    finally:
        config.parent.chmod(0o755)

    assert not report.config_converted and report.failures
    assert json.loads(config.read_text()) == V1, "the v1 file is intact"
    assert "could not be rewritten" in caplog.text
    # The in-memory migration still works, so the app keeps running.
    assert [r.library for r in load_config(config).libraries] == ["Animes", "Anime Films"]


def test_a_corrupt_progress_file_is_left_alone_and_stays_importable(tmp_path):
    config, logs = v1_install(tmp_path)
    (logs / "plex-anime-successful.txt").write_text("{{{ truncated by a killed container")

    with Store(logs / "state.db") as store:
        report = migrate_install(config, logs, store)
        assert report.imported == {}
        assert store.kv_get("legacy_imported::Animes") is None, "not flagged: it can be repaired"

    assert (logs / "plex-anime-successful.txt").is_file(), "not renamed away"
    assert any("not readable" in note for note in report.notes)


def test_an_existing_imported_backup_is_never_clobbered(tmp_path):
    config, logs = v1_install(tmp_path)
    keep = logs / "plex-anime-successful.txt.imported"
    keep.write_text("an older backup")

    with Store(logs / "state.db") as store:
        report = migrate_install(config, logs, store)

    assert keep.read_text() == "an older backup"
    assert any(p.name.startswith("plex-anime-successful.txt.imported.") for p in report.renamed)


def test_the_legacy_logs_directory_falls_back_to_logs(tmp_path, monkeypatch):
    from plex_auto_genres.migration import legacy_logs_dir

    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "plex-anime-successful.txt").write_text("[]")
    elsewhere = tmp_path / "var"
    elsewhere.mkdir()

    # A --db outside logs/ must still find v1's files.
    assert legacy_logs_dir(elsewhere / "state.db") == Path("logs")
    # ...and files next to the database win.
    (elsewhere / "plex-anime-failures.txt").write_text("[]")
    assert legacy_logs_dir(elsewhere / "state.db") == elsewhere
