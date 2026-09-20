"""Async rate limiting.

v1 slept a flat 4 seconds before every Jikan call -- twice per anime, so 8
seconds of guaranteed dead time per title regardless of how fast the API
actually answered. This replaces that with token buckets that model the
provider's real published limits, which lets requests run concurrently right up
to the allowance and no further.
"""

from __future__ import annotations

import asyncio
import time
import weakref
from dataclasses import dataclass


class TokenBucket:
    """A single leaky-bucket limiter.

    ``rate`` tokens are added per ``period`` seconds, up to ``burst`` in stock.
    :meth:`acquire` waits until a token is free, then takes it.
    """

    __slots__ = ("_burst", "_lock", "_period", "_rate", "_tokens", "_updated")

    def __init__(self, rate: float, period: float = 1.0, burst: float | None = None) -> None:
        if rate <= 0 or period <= 0:
            raise ValueError("rate and period must be positive")
        self._rate = rate
        self._period = period
        self._burst = burst if burst is not None else rate
        self._tokens = float(self._burst)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._updated
        if elapsed > 0:
            self._tokens = min(self._burst, self._tokens + elapsed * (self._rate / self._period))
            self._updated = now

    async def acquire(self) -> None:
        """Wait until a token is available, then consume it."""
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                deficit = 1.0 - self._tokens
                wait = deficit * (self._period / self._rate)
            # Sleep outside the lock so other tasks can still refill-check.
            await asyncio.sleep(max(wait, 0.01))


class CompositeLimiter:
    """Several buckets that must all allow a request (e.g. 3/s *and* 60/min)."""

    __slots__ = ("_buckets", "_penalty_lock", "_penalty_until")

    def __init__(self, *buckets: TokenBucket) -> None:
        self._buckets = buckets
        self._penalty_until = 0.0
        self._penalty_lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until every bucket allows a request."""
        # Honour any server-requested cooldown before spending tokens.
        while True:
            async with self._penalty_lock:
                remaining = self._penalty_until - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(remaining, 5.0))
        for bucket in self._buckets:
            await bucket.acquire()

    async def penalise(self, seconds: float) -> None:
        """Back off for ``seconds`` after the provider returned HTTP 429."""
        async with self._penalty_lock:
            self._penalty_until = max(self._penalty_until, time.monotonic() + max(seconds, 0.0))


@dataclass(frozen=True, slots=True)
class LimitSpec:
    """Published limits for a provider, as (requests, per_seconds) pairs."""

    windows: tuple[tuple[float, float], ...]

    def build(self) -> CompositeLimiter:
        return CompositeLimiter(*(TokenBucket(rate=n, period=p, burst=n) for n, p in self.windows))


#: One limiter per provider per event loop. A provider's quota is per
#: *process*, not per pool: a running job and the binding picker's searches
#: draw on the same allowance, and a 429 cooldown applies to both.
_SHARED: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, CompositeLimiter]]" = (
    weakref.WeakKeyDictionary()
)


def shared_limiter(name: str, spec: LimitSpec) -> CompositeLimiter:
    """The process-wide limiter for ``name`` on the running loop (fresh if none)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return spec.build()
    per_loop = _SHARED.setdefault(loop, {})
    limiter = per_loop.get(name)
    if limiter is None:
        limiter = per_loop[name] = spec.build()
    return limiter


#: https://docs.api.jikan.moe/#section/Information/Rate-Limiting -- 3/s, 60/min.
JIKAN_LIMITS = LimitSpec(((3, 1.0), (60, 60.0)))
#: AniList allows 90 requests/minute on the public endpoint.
ANILIST_LIMITS = LimitSpec(((90, 60.0),))
#: TMDB removed its hard cap but still throttles; ~40/s is comfortably safe.
TMDB_LIMITS = LimitSpec(((40, 1.0),))
