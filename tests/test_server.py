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
    assert next(r["run_id"] for r in body) == running
    # An open row is "running" only while a job of this process owns it; a
    # row nobody owns was interrupted, however recently it was opened.
    assert status == {ok: "ok", partial: "partial", failed: "failed",
                      undone: "undone", running: "interrupted"}


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
    animes = next(lib for lib in client.get("/api/v1/libraries").json() if lib["name"] == "Animes")
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


# -- jobs, SSE, undo (phase 2) ----------------------------------------------


def _job_app(tmp_path, config_file, monkeypatch):
    """An app whose fake Plex has items, so jobs actually process something."""
    from .conftest import FakePlexItem
    from .test_pipeline import FakeServer

    class Section:
        def __init__(self, key, title, items):
            self.key, self.title, self.type, self.agent = key, title, "show", "hama"
            self._items, self.totalSize = items, len(items)

        def all(self):
            return self._items

        def collections(self):
            return []

    class Server(FakeServer):
        def __init__(self, items):
            super().__init__(items)
            self._sec = Section(1, "Animes", items)
            self.library = type("L", (), {
                "section": lambda _s, name: self._sec,
                "sections": lambda _s: [self._sec],
            })()
            self.friendlyName, self.version = "Homelab", "1.0"

    items = []
    for k, title in ((1, "One"), (2, "Two")):
        h = FakePlexItem(k, title, 2000 + k, genres=("Old",))
        h.guids = [type("G", (), {"id": f"mal://{k}"})()]
        items.append(h)
    server = Server(items)
    monkeypatch.setenv("PLEX_BASE_URL", "http://plex:32400")
    monkeypatch.setenv("PLEX_TOKEN", "t")
    monkeypatch.setattr(state_module.plex_client, "connect", lambda s: server)
    return create_app(config_file, tmp_path / "state.db"), server


def _jikan_ok(request):
    import httpx as _httpx

    return _httpx.Response(200, json={"data": {
        "mal_id": 1, "title": "Anime", "genres": [{"name": "Action"}]}})


