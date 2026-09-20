"""The job manager: queueing, one-at-a-time, cancellation, events."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx

from plex_auto_genres.config import AppConfig
from plex_auto_genres.jobs import JobConflict, JobManager, JobOptions
from plex_auto_genres.models import ExternalId, MediaType, ProviderResult
from plex_auto_genres.providers.jikan import JikanProvider
from plex_auto_genres.store import Store

from .conftest import FakePlexItem
from .test_pipeline import FakeServer


def make_config() -> AppConfig:
    return AppConfig.model_validate({
        "version": 2,
        "libraries": [
            {"library": "Animes", "type": "anime", "useGenres": True},
            {"library": "Films", "type": "standard-movie", "enabled": False},
        ],
        "providers": {"concurrency": 2},
    })


def item(rating_key: int, title: str) -> FakePlexItem:
    handle = FakePlexItem(rating_key, title, 2000 + rating_key)
    handle.guids = [type("G", (), {"id": f"mal://{rating_key}"})()]
    return handle


def jikan_ok(request):  # respx side effect; returns genres for any id
    return httpx.Response(200, json={"data": {
        "mal_id": 1, "title": "Anime", "genres": [{"name": "Action"}]}})


@pytest.fixture
def manager(store: Store):
    server = FakeServer([item(1, "One"), item(2, "Two"), item(3, "Three")])
    config = make_config()

    async def plex():
        return server

    m = JobManager(store, lambda: config, plex, history=5)
    return m


async def wait_for(predicate, timeout=5.0, step=0.02):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(step)


@respx.mock
async def test_job_runs_to_completion_and_records_runs(manager: JobManager, store: Store):
    respx.get(url__regex=r"https://api\.jikan\.moe/v4/anime/\d+").mock(side_effect=jikan_ok)
    await manager.start()
    job = manager.enqueue("Animes", JobOptions())
    assert job.status == "queued"

    await wait_for(lambda: job.status == "done")

    assert len(job.run_ids) == 1
    assert job.reports[0]["written"] == 3
    assert job.progress.done == 3 and job.progress.pending == 3
    row = store.get_run(job.run_ids[0])
    assert row["finished_at"] is not None
    assert manager.get(job.job_id) is job          # remembered in history
    assert manager.job_for_run(job.run_ids[0]) is job
    await manager.stop()


async def test_unknown_library_is_rejected_up_front(manager: JobManager):
    from plex_auto_genres.jobs import JobError

    with pytest.raises(JobError):
        manager.enqueue("Nope")


async def test_only_one_job_per_library(manager: JobManager):
    manager.enqueue("Animes")
    with pytest.raises(JobConflict):
        manager.enqueue("animes")   # case-insensitive, like config.find


@respx.mock
async def test_jobs_run_one_at_a_time_in_order(manager: JobManager, monkeypatch):
    respx.get(url__regex=r"https://api\.jikan\.moe/v4/anime/\d+").mock(side_effect=jikan_ok)
    order: list[str] = []
    real = JobManager._execute

    async def slow(self, job):
        order.append(f"start:{job.library}")
        await asyncio.sleep(0.15)
        await real(self, job)
        order.append(f"end:{job.library}")

    monkeypatch.setattr(JobManager, "_execute", slow)
    await manager.start()
    first = manager.enqueue("Animes")
    # Films is disabled but a manual job may still target it.
    second = manager.enqueue("Films", JobOptions(only=("sort",)))
    await asyncio.sleep(0.05)
    assert first.status == "running" and second.status == "queued"
    await wait_for(lambda: second.status in ("done", "failed"))
    assert order[:2] == ["start:Animes", "end:Animes"]
    await manager.stop()


async def test_cancelling_a_queued_job_never_runs_it(manager: JobManager, monkeypatch):
    ran: list[str] = []

    async def block(self, job):
        ran.append(job.library)
        await asyncio.sleep(0.3)

    monkeypatch.setattr(JobManager, "_execute", block)
    await manager.start()
    manager.enqueue("Animes")
    queued = manager.enqueue("Films")
    await asyncio.sleep(0.05)

    await manager.cancel(queued.job_id)
    assert queued.status == "cancelled" and queued.finished_at is not None
    assert manager.active_for("Films") is None
    await asyncio.sleep(0.4)
    assert ran == ["Animes"]
    await manager.stop()


async def test_cancelling_a_running_job_closes_its_run_as_cancelled(
    manager: JobManager, store: Store, monkeypatch
):
    async def slow_resolve(self, request):
        await asyncio.sleep(0.2)
        return ProviderResult(provider="jikan", provider_id="1", title=request.title,
                              genres=["Action"])

    monkeypatch.setattr(JikanProvider, "resolve", slow_resolve)
    await manager.start()
    job = manager.enqueue("Animes")
    await wait_for(lambda: job.progress.pending == 3)   # the run has begun

    await manager.cancel(job.job_id)
    await wait_for(lambda: job.status == "cancelled")

    assert job.cancel_requested
    row = store.get_run(job.run_ids[0])
    assert row["finished_at"] is not None, "the run row was closed, not left dangling"
    assert json.loads(row["report"])["cancelled"] is True
    assert manager.active_for("Animes") is None
    # The job's own record agrees with the run row, even though the runner's
    # report callback never fired for an action that raised.
    assert job.reports and job.reports[0]["cancelled"] is True
    assert job.reports[0]["run_id"] == job.run_ids[0]
    await manager.stop()


@respx.mock
async def test_subscribe_streams_begin_items_report_and_end(manager: JobManager):
    respx.get(url__regex=r"https://api\.jikan\.moe/v4/anime/\d+").mock(side_effect=jikan_ok)
    await manager.start()
    job = manager.enqueue("Animes")

    events = []
    async for event, data in manager.subscribe(job.job_id, heartbeat=0.5):
        events.append((event, data))
        if event == "end":
            break

    names = [e for e, _ in events]
    assert names[0] == "snapshot"
    assert "begin" in names and names.count("item") == 3 and "report" in names
    assert names[-1] == "end"
    end = events[-1][1]
    assert end["status"] == "done" and end["reports"][0]["written"] == 3
    await manager.stop()


@respx.mock
async def test_subscribing_to_a_finished_job_replays_snapshot_and_end(manager: JobManager):
    respx.get(url__regex=r"https://api\.jikan\.moe/v4/anime/\d+").mock(side_effect=jikan_ok)
    await manager.start()
    job = manager.enqueue("Animes")
    await wait_for(lambda: job.status == "done")

    events = [e async for e in manager.subscribe(job.job_id)]
    assert [e for e, _ in events] == ["snapshot", "end"]
    assert events[0][1]["status"] == "done"
    await manager.stop()


async def test_subscribe_emits_pings_while_idle(manager: JobManager, monkeypatch):
    async def block(self, job):
        await asyncio.sleep(0.5)

    monkeypatch.setattr(JobManager, "_execute", block)
    await manager.start()
    job = manager.enqueue("Animes")
    seen = []
    async for event, _ in manager.subscribe(job.job_id, heartbeat=0.05):
        seen.append(event)
        if event == "ping":
            break
    assert "ping" in seen
    await manager.stop()


async def test_a_crashing_job_does_not_kill_the_worker(manager: JobManager, monkeypatch):
    calls = {"n": 0}

    async def flaky(self, job):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")

    monkeypatch.setattr(JobManager, "_execute", flaky)
    await manager.start()
    bad = manager.enqueue("Animes")
    await wait_for(lambda: bad.status == "failed")
    assert "RuntimeError: boom" in bad.error

    good = manager.enqueue("Animes")   # same library is free again
    await wait_for(lambda: good.status == "done")
    await manager.stop()


async def test_stop_cancels_the_running_job(manager: JobManager, monkeypatch):
    async def block(self, job):
        await asyncio.sleep(10)

    monkeypatch.setattr(JobManager, "_execute", block)
    await manager.start()
    job = manager.enqueue("Animes")
    await wait_for(lambda: job.status == "running")
    await manager.stop()
    assert job.status == "cancelled" and job.error == "server shutting down"


async def test_enqueue_all_skips_disabled_and_busy_libraries(manager: JobManager, monkeypatch):
    async def block(self, job):
        await asyncio.sleep(0.3)

    monkeypatch.setattr(JobManager, "_execute", block)
    await manager.start()
    first = manager.enqueue_all(JobOptions(source="schedule"))
    assert [j.library for j in first] == ["Animes"]      # Films is disabled
    assert manager.enqueue_all() == []                    # Animes is busy
    await manager.stop()


def test_unknown_media_type_guard():
    assert MediaType("anime").is_anime
    assert ExternalId("mal", "1").scheme == "mal"


# -- review regressions ---------------------------------------------------------


async def test_cancelling_before_the_library_is_read_still_closes_the_row(
    manager: JobManager, store: Store, monkeypatch
):
    import time as _time

    from plex_auto_genres import pipeline as pipeline_module

    real = pipeline_module.plex_client.iter_library

    def slow(server, library, **kwargs):
        _time.sleep(0.4)
        return real(server, library, **kwargs)

    monkeypatch.setattr(pipeline_module.plex_client, "iter_library", slow)
    await manager.start()
    job = manager.enqueue("Animes")
    await wait_for(lambda: job.status == "running")
    await asyncio.sleep(0.1)                      # inside iter_library, before begin()

    await manager.cancel(job.job_id)
    await wait_for(lambda: job.status == "cancelled")

    assert job.run_ids, "the run the job opened is known although begin() never fired"
    row = store.get_run(job.run_ids[0])
    assert row["finished_at"] is not None and json.loads(row["report"])["cancelled"] is True
    assert job.reports and job.reports[0]["run_id"] == job.run_ids[0]
    await manager.stop()


async def test_terminal_messages_reach_a_saturated_subscriber(manager: JobManager):
    job = manager.enqueue("Animes")
    queue: asyncio.Queue = asyncio.Queue(maxsize=2)
    queue.put_nowait(("item", {}))
    queue.put_nowait(("item", {}))
    job.subscribers.append(queue)

    manager._finish(job, "done")

    received = [queue.get_nowait() for _ in range(queue.qsize())]
    assert received[-2][0] == "end" and received[-1] is None


async def test_enqueue_many_is_all_or_nothing(manager: JobManager):
    from plex_auto_genres.jobs import JobError

    with pytest.raises(JobError):
        manager.enqueue_many(["Animes", "Nope"])
    assert manager.list() == []
    with pytest.raises(JobConflict):
        manager.enqueue_many(["Animes", "animes"])
    assert manager.list() == []
