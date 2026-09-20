"""Token bucket behaviour."""

from __future__ import annotations

import asyncio
import time

import pytest

from plex_auto_genres.ratelimit import CompositeLimiter, LimitSpec, TokenBucket


async def test_burst_is_allowed_immediately():
    bucket = TokenBucket(rate=5, period=1.0)
    start = time.monotonic()
    await asyncio.gather(*(bucket.acquire() for _ in range(5)))
    assert time.monotonic() - start < 0.2


async def test_requests_beyond_the_burst_are_paced():
    bucket = TokenBucket(rate=10, period=1.0, burst=2)
    start = time.monotonic()
    for _ in range(4):
        await bucket.acquire()
    # 2 free, then 2 more at 10/s -> at least ~0.2s.
    assert time.monotonic() - start >= 0.15


async def test_composite_requires_every_window():
    limiter = CompositeLimiter(TokenBucket(100, 1.0), TokenBucket(2, 1.0, burst=2))
    start = time.monotonic()
    for _ in range(3):
        await limiter.acquire()
    assert time.monotonic() - start >= 0.3


async def test_penalty_delays_the_next_acquire():
    limiter = LimitSpec(((100, 1.0),)).build()
    await limiter.penalise(0.3)
    start = time.monotonic()
    await limiter.acquire()
    assert time.monotonic() - start >= 0.25


def test_invalid_rates_are_rejected():
    with pytest.raises(ValueError):
        TokenBucket(rate=0)
    with pytest.raises(ValueError):
        TokenBucket(rate=1, period=0)


async def test_concurrency_is_actually_concurrent():
    """Ten calls at 10/s should take ~1s, not 10x the per-call latency."""
    limiter = LimitSpec(((20, 1.0),)).build()

    async def call():
        await limiter.acquire()
        await asyncio.sleep(0.1)   # simulated network latency

    start = time.monotonic()
    await asyncio.gather(*(call() for _ in range(10)))
    elapsed = time.monotonic() - start
    assert elapsed < 0.5, f"expected overlap, took {elapsed:.2f}s"