def _wait_job(c, job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = c.get(f"/api/v1/jobs/{job_id}").json()
        if body["status"] not in ("queued", "running"):
            return body
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def test_start_job_for_one_library_and_watch_it_finish(tmp_path, config_file, monkeypatch):
    import respx as _respx

    app, server = _job_app(tmp_path, config_file, monkeypatch)
    with _respx.mock:
        _respx.get(url__regex=r"https://api\.jikan\.moe/v4/anime/\d+").mock(side_effect=_jikan_ok)
        with TestClient(app) as c:
            response = c.post("/api/v1/libraries/Animes/run", json={"dry_run": False})
            assert response.status_code == 202
            job = response.json()
            assert job["status"] in ("queued", "running") and job["library"] == "Animes"

            done = _wait_job(c, job["job_id"])
            assert done["status"] == "done"
            assert done["progress"]["done"] == 2 and done["reports"][0]["written"] == 2

            run = c.get(f"/api/v1/runs/{done['run_ids'][0]}").json()
            assert run["status"] == "ok" and run["job_id"] == job["job_id"]
            assert server._section.all()[0].last_tags == ["Action"]   # clearGenres: replace


def test_starting_a_second_job_for_a_busy_library_is_a_409(tmp_path, config_file, monkeypatch):
    from plex_auto_genres.jobs import JobManager

    async def block(self, job):
        import asyncio as _a
        await _a.sleep(0.5)

    monkeypatch.setattr(JobManager, "_execute", block)
    app, _ = _job_app(tmp_path, config_file, monkeypatch)
    with TestClient(app) as c:
        assert c.post("/api/v1/libraries/Animes/run").status_code == 202
        dup = c.post("/api/v1/libraries/Animes/run")
        assert dup.status_code == 409
        assert "already queued or running" in dup.json()["detail"]
        # POST /jobs is all-or-nothing
        batch = c.post("/api/v1/jobs", json={"libraries": ["Films", "Animes"]})
        assert batch.status_code == 409
        assert c.get("/api/v1/jobs").json()[0]["library"] == "Animes"


def test_unknown_library_is_a_404(tmp_path, config_file, monkeypatch):
    app, _ = _job_app(tmp_path, config_file, monkeypatch)
    with TestClient(app) as c:
        assert c.post("/api/v1/libraries/Nope/run").status_code == 404


def test_start_all_enabled_libraries(tmp_path, config_file, monkeypatch):
    from plex_auto_genres.jobs import JobManager

    async def quick(self, job):
        return None

    monkeypatch.setattr(JobManager, "_execute", quick)
    app, _ = _job_app(tmp_path, config_file, monkeypatch)
    with TestClient(app) as c:
        response = c.post("/api/v1/jobs", json={})
        assert response.status_code == 202
        assert [j["library"] for j in response.json()] == ["Animes"]   # Films is disabled


def test_cancel_a_running_job_and_refuse_a_finished_one(tmp_path, config_file, monkeypatch):
    from plex_auto_genres.jobs import JobManager

    async def block(self, job):
        import asyncio as _a
        await _a.sleep(5)

    monkeypatch.setattr(JobManager, "_execute", block)
    app, _ = _job_app(tmp_path, config_file, monkeypatch)
    with TestClient(app) as c:
        job = c.post("/api/v1/libraries/Animes/run").json()
        time.sleep(0.05)
        cancelled = c.post(f"/api/v1/jobs/{job['job_id']}/cancel")
        assert cancelled.status_code == 200
        done = _wait_job(c, job["job_id"])
        assert done["status"] == "cancelled"
        again = c.post(f"/api/v1/jobs/{job['job_id']}/cancel")
        assert again.status_code == 409


def test_sse_stream_ends_with_an_end_event(tmp_path, config_file, monkeypatch):
    import asyncio as _a

    from plex_auto_genres.models import ProviderResult
    from plex_auto_genres.providers.jikan import JikanProvider

    async def slow_resolve(self, request):
        await _a.sleep(0.15)
        return ProviderResult(provider="jikan", provider_id="1", title=request.title,
                              genres=["Action"])

    monkeypatch.setattr(JikanProvider, "resolve", slow_resolve)
    app, _ = _job_app(tmp_path, config_file, monkeypatch)
    if True:
        with TestClient(app) as c:
            job = c.post("/api/v1/libraries/Animes/run").json()
            events = []
            with c.stream("GET", f"/api/v1/jobs/{job['job_id']}/events") as stream:
                assert stream.headers["content-type"].startswith("text/event-stream")
                current = None
                for line in stream.iter_lines():
                    if line.startswith("event: "):
                        current = line[7:]
                    elif line.startswith("data: ") and current:
                        events.append((current, json.loads(line[6:])))
                        if current == "end":
                            break
            names = [e for e, _ in events]
            assert names[0] == "snapshot" and names[-1] == "end"
            assert "begin" in names and "item" in names and "report" in names
            assert events[-1][1]["status"] == "done"


def test_sse_for_an_unknown_job_is_a_404(client):
    assert client.get("/api/v1/jobs/nope/events").status_code == 404


def test_undo_from_the_api(tmp_path, config_file, monkeypatch):
    import respx as _respx

    app, server = _job_app(tmp_path, config_file, monkeypatch)
    with _respx.mock:
        _respx.get(url__regex=r"https://api\.jikan\.moe/v4/anime/\d+").mock(side_effect=_jikan_ok)
        with TestClient(app) as c:
            job = c.post("/api/v1/libraries/Animes/run").json()
            done = _wait_job(c, job["job_id"])
            run_id = done["run_ids"][0]
            first = server._section.all()[0]
            assert first.last_tags == ["Action"]

            response = c.post(f"/api/v1/runs/{run_id}/undo")
            assert response.status_code == 200
            assert response.json() == {"run_id": run_id, "restored": 2, "skipped": 0}
            assert first.last_tags == ["Old"]
            assert c.get(f"/api/v1/runs/{run_id}").json()["status"] == "undone"

            assert c.post(f"/api/v1/runs/{run_id}/undo").status_code == 409   # twice
            assert c.post("/api/v1/runs/nope/undo").status_code == 404


def test_undo_refuses_while_a_job_runs_for_that_library(tmp_path, config_file, monkeypatch):
    from plex_auto_genres.jobs import JobManager

    async def block(self, job):
        import asyncio as _a
        await _a.sleep(1)

    monkeypatch.setattr(JobManager, "_execute", block)
    app, _ = _job_app(tmp_path, config_file, monkeypatch)
    store = Store(tmp_path / "state.db")
    run_id = _seed_run(store, "Animes")
    store.add_snapshot(run_id, "Animes", 1, "One", "genre", ["Old"], ["Old", "Action"])
    store.close()
    with TestClient(app) as c:
        c.post("/api/v1/libraries/Animes/run")
        time.sleep(0.05)
        response = c.post(f"/api/v1/runs/{run_id}/undo")
        assert response.status_code == 409
        assert "job is running" in response.json()["detail"]


def test_undo_with_nothing_recorded_is_a_400(client, tmp_path):
    store = Store(tmp_path / "state.db")
    run_id = _seed_run(store, "Animes")
    store.close()
    response = client.post(f"/api/v1/runs/{run_id}/undo")
    assert response.status_code == 400


def test_scheduled_pass_goes_through_the_job_queue(tmp_path, config_file, monkeypatch):
    """With --cron and --now, the boot-time pass is a queued job, not a bare call."""
    from plex_auto_genres.jobs import JobManager

    async def quick(self, job):
        return None

    monkeypatch.setattr(JobManager, "_execute", quick)
    monkeypatch.setenv("PLEX_BASE_URL", "http://plex:32400")
    monkeypatch.setenv("PLEX_TOKEN", "t")
    monkeypatch.setattr(state_module.plex_client, "connect", lambda s: FakePlex())
    app = create_app(config_file, tmp_path / "state.db", cron="0 1 * * *", run_on_start=True)
    with TestClient(app) as c:
        time.sleep(0.1)
        jobs = c.get("/api/v1/jobs").json()
        assert [j["source"] for j in jobs] == ["schedule"]
        assert jobs[0]["library"] == "Animes"


# -- config editing (phase 3) -------------------------------------------------


def test_get_config_carries_an_etag_that_tracks_the_file(client, config_file):
    first = client.get("/api/v1/config").json()["etag"]
    assert first
    raw = json.loads(config_file.read_text())
    raw["libraries"].append({"library": "New", "type": "anime"})
    time.sleep(0.02)
    config_file.write_text(json.dumps(raw))
    assert client.get("/api/v1/config").json()["etag"] != first


def test_validate_endpoint_is_a_dry_run(client, config_file):
    before = config_file.read_text()
    response = client.post("/api/v1/config/validate", json={
        "version": 2, "libraries": [{"library": "X", "type": "anime", "clearGenres": True}],
    })
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["errors"][0]["loc"] == ["libraries", 0]
    assert "clearGenres requires useGenres" in body["errors"][0]["msg"]
    assert config_file.read_text() == before


def test_put_config_writes_the_file_and_the_server_reloads_it(client, config_file):
    view = client.get("/api/v1/config").json()
    doc = {"version": 2, "defaults": view["defaults"], "libraries": view["libraries"]}
    doc["libraries"][0]["clearGenres"] = False
    doc["libraries"].append({"library": "Séries", "type": "standard-tv", "useGenres": True})

    response = client.put("/api/v1/config", json=doc, headers={"If-Match": view["etag"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] and body["backup"].endswith("config.json.bak")

    on_disk = json.loads(config_file.read_text())
    assert [lib["library"] for lib in on_disk["libraries"]] == ["Animes", "Films", "Séries"]
    assert on_disk["libraries"][0]["clearGenres"] is False
    assert "plex" not in on_disk

    fresh = client.get("/api/v1/config").json()
    assert fresh["etag"] == body["etag"]
    assert fresh["libraries"][2]["library"] == "Séries"
    libs = {lib["name"]: lib for lib in client.get("/api/v1/libraries").json()}
    assert "Séries" in libs   # every other endpoint sees the new config at once


def test_put_config_refuses_a_stale_etag(client, config_file):
    view = client.get("/api/v1/config").json()
    doc = {"version": 2, "defaults": view["defaults"], "libraries": view["libraries"]}
    response = client.put("/api/v1/config", json=doc, headers={"If-Match": "0000000000000000"})
    assert response.status_code == 412
    assert response.json()["etag"] == view["etag"]
    assert response.headers["etag"] == f'"{view["etag"]}"'


def test_put_config_rejects_an_invalid_document_without_touching_the_file(client, config_file):
    before = config_file.read_text()
    response = client.put("/api/v1/config", json={
        "version": 2, "libraries": [{"library": "X", "type": "anime"}, {"library": "x", "type": "anime"}],
    })
    assert response.status_code == 422
    body = response.json()
    assert body["ok"] is False and "more than once" in body["errors"][0]["msg"]
    assert config_file.read_text() == before


def test_put_config_preserves_comments_on_disk(client, config_file):
    raw = json.loads(config_file.read_text())
    raw["//"] = "hand-written note"
    raw["libraries"][0]["//why"] = "because"
    config_file.write_text(json.dumps(raw))
    view = client.get("/api/v1/config").json()

    doc = {"version": 2, "defaults": view["defaults"], "libraries": list(reversed(view["libraries"]))}
    assert client.put("/api/v1/config", json=doc, headers={"If-Match": view["etag"]}).status_code == 200

    on_disk = json.loads(config_file.read_text())
    assert on_disk["//"] == "hand-written note"
    animes = next(lib for lib in on_disk["libraries"] if lib["library"] == "Animes")
    assert animes["//why"] == "because"        # followed the library, not the index


# -- review regressions ---------------------------------------------------------


def test_start_jobs_queues_nothing_when_a_name_is_unknown(tmp_path, config_file, monkeypatch):
    from plex_auto_genres.jobs import JobManager

    async def quick(self, job):
        return None

    monkeypatch.setattr(JobManager, "_execute", quick)
    app, _ = _job_app(tmp_path, config_file, monkeypatch)
    with TestClient(app) as c:
        assert c.post("/api/v1/jobs", json={"libraries": ["Animes", "Nope"]}).status_code == 404
        assert c.get("/api/v1/jobs").json() == []


def test_a_run_owned_by_a_live_job_reports_running(tmp_path, config_file, monkeypatch):
    import asyncio as _asyncio

    from plex_auto_genres.models import ProviderResult
    from plex_auto_genres.providers.jikan import JikanProvider

    async def slow_resolve(self, request):
        await _asyncio.sleep(0.4)
        return ProviderResult(provider="jikan", provider_id="1", title=request.title,
                              genres=["Action"])

    monkeypatch.setattr(JikanProvider, "resolve", slow_resolve)
    app, _ = _job_app(tmp_path, config_file, monkeypatch)
    with TestClient(app) as c:
        job = c.post("/api/v1/libraries/Animes/run").json()
        deadline = time.time() + 5
        listed: list[dict] = []
        while time.time() < deadline:
            listed = c.get("/api/v1/runs").json()
            if listed and listed[0]["status"] == "running":
                break
            time.sleep(0.05)
        assert listed and listed[0]["status"] == "running"
        assert listed[0]["job_id"] == job["job_id"]

        done = _wait_job(c, job["job_id"])
        assert c.get(f"/api/v1/runs/{done['run_ids'][0]}").json()["status"] == "ok"
