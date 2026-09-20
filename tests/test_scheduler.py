"""The live scheduler: config over fallback, pause, replan, firing."""

from __future__ import annotations

import asyncio
import contextlib
import time

from plex_auto_genres import scheduler as scheduler_module
from plex_auto_genres.scheduler import Scheduler, next_fires, validate_cron


async def test_plan_prefers_the_config_and_honours_pause():
    settings: dict = {"value": ("0 2 * * *", True)}

    async def run() -> None:
        pass

    s = Scheduler(run, settings=lambda: settings["value"], fallback="0 1 * * *")
    assert (s.plan.cron, s.plan.source, s.plan.enabled) == ("0 2 * * *", "config", True)
    assert s.plan.next_fire_at > time.time()

    settings["value"] = (None, True)          # no expression in the file: the fallback
    s.replan()
    assert (s.plan.cron, s.plan.source) == ("0 1 * * *", "env")

    settings["value"] = ("0 2 * * *", False)  # paused: the expression stays, nothing fires
    s.replan()
    assert s.plan.enabled is False and s.plan.next_fire_at is None and s.plan.cron == "0 2 * * *"

    # Broken by hand: the expression stays visible so the operator can see
    # what is wrong, but nothing fires.
    settings["value"] = ("not a cron", True)
    s.replan()
    assert (s.plan.cron, s.plan.source, s.plan.next_fire_at) == ("not a cron", "config", None)

    # Valid fields, no reachable date: same treatment, and refused on save.
    settings["value"] = ("0 0 30 2 *", True)
    s.replan()
    assert s.plan.next_fire_at is None


async def test_unreadable_settings_keep_the_last_plan():
    """A broken config must not resurrect a pass the operator paused."""

    async def run() -> None:
        pass

    state: dict = {"value": ("0 2 * * *", False)}

    def settings():
        if state["value"] == "boom":
            raise RuntimeError("config unreadable")
        return state["value"]

    s = Scheduler(run, settings=settings, fallback="0 1 * * *")
    assert s.plan.enabled is False and s.plan.next_fire_at is None

    state["value"] = "boom"
    plan = s.replan()
    assert plan.enabled is False and plan.cron == "0 2 * * *", "the pause survived"
    assert plan.next_fire_at is None and "unreadable" in (plan.error or "")


async def test_times_are_local_not_utc():
    """croniter reads a bare timestamp as UTC; the schedule must follow TZ."""
    import os
    import time as _time

    from plex_auto_genres.scheduler import next_fire

    previous = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Paris"
    _time.tzset()
    try:
        fire = next_fire("0 1 * * *")
        assert fire is not None
        assert _time.localtime(fire).tm_hour == 1, "01:00 means 01:00 where the server lives"
    finally:
        if previous is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = previous
        _time.tzset()


async def test_a_paused_schedule_is_not_run_on_start():
    fired: list[int] = []

    async def run() -> None:
        fired.append(1)

    s = Scheduler(run, settings=lambda: ("0 1 * * *", False), fallback=None)
    task = asyncio.create_task(s.run_forever(run_now=True))
    await asyncio.sleep(0.05)
    assert fired == [], "a paused schedule stays paused across a restart"
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_the_loop_fires_when_due_and_wakes_on_replan(monkeypatch):
    fired = asyncio.Event()

    async def run() -> None:
        fired.set()

    settings: dict = {"value": (None, True)}
    s = Scheduler(run, settings=lambda: settings["value"], fallback=None)
    task = asyncio.create_task(s.run_forever())
    await asyncio.sleep(0.05)
    assert s.plan.cron is None and not fired.is_set()

    monkeypatch.setattr(scheduler_module, "next_fire", lambda expr, now=None: time.time() + 0.1)
    settings["value"] = ("* * * * *", True)
    s.replan()
    await asyncio.wait_for(fired.wait(), 2)

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_run_now_fires_immediately():
    calls: list[int] = []

    async def run() -> None:
        calls.append(1)

    s = Scheduler(run, settings=lambda: None, fallback=None)
    task = asyncio.create_task(s.run_forever(run_now=True))
    await asyncio.sleep(0.05)
    assert calls == [1]
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def test_previews_and_validation():
    fires = next_fires("0 1 * * *", 3)
    assert len(fires) == 3 and fires[0] < fires[1] < fires[2]
    validate_cron("*/15 * * * *")
    try:
        validate_cron("every tuesday")
    except ValueError as exc:
        assert "Invalid cron" in str(exc)
    else:
        raise AssertionError("expected a ValueError")
