"""Client-side rate limiting.

Free-tier quotas are the binding constraint on ingestion throughput, not CPU or
bandwidth. Staying under them deliberately is what turns a large document into
a slow job rather than a failed one.

A token-bucket limiter smooths bursts, and retries with jittered exponential
backoff handle the 429s that still slip through -- concurrent workers sharing
one quota will occasionally race past the local limiter.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RateLimiter:
    """Async token bucket, refilled continuously at `rate_per_minute`.

    Continuous refill rather than a fixed window: a window lets the whole
    minute's quota fire in one burst, which is exactly the shape that trips a
    provider's own limiter.
    """

    def __init__(self, rate_per_minute: int, burst: int | None = None) -> None:
        if rate_per_minute <= 0:
            raise ValueError("rate_per_minute must be positive")
        self._rate = rate_per_minute / 60.0
        default_burst = max(rate_per_minute // 4, 1)
        self._capacity = float(burst if burst is not None else default_burst)
        self._tokens = self._capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, cost: float = 1.0) -> None:
        if cost > self._capacity:
            # Never wait forever for a cost the bucket can never hold.
            cost = self._capacity

        while True:
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity, self._tokens + (now - self._updated) * self._rate
                )
                self._updated = now

                if self._tokens >= cost:
                    self._tokens -= cost
                    return

                deficit = cost - self._tokens
                wait = deficit / self._rate

            await asyncio.sleep(min(wait, 5.0))


class RateLimitError(Exception):
    """The provider reported 429. Carries the server's retry hint when given."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class TransientError(Exception):
    """A 5xx or network failure. Worth retrying; not the caller's fault."""


class QuotaExhaustedError(Exception):
    """The provider's allowance for the day is spent.

    Deliberately not retried: `with_retries` lets it through at once, because
    no amount of waiting inside one request will bring the quota back.
    """


async def with_retries(
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int = 5,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    description: str = "request",
) -> T:
    """Retries on rate limits and transient failures, with jittered backoff.

    Jitter matters when several batches are in flight: without it they retry in
    lockstep and collide again on exactly the same schedule.
    """
    last: Exception | None = None

    for attempt in range(attempts):
        try:
            return await operation()
        except RateLimitError as exc:
            last = exc
            # Honour the server's own hint when it gives one.
            delay = (
                exc.retry_after
                if exc.retry_after is not None
                else base_delay * (2**attempt)
            )
        except TransientError as exc:
            last = exc
            delay = base_delay * (2**attempt)

        if attempt == attempts - 1:
            break

        delay = min(delay, max_delay) * (0.75 + random.random() * 0.5)
        logger.warning(
            "%s failed (attempt %s/%s): %s -- retrying in %.1fs",
            description,
            attempt + 1,
            attempts,
            last,
            delay,
        )
        await asyncio.sleep(delay)

    raise RuntimeError(
        f"{description} failed after {attempts} attempts: {last}"
    ) from last
