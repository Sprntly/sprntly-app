"""In-process TTL caches for the per-request auth/tenancy lookups.

Every authenticated request resolves user → company membership (and usually a
profile display name + workspace rows) before any handler logic runs. Each of
those is a serial remote PostgREST roundtrip; this module caches them
in-process, which is consistent because the deployment is a single uvicorn
process — every backend write goes through this process and can invalidate.

CRITICAL INVARIANT — memberships must NEVER cache empty results. Onboarding
inserts the company_members row from the BROWSER via supabase-js, so the
backend never sees that write and cannot invalidate on it. A cached empty
membership list would 403 a freshly-onboarded user ("complete onboarding
first") for a full TTL. `memberships_for_user` therefore only caches
non-empty results — an empty read stays a cache miss and is re-queried.

Thread-safety: callers span the event loop thread (async middleware) and the
FastAPI/anyio threadpool threads (sync dependencies + `asyncio.to_thread`
helpers), so every TTLMap operation holds a `threading.Lock`.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Hashable


class TTLMap:
    """Tiny hand-rolled TTL cache: monotonic-clock expiry, drop-all when the
    size bound is exceeded (no LRU bookkeeping — these maps hold at most a few
    thousand tiny rows and a full drop just re-warms on the next request)."""

    def __init__(self, ttl: float, maxsize: int = 2048) -> None:
        self.ttl = ttl
        self.maxsize = maxsize
        self._data: dict[Hashable, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Hashable) -> Any | None:
        """The cached value, or None when absent or past its TTL."""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if time.monotonic() >= expires_at:
                del self._data[key]
                return None
            return value

    def set(self, key: Hashable, value: Any) -> None:
        with self._lock:
            if len(self._data) >= self.maxsize and key not in self._data:
                self._data.clear()
            self._data[key] = (time.monotonic() + self.ttl, value)

    def invalidate(self, key: Hashable) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


# user_id → non-empty [{company_id, role}] (see the module-docstring invariant).
memberships_cache = TTLMap(30.0)
# user_id → profiles row ({full_name, first_name, last_name, email}). Cosmetic
# (display names only) and no backend endpoint writes profiles, so a longer TTL.
profile_name_cache = TTLMap(60.0)
# workspace_id → workspaces row.
workspace_cache = TTLMap(30.0)
# company_id → the company's default workspaces row.
default_ws_cache = TTLMap(30.0)
# (workspace_id, user_id) → workspace_members row, or the absent sentinel
# (app.db.workspaces owns the sentinel — all workspace-member writes go
# through backend routes, which invalidate, so caching absence is safe).
workspace_member_cache = TTLMap(30.0)


def invalidate_user(user_id: str) -> None:
    """Drop a user's cached membership + profile name (team-member mutations)."""
    memberships_cache.invalidate(user_id)
    profile_name_cache.invalidate(user_id)


def invalidate_workspace_caches() -> None:
    """Drop all three workspace maps (workspace / workspace-member mutations —
    coarse on purpose: these mutations are rare and re-warming is one read)."""
    workspace_cache.clear()
    default_ws_cache.clear()
    workspace_member_cache.clear()


def clear_all() -> None:
    """Reset every cache — for tests."""
    memberships_cache.clear()
    profile_name_cache.clear()
    workspace_cache.clear()
    default_ws_cache.clear()
    workspace_member_cache.clear()
