"""HTTP API, phase 1 (read-only)."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from plex_auto_genres.errors import PlexConnectionError
from plex_auto_genres.models import RunReport
from plex_auto_genres.server import create_app
from plex_auto_genres.server import state as state_module
from plex_auto_genres.store import Store


class FakeSection:
    def __init__(self, key, title, type_, size):
        self.key, self.title, self.type, self.totalSize = key, title, type_, size
        self.agent = "tv.plex.agents.series"


class FakeLibrary:
    def __init__(self, sections):
        self._sections = sections

    def sections(self):
        return self._sections


class FakePlex:
    friendlyName = "Homelab"
    version = "1.41.0"

    def __init__(self, sections=()):
        self.library = FakeLibrary(list(sections))


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "version": 2,
        "defaults": {"anime": {"ignore": ["Kids"]}},
        "libraries": [
            {"library": "Animes", "type": "anime", "useGenres": True, "clearGenres": True},
            {"library": "Films", "type": "standard-movie", "enabled": False},
        ],
    }))
    return path


@pytest.fixture
def client(tmp_path, config_file, monkeypatch):
    monkeypatch.setenv("PLEX_BASE_URL", "http://plex:32400")
    monkeypatch.setenv("PLEX_TOKEN", "t")
    monkeypatch.setenv("TMDB_API_KEY", "k")
    fake = FakePlex([FakeSection(1, "Animes", "show", 120), FakeSection(2, "Photos", "photo", 9),
                     FakeSection(3, "Unconfigured Movies", "movie", 40)])
    monkeypatch.setattr(state_module.plex_client, "connect", lambda settings: fake)
    app = create_app(config_file, tmp_path / "state.db", static_dir=tmp_path / "nope")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def offline_client(tmp_path, config_file, monkeypatch):
    monkeypatch.setenv("PLEX_BASE_URL", "http://plex:32400")
    monkeypatch.setenv("PLEX_TOKEN", "t")

    def boom(settings):
        raise PlexConnectionError("connection refused")

    monkeypatch.setattr(state_module.plex_client, "connect", boom)
    app = create_app(config_file, tmp_path / "state.db")
    with TestClient(app) as c:
        yield c


# -- health ------------------------------------------------------------------


def test_health_reports_plex_and_version(client):
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert body["plex"] == {
        "reachable": True, "server_name": "Homelab", "version": "1.41.0",
        "error": None, "checked_at": pytest.approx(time.time(), abs=5),
    }
    assert body["version"]
    assert body["scheduler"] is None


def test_health_degrades_when_plex_is_down(offline_client):
    body = offline_client.get("/api/v1/health").json()
    assert body["status"] == "degraded"
    assert body["plex"]["reachable"] is False
    assert "connection refused" in body["plex"]["error"]


# -- config ------------------------------------------------------------------


def test_config_never_leaks_secret_values(client):
    body = client.get("/api/v1/config").json()
    assert body["secrets"] == {
        "plex_token": True, "plex_password": False, "tmdb_api_key": True,
        "plex_base_url": "http://plex:32400", "plex_server_name": None, "collection_prefix": "",
    }
    text = json.dumps(body)
    assert '"t"' not in text and '"k"' not in text


def test_config_uses_the_file_s_own_key_names(client):
    """Aliases, so the UI shows what the user wrote (useGenres, not use_genres)."""
    body = client.get("/api/v1/config").json()
    first = body["libraries"][0]
    assert first["useGenres"] is True
    assert "use_genres" not in first


def test_config_reloads_when_the_file_changes(client, config_file):
    assert len(client.get("/api/v1/config").json()["libraries"]) == 2
    raw = json.loads(config_file.read_text())
    raw["libraries"].append({"library": "New", "type": "anime"})
    time.sleep(0.02)
    config_file.write_text(json.dumps(raw))
    assert len(client.get("/api/v1/config").json()["libraries"]) == 3


def test_broken_config_is_a_503_not_a_500(client, config_file):
    config_file.write_text("{ nope")
    response = client.get("/api/v1/config")
    assert response.status_code == 503
    assert "not valid JSON" in response.json()["detail"]


def test_schema_is_served(client):
    body = client.get("/api/v1/config/schema").json()
    assert "LibraryRun" in body["$defs"]


# -- doctor ------------------------------------------------------------------


def test_doctor_is_structured(client, monkeypatch):
    from plex_auto_genres import doctor as doctor_module

    monkeypatch.setattr(doctor_module, "fetch_live_genres", lambda store: ["Action"])
    body = client.get("/api/v1/doctor").json()
    ids = {c["id"]: c for c in body["checks"]}
    assert ids["config"]["level"] == "ok"
    assert ids["plex-credentials"]["level"] == "ok"
    assert body["ok"] is True
    assert isinstance(body["warnings"], int)


# -- libraries ---------------------------------------------------------------


def test_libraries_merge_config_and_plex(client):
    body = client.get("/api/v1/libraries").json()
    by_name = {lib["name"]: lib for lib in body}

    animes = by_name["Animes"]
    assert animes["configured"] is True
    assert animes["useGenres"] is True
    assert animes["plex"] == {"key": 1, "section_type": "show", "item_count": 120,
                              "agent": "tv.plex.agents.series"}

    films = by_name["Films"]
    assert films["configured"] and films["enabled"] is False
    assert films["plex"] is None  # not on the server

    extra = by_name["Unconfigured Movies"]
    assert extra["configured"] is False and extra["plex"]["item_count"] == 40
    assert "Photos" not in by_name  # photo sections are not tagging targets


def test_libraries_degrade_to_config_only_when_plex_is_down(offline_client):
    body = offline_client.get("/api/v1/libraries").json()
    assert [lib["name"] for lib in body] == ["Animes", "Films"]
    assert all(lib["plex"] is None for lib in body)


# -- runs --------------------------------------------------------------------


def _seed_run(store: Store, library="Animes", *, written=3, failed=0, finish=True, undo=False):
    run_id = store.start_run(library, "genres", dry_run=False)
    if finish:
        store.finish_run(RunReport(run_id=run_id, library=library, action="genres",
                                   written=written, failed=failed))
    if undo:
        store.mark_undone(run_id)
    return run_id


def test_runs_are_listed_newest_first_with_a_derived_status(client, tmp_path):
    store = Store(tmp_path / "state.db")
    ok = _seed_run(store)
    partial = _seed_run(store, written=2, failed=1)
    failed = _seed_run(store, written=0, failed=4)
    undone = _seed_run(store, undo=True)
    running = _seed_run(store, finish=False)
    store.close()

    body = client.get("/api/v1/runs").json()
    status = {r["run_id"]: r["status"] for r in body}
    assert [r["run_id"] for r in body][0] == running
    assert status == {ok: "ok", partial: "partial", failed: "failed",
                      undone: "undone", running: "running"}


def test_unfinished_runs_from_a_previous_process_are_interrupted(tmp_path, config_file, monkeypatch):
    monkeypatch.setenv("PLEX_BASE_URL", "http://plex:32400")
    monkeypatch.setenv("PLEX_TOKEN", "t")
    monkeypatch.setattr(state_module.plex_client, "connect", lambda s: FakePlex())
    store = Store(tmp_path / "state.db")
    stale = _seed_run(store, finish=False)
    store.close()
    time.sleep(0.02)

    with TestClient(create_app(config_file, tmp_path / "state.db")) as c:
        assert c.get(f"/api/v1/runs/{stale}").json()["status"] == "interrupted"


def test_unknown_run_is_a_404_problem(client):
    response = client.get("/api/v1/runs/nope")
    assert response.status_code == 404
    assert response.json() == {"title": "Not Found", "status": 404, "detail": "No run 'nope'."}


def test_runs_filter_by_library(client, tmp_path):
    store = Store(tmp_path / "state.db")
    _seed_run(store, "Animes")
    _seed_run(store, "Films")
    store.close()
    body = client.get("/api/v1/runs", params={"library": "Films"}).json()
    assert [r["library"] for r in body] == ["Films"]


def test_libraries_carry_their_last_run(client, tmp_path):
    store = Store(tmp_path / "state.db")
    run_id = _seed_run(store, "Animes")
    store.close()
    animes = next(l for l in client.get("/api/v1/libraries").json() if l["name"] == "Animes")
    assert animes["last_run"]["run_id"] == run_id


# -- bindings ----------------------------------------------------------------


def test_bindings_are_listed(client, tmp_path):
    store = Store(tmp_path / "state.db")
    store.set_binding("Animes", "Monster", "mal", "19", note="why")
    store.close()
    body = client.get("/api/v1/bindings").json()
    assert body[0]["provider_id"] == "19" and body[0]["note"] == "why"


# -- UI hosting --------------------------------------------------------------


def test_missing_ui_build_explains_itself(client):
    response = client.get("/runs")
    assert response.status_code == 503
    assert "pnpm" in response.json()["detail"]


def test_spa_fallback_serves_index_for_client_routes(tmp_path, config_file, monkeypatch):
    monkeypatch.setenv("PLEX_BASE_URL", "http://plex:32400")
    monkeypatch.setenv("PLEX_TOKEN", "t")
    monkeypatch.setattr(state_module.plex_client, "connect", lambda s: FakePlex())
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>ui</title>")
    (static / "assets" / "app.js").write_text("console.log(1)")

    with TestClient(create_app(config_file, tmp_path / "state.db", static_dir=static)) as c:
        assert "<title>ui" in c.get("/").text
        assert "<title>ui" in c.get("/runs/abc").text       # client-side route
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert c.get("/api/v1/nope").status_code == 404      # API 404s stay 404s
        assert c.get("/../../etc/passwd").status_code in (200, 404)  # never escapes


def test_scheduler_status_is_exposed(tmp_path, config_file, monkeypatch):
    monkeypatch.setenv("PLEX_BASE_URL", "http://plex:32400")
    monkeypatch.setenv("PLEX_TOKEN", "t")
    monkeypatch.setattr(state_module.plex_client, "connect", lambda s: FakePlex())
    with TestClient(create_app(config_file, tmp_path / "state.db", cron="0 1 * * *")) as c:
        body = c.get("/api/v1/health").json()
        assert body["scheduler"]["cron"] == "0 1 * * *"
        assert body["scheduler"]["next_fire_at"] > time.time()


def test_an_explicit_static_dir_without_a_build_does_not_fall_back(tmp_path, monkeypatch):
    """Pointing --static-dir at the wrong place must not silently serve the
    package's own build (or whatever PAG_STATIC_DIR names)."""
    from plex_auto_genres.server import app as app_module

    built = tmp_path / "built"
    built.mkdir()
    (built / "index.html").write_text("<title>built</title>")
    monkeypatch.setenv("PAG_STATIC_DIR", str(built))
    monkeypatch.setattr(app_module, "PACKAGE_STATIC", built)

    assert app_module.resolve_static_dir(tmp_path / "wrong") is None
    assert app_module.resolve_static_dir(None) == built
