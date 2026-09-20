"""Cron-style scheduling, in-process.

Used by ``plex-auto-genres schedule`` and, in ``serve``, by the app itself so
one container runs both the web UI and the nightly job. The schedule is
*live*: :class:`Scheduler` re-reads its settings whenever they change (and
periodically anyway), so a cron expression edited in the config -- from the
UI or by hand -- or a pause takes effect without a restart.

Times are local. ``croniter`` interprets a bare timestamp as UTC, which would
make ``0 1 * * *`` fire at 01:00 UTC whatever ``TZ`` says, so every call here
hands it a timezone-aware ``datetime`` instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime

from croniter import croniter

log = logging.getLogger(__name__)

RunFn = Callable[[], Awaitable[None]]
#: How long the scheduler sleeps between looks at its settings while idle or
#: waiting for a distant fire time. Edits do not wait for it:
#: :meth:`Scheduler.replan` wakes the loop at once.
RECHECK_S = 60.0


def validate_cron(expression: str) -> None:
    """Raise ``ValueError`` for an expression this scheduler cannot use.

    ``croniter.is_valid`` only checks that the fields expand; an expression
    can pass and still never match a real date (``0 0 30 2 *``), which would
    raise later, in the loop. Both are refused here, at the edge.
    """
    if not croniter.is_valid(expression):
        raise ValueError(f"Invalid cron expression: {expression!r}")
    if _next_after(expression, _now()) is None:
        raise ValueError(f"Cron expression never matches a date: {expression!r}")


def next_fire(expression: str, now: float | None = None) -> float | None:
    """Unix timestamp of the next occurrence in local time, or ``None``.

    ``None`` means the expression is unusable (malformed, or it matches no
    date within croniter's horizon); callers treat that as "never fires".
    """
    return _next_after(expression, _now(now))


def next_fires(expression: str, count: int = 3, now: float | None = None) -> list[float]:
    """The next ``count`` occurrences, for previews. Empty when there are none."""
    out: list[float] = []
    start = _now(now)
    for _ in range(count):
        found = _next_after(expression, start)
        if found is None:
            break
        out.append(found)
        start = datetime.fromtimestamp(found).astimezone()
    return out


def _now(timestamp: float | None = None) -> datetime:
    """Local, timezone-aware. croniter reads a bare float as UTC."""
    if timestamp is None:
        return datetime.now().astimezone()
    return datetime.fromtimestamp(timestamp).astimezone()


def _next_after(expression: str, start: datetime) -> float | None:
    try:
        return croniter(expression, start).get_next(datetime).timestamp()
    except Exception as exc:  # malformed, or no matching date within the horizon
        log.debug("cron %r has no next occurrence: %s", expression, exc)
        return None


@dataclass(frozen=True, slots=True)
class SchedulePlan:
    """What the scheduler is currently set to do."""

    #: The effective cron expression, or ``None`` when nothing is scheduled.
    cron: str | None = None
    #: False when paused: the expression is kept but never fires.
    enabled: bool = True
    #: Where the expression came from: ``config`` (the file), ``env`` (the
    #: ``--cron`` flag / ``CRON_SCHEDULE``), or ``none``.
    source: str = "none"
    next_fire_at: float | None = None
    #: Set when the settings could not be read; the previous plan is kept.
    error: str | None = None


#: Returns ``(cron, enabled)`` from the config, or ``None`` when the config
#: has no schedule block. Raising means "cannot read it right now", which is
#: different: the scheduler then keeps the plan it already had.
SettingsFn = Callable[[], tuple[str | None, bool] | None]


class Scheduler:
    """Fire ``run`` on a cron expression that can change underneath it.

    The expression comes from the config file when it has one, else from the
    ``fallback`` given on the command line; the config's ``enabled`` flag can
    pause either. :attr:`plan` is what the API reports -- a plain read, never
    I/O; :meth:`replan` re-reads the settings and wakes the loop, and is what
    a config save calls.
    """

    def __init__(
        self,
        run: RunFn,
        *,
        settings: SettingsFn,
        fallback: str | None = None,
        on_plan: Callable[[SchedulePlan], None] | None = None,
    ) -> None:
        if fallback:
            validate_cron(fallback)
        self._run = run
        self._settings = settings
        self._fallback = fallback
        self._on_plan = on_plan
        self._changed = asyncio.Event()
        self._plan = self._resolve()

    @property
    def plan(self) -> SchedulePlan:
        """The current plan. A pure read: safe from a request handler."""
        return self._plan

    def replan(self) -> SchedulePlan:
        """Re-read the settings now and wake the loop. Never raises."""
        self._plan = self._resolve()
        self._changed.set()
        return self._plan

    def _resolve(self) -> SchedulePlan:
        """Work out the current plan. Never raises; never blocks for long."""
        cron: str | None = None
        enabled = True
        source = "none"
        try:
            found = self._settings()
        except Exception as exc:
            # Unreadable is not the same as unconfigured: a broken file must
            # not resurrect a schedule the operator paused. Keep what we had.
            previous = getattr(self, "_plan", None) or SchedulePlan()
            log.warning("Schedule settings unavailable (%s); keeping the last known plan", exc)
            return replace(previous, error=str(exc))
        if found is not None:
            cron, enabled = found
            if cron:
                source = "config"
        if not cron and self._fallback:
            cron, source = self._fallback, "env"

        fire_at = None
        if cron and enabled:
            fire_at = next_fire(cron)
            if fire_at is None:
                log.warning("Cron %r never fires; the schedule is off until it is fixed", cron)
        return SchedulePlan(cron=cron, enabled=enabled, source=source, next_fire_at=fire_at)

    async def run_forever(self, *, run_now: bool = False) -> None:
        """Loop until cancelled. Failures of ``run`` never stop it."""
        if run_now:
            # A paused schedule stays paused, restart or not.
            if self._plan.enabled:
                await _guarded(self._run)
            else:
                log.info("Skipping the start-up pass: the schedule is paused")

        while True:
            # Cleared first: a replan() that lands while we resolve still wakes us.
            self._changed.clear()
            self._plan = plan = self._resolve()
            if self._on_plan is not None:
                self._on_plan(plan)

            wait = RECHECK_S if plan.next_fire_at is None else min(
                max(plan.next_fire_at - time.time(), 0.0), RECHECK_S
            )
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
    """Fire ``run`` on a fixed expression until cancelled (no live settings).

    Raises ``ValueError`` for an expression that is malformed or never fires.
    """
    validate_cron(expression)

    def announce(plan: SchedulePlan) -> None:
        if on_schedule is not None and plan.next_fire_at is not None:
            on_schedule(plan.next_fire_at)

    scheduler = Scheduler(run, settings=lambda: None, fallback=expression, on_plan=announce)
    await scheduler.run_forever(run_now=run_now)


async def _guarded(run: RunFn) -> None:
    with contextlib.suppress(Exception):  # keep the loop alive across a broken run
        await run()
        return
    log.exception("Scheduled run crashed")
