"""A per-client request ceiling for the public read surface.

The catalog, results and status endpoints are unauthenticated and database-backed, so without a
ceiling one client can drive the connection pool on their own. This is the ceiling: a sliding
window over request timestamps per client key.

**Sliding, not fixed.** A fixed window lets a client spend the whole budget in the last second
of one window and the whole budget again in the first second of the next, so the real
short-term rate is twice the configured one. A sliding window costs one deque per client and
does not have that edge.

**In-process, and that is a real limitation.** There is no shared store, so N replicas admit N
times the configured rate. That is a deliberate trade: the alternative is a Redis dependency in
`requirements-service.lock` and a new piece of deployment, and this is a read surface where the
purpose is to stop one client from monopolising a process rather than to meter a quota exactly.
Set `RATE_LIMIT_REQUESTS` to the per-replica share and put a shared limiter at the edge if an
exact global rate is ever required.

**The key table is bounded.** Client keys are attacker-chosen — one per source address — so an
unbounded dictionary is a memory exhaustion primitive. `max_clients` caps it, and the cap is
enforced by evicting the entries that have gone idle, which are exactly the ones whose windows
have expired anyway.

`Decision` carries the numbers the response headers report, so the middleware never recomputes
them and the headers cannot disagree with the verdict.
"""

from __future__ import annotations

import math
from collections import OrderedDict, deque
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Decision:
    allowed: bool
    limit: int
    remaining: int
    # Seconds until the budget next increases. Reported as `RateLimit-Reset`, and as
    # `Retry-After` when the request was refused.
    reset_seconds: int


@dataclass
class SlidingWindowLimiter:
    """Not thread-safe, and does not need to be.

    Uvicorn runs one event loop per worker process and `check` contains no await, so no two
    checks in a process interleave. Sharing one limiter across threads would need a lock.
    """

    limit: int
    window_seconds: int
    max_clients: int
    _hits: OrderedDict[str, deque[float]] = field(
        default_factory=OrderedDict, init=False, repr=False
    )

    def check(self, key: str, now: float) -> Decision:
        """Record a request against `key` and say whether it is admitted."""
        window = self._hits.get(key)
        if window is None:
            window = deque()
            self._hits[key] = window
        # Most-recently-seen last, so eviction below drops the coldest keys.
        self._hits.move_to_end(key)

        horizon = now - self.window_seconds
        while window and window[0] <= horizon:
            window.popleft()

        if len(window) >= self.limit:
            # Refused requests are not recorded. Counting them would extend the penalty every
            # time a client retried, which turns a burst into an unbounded lockout.
            return Decision(
                allowed=False,
                limit=self.limit,
                remaining=0,
                reset_seconds=_ceil_positive(window[0] + self.window_seconds - now),
            )

        window.append(now)
        self._evict(now)
        return Decision(
            allowed=True,
            limit=self.limit,
            remaining=self.limit - len(window),
            reset_seconds=_ceil_positive(window[0] + self.window_seconds - now),
        )

    def _evict(self, now: float) -> None:
        """Drop the coldest keys until the table is within its cap.

        Dropping a key forgets its window, so an evicted client starts with a full budget. That
        is the correct direction to fail: the cap exists to stop the limiter from becoming the
        denial of service, and the keys evicted first are the least recently active ones.
        """
        horizon = now - self.window_seconds
        while len(self._hits) > self.max_clients:
            key, window = next(iter(self._hits.items()))
            if window and window[-1] > horizon:
                # Every remaining key is inside its window: the cap is genuinely saturated by
                # active clients rather than by stale entries. Evicting an active client would
                # hand back a full budget, so stop and leave the table one over instead.
                break
            self._hits.popitem(last=False)

    @property
    def tracked_clients(self) -> int:
        return len(self._hits)


def _ceil_positive(seconds: float) -> int:
    """At least one second: a `Retry-After: 0` invites an immediate retry."""
    return max(1, math.ceil(seconds))


__all__ = ["Decision", "SlidingWindowLimiter"]
