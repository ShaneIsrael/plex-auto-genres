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

    settings["value"] = ("not a cron", True)  # broken by hand: off until fixed
    s.replan()
    assert (s.plan.cron, s.plan.source, s.plan.next_fire_at) == (None, "none", None)

    def boom():
        raise RuntimeError("config unreadable")

    s = Scheduler(run, settings=boom, fallback="0 1 * * *")
    assert (s.plan.cron, s.plan.source) == ("0 1 * * *", "env")


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
