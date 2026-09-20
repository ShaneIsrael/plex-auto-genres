"""Cron-style scheduling, in-process.

Used by ``plex-auto-genres schedule`` and, in ``serve``, by the app itself so
one container runs both the web UI and the nightly job. The schedule is
*live*: :class:`Scheduler` re-reads its settings every time it plans, so a
cron expression edited in the config (from the UI or by hand) or a pause
takes effect without a restart.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from croniter import croniter

log = logging.getLogger(__name__)

RunFn = Callable[[], Awaitable[None]]
#: How long the scheduler sleeps between looks at its settings while idle or
#: waiting for a distant fire time; edits take effect within this window
#: unless :meth:`Scheduler.replan` is called, which wakes it at once.
RECHECK_S = 60.0


def validate_cron(expression: str) -> None:
    """Raise ``ValueError`` for an expression croniter cannot parse."""
    if not croniter.is_valid(expression):
        raise ValueError(f"Invalid cron expression: {expression!r}")


def next_fire(expression: str, now: float | None = None) -> float:
    """Unix timestamp of the next occurrence."""
    return croniter(expression, now if now is not None else time.time()).get_next(float)


def next_fires(expression: str, count: int = 3, now: float | None = None) -> list[float]:
    """The next ``count`` occurrences, for previews."""
    it = croniter(expression, now if now is not None else time.time())
    return [it.get_next(float) for _ in range(count)]


@dataclass(frozen=True, slots=True)
class SchedulePlan:
    """What the scheduler is currently set to do."""

    #: The effective cron expression, or ``None`` when nothing is scheduled.
    cron: str | None
    #: False when paused: the expression is kept but never fires.
    enabled: bool
    #: Where the expression came from: ``config`` (the file), ``env`` (the
    #: ``--cron`` flag / ``CRON_SCHEDULE``), or ``none``.
    source: str
    next_fire_at: float | None


#: Returns ``(cron, enabled)`` from the config, or ``None`` when the config
#: has no schedule block (or cannot be read right now).
SettingsFn = Callable[[], tuple[str | None, bool] | None]


class Scheduler:
    """Fire ``run`` on a cron expression that can change underneath it.

    The expression comes from the config file when it has one, else from the
    ``fallback`` given on the command line; the config's ``enabled`` flag can
    pause either. :meth:`plan` is what the API reports; :meth:`replan` wakes
    the loop early after a config save.
    """

    def __init__(
        self,
        run: RunFn,
        *,
        settings: SettingsFn,
        fallback: str | None = None,
        on_schedule: Callable[[float], None] | None = None,
    ) -> None:
        if fallback:
            validate_cron(fallback)
        self._run = run
        self._settings = settings
        self._fallback = fallback
        self._on_schedule = on_schedule
        self._changed = asyncio.Event()
        self._plan: SchedulePlan = SchedulePlan(None, True, "none", None)
        self._stale = True

    @property
    def plan(self) -> SchedulePlan:
        """The current plan; recomputed on demand after :meth:`replan`."""
        if self._stale:
            self._plan = self._resolve()
            self._stale = False
        return self._plan

    def replan(self) -> None:
        """Re-read the settings now instead of at the next periodic check."""
        self._stale = True
        self._changed.set()

    def _resolve(self) -> SchedulePlan:
        cron: str | None = None
        enabled = True
        source = "none"
        try:
            found = self._settings()
        except Exception as exc:  # a broken config must not kill the loop
            log.warning("Schedule settings unavailable (%s); using the fallback", exc)
            found = None
        if found is not None:
            cron, enabled = found
            if cron:
                source = "config"
        if not cron and self._fallback:
            cron, source = self._fallback, "env"
        if cron:
            try:
                validate_cron(cron)
            except ValueError as exc:
                log.warning("%s; the schedule is off until it is fixed", exc)
                cron, source = None, "none"
        fire_at = next_fire(cron) if cron and enabled else None
        return SchedulePlan(cron=cron, enabled=enabled, source=source, next_fire_at=fire_at)

    async def run_forever(self, *, run_now: bool = False) -> None:
        """Loop until cancelled. Failures of ``run`` never stop it."""
        if run_now:
            await _guarded(self._run)

        while True:
            self._plan = plan = self._resolve()
            self._stale = False
            if plan.next_fire_at is not None and self._on_schedule is not None:
                self._on_schedule(plan.next_fire_at)
            wait = RECHECK_S if plan.next_fire_at is None else min(
                max(plan.next_fire_at - time.time(), 0.0), RECHECK_S
            )
            self._changed.clear()
            try:
                await asyncio.wait_for(self._changed.wait(), wait)
                continue  # settings changed: plan again
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                log.info("Scheduler stopped")
                raise
            if plan.next_fire_at is not None and time.time() >= plan.next_fire_at:
                await _guarded(self._run)


async def run_forever(
    expression: str,
    run: RunFn,
    *,
    run_now: bool = False,
    on_schedule: Callable[[float], None] | None = None,
) -> None:
    """Fire ``run`` on a fixed expression until cancelled (no live settings)."""
    scheduler = Scheduler(run, settings=lambda: None, fallback=expression, on_schedule=on_schedule)
    await scheduler.run_forever(run_now=run_now)


async def _guarded(run: RunFn) -> None:
    try:
        await run()
    except Exception:  # keep the loop alive across a broken run; CancelledError passes through
        log.exception("Scheduled run crashed")
