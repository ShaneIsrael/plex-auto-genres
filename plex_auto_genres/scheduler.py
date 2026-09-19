"""Cron-style scheduling, in-process.

Used by ``plex-auto-genres schedule`` and, when a schedule is given, by
``plex-auto-genres serve`` so one container runs both the web UI and the
nightly job.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from croniter import croniter

log = logging.getLogger(__name__)

RunFn = Callable[[], Awaitable[None]]


def validate_cron(expression: str) -> None:
    """Raise ``ValueError`` for an expression croniter cannot parse."""
    if not croniter.is_valid(expression):
        raise ValueError(f"Invalid cron expression: {expression!r}")


def next_fire(expression: str, now: float | None = None) -> float:
    """Unix timestamp of the next occurrence."""
    return croniter(expression, now if now is not None else time.time()).get_next(float)


async def run_forever(
    expression: str,
    run: RunFn,
    *,
    run_now: bool = False,
    on_schedule: Callable[[float], None] | None = None,
) -> None:
    """Fire ``run`` on the schedule until cancelled. Failures never stop it."""
    validate_cron(expression)
    if run_now:
        await _guarded(run)

    while True:
        fire_at = next_fire(expression)
        if on_schedule is not None:
            on_schedule(fire_at)
        wait = max(fire_at - time.time(), 1.0)
        try:
            await asyncio.sleep(wait)
        except asyncio.CancelledError:
            log.info("Scheduler stopped")
            raise
        await _guarded(run)


async def _guarded(run: RunFn) -> None:
    try:
        await run()
    except Exception:  # keep the loop alive across a broken run; CancelledError passes through
        log.exception("Scheduled run crashed")
