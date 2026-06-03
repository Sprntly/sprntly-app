"""Generic process-local sliding-window rate limiter (P5-04).

A reusable primitive — `SlidingWindowLimiter(max_events, window_seconds)` keyed by
an arbitrary string — extracted so multiple Design Agent surfaces can share one
limiter shape:

  - P5-04 consumes `ITERATE_LIMITER` (6 iterate-calls/hr/prototype) on POST /iterate.
  - P5-07 will consume the same class for the public-token / per-IP comment limits.

This generalises the per-token passcode limiter already living in
`app/db/prototypes.py` (`passcode_rate_limit_check`): timestamp list per key,
`Lock`-guarded, pruned on each check, `time.monotonic()` so a wall-clock
adjustment cannot skew the window. That earlier limiter is deliberately NOT
refactored onto this primitive in P5-04 (future consolidation, out of scope).

NOT distributed. The window is process-local: under horizontal scaling each
worker keeps its own counts, so the effective limit is `max_events × workers`.
For the 2-week single-uvicorn-worker build this is correct; the swap-to-Redis
path (replace the `dict` + `Lock` with a Redis sorted-set per key) is a P6-01
handoff line.
"""
from __future__ import annotations

import time
from threading import Lock


class SlidingWindowLimiter:
    """Process-local in-memory sliding-window rate limiter keyed by an arbitrary
    string. Generic over (max_events, window_seconds). Lock-guarded for the
    FastAPI TestClient / threaded-uvicorn case. Mirrors the passcode-limiter
    discipline (prune-on-check, monotonic timestamps). NOT distributed — see the
    module docstring for the Redis-migration path."""

    def __init__(self, max_events: int, window_seconds: int) -> None:
        self._max = max_events
        self._window = window_seconds
        self._events: dict[str, list[float]] = {}
        self._lock = Lock()

    def check(self, key: str) -> bool:
        """True iff `key` has < max_events in the last window_seconds. Prunes
        expired timestamps as a side effect. Does NOT record an event."""
        now = time.monotonic()
        with self._lock:
            fresh = [t for t in self._events.get(key, []) if now - t < self._window]
            self._events[key] = fresh
            return len(fresh) < self._max

    def register(self, key: str) -> None:
        """Record one event for `key` at the current monotonic time."""
        with self._lock:
            self._events.setdefault(key, []).append(time.monotonic())

    def retry_after(self, key: str) -> int:
        """Seconds until the OLDEST in-window event for `key` expires (i.e. when
        the next call would be admitted). 0 when under the limit."""
        now = time.monotonic()
        with self._lock:
            fresh = [t for t in self._events.get(key, []) if now - t < self._window]
            if len(fresh) < self._max:
                return 0
            oldest = min(fresh)
            return max(1, int(self._window - (now - oldest)))


# Module-level singleton consumed by POST /iterate (P5-04): 6 iterate-calls per
# hour per prototype; the 7th in-window call is rate-limited. Kept generic — do
# NOT hardcode anything iterate-specific into the class above; P5-07 instantiates
# its own SlidingWindowLimiter for the public-surface limits.
ITERATE_LIMITER = SlidingWindowLimiter(max_events=6, window_seconds=3600)

# Module-level singletons consumed by the unauthenticated public share surface
# (P5-07). Both REUSE the SlidingWindowLimiter primitive above — no new limiter
# logic is introduced; they differ only in (max_events, window_seconds) and in the
# key the route hands them:
#
#   PUBLIC_TOKEN_LIMITER  — GET /by-token/{token}: 60 requests/min PER TOKEN.
#       Throttles a cheap brute-scan of one share token's view endpoint. Keyed by
#       the token string (the token IS the public identity); the 61st in-window
#       request returns 429. Keyed per-token because the view is per-share.
#   PUBLIC_COMMENT_LIMITER — POST /by-token/{token}/comments: 10 comments/hour PER
#       IP. Spam throttle on the anonymous public write. Keyed by the client IP —
#       the same machine can comment across many tokens, so per-IP (not per-token)
#       is the spam boundary; the 11th in-window comment returns 429.
PUBLIC_TOKEN_LIMITER = SlidingWindowLimiter(max_events=60, window_seconds=60)
PUBLIC_COMMENT_LIMITER = SlidingWindowLimiter(max_events=10, window_seconds=3600)
