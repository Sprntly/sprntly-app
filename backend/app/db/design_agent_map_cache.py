"""Durable L2 for the codebase-map cache.

A Supabase-backed second cache tier behind the process-local LRU in
``app.design_agent.codebase_map.service``. The LRU (L1) is wiped on every
deploy/restart, so today the first locate after a deploy re-pays the full cold
map build — one of the heavy operations that contends for CPU and contributes
to prod ``/locate`` 504s. This L2, keyed by ``(installation_id, repo,
commit_sha)``, survives a restart: a deploy no longer throws away a still-valid
map and the first post-deploy locate is warm.

Design notes
------------
* **Payload = jsonb.** The cached value is a ``MapResult`` (Pydantic v2 model).
  The service serializes it with ``model_dump(mode="json")`` and rehydrates with
  ``MapResult.model_validate(...)``, so the round-trip is lossless. We store the
  plain dict here; jsonb keeps it queryable and avoids an opaque blob.
* **TTL = 3600s (1h), longer than L1's 900s.** ``commit_sha`` keying is the real
  invalidation — a new commit is a new key, so a stale map is only reachable via
  the rare same-SHA force-push. The TTL is a backstop for that edge plus a hard
  staleness bound. It is deliberately *longer* than L1 because the entire point
  of L2 is to survive a deploy: a 20-minute-old map after a restart should still
  be served warm, which a 900s L2 TTL would defeat. ``DESIGN_AGENT_MAP_CACHE_TTL_SECONDS``
  can override it; an explicit ``0`` disables L2 reads (emergency kill switch).
* **Fail-soft is load-bearing.** EVERY DB call is guarded. A missing table, a DB
  outage, or any unexpected error logs a warning and returns ``None`` (get) or
  silently no-ops (put). The caller then behaves exactly as it does today
  (in-process LRU only). The durable layer is purely additive — it can never
  break locate.

This module is *synchronous* (supabase-py is a sync client) and uses
``require_client()`` mirroring the other ``app.db.*`` helpers; the async locate
route calls ``service.build_map`` via ``asyncio.to_thread``, so these sync calls
run off the event loop.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from app.db.client import require_client, utc_now

logger = logging.getLogger(__name__)

_TABLE = "design_agent_map_cache"

# L2 TTL: 1h default (see module docstring for why it is longer than L1's 900s).
# `0` disables L2 reads — an emergency kill switch that degrades to L1-only
# without a redeploy of the service module.
_DEFAULT_TTL_SECONDS = 3600


def _ttl_seconds() -> int:
    """L2 TTL, env-overridable. Read per-call so an ops override takes effect
    without a process restart. Falls back to the default on a malformed value."""
    raw = os.getenv("DESIGN_AGENT_MAP_CACHE_TTL_SECONDS")
    if raw is None:
        return _DEFAULT_TTL_SECONDS
    try:
        val = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "map_cache_l2 bad DESIGN_AGENT_MAP_CACHE_TTL_SECONDS=%r; using default %ds",
            raw, _DEFAULT_TTL_SECONDS,
        )
        return _DEFAULT_TTL_SECONDS
    return max(val, 0)


def _age_seconds(created_at: Any) -> float | None:
    """Seconds since `created_at` (an ISO-8601 string or datetime), or None if it
    cannot be parsed. None means 'treat as fresh' is NOT assumed — the caller
    treats an unparseable timestamp as expired (safe: a miss only costs a rebuild)."""
    if created_at is None:
        return None
    try:
        if isinstance(created_at, datetime):
            dt = created_at
        else:
            # Postgres timestamptz serializes as e.g. "2026-06-15T12:00:00+00:00"
            # or "...Z". Normalize the trailing Z for fromisoformat.
            s = str(created_at).replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except (TypeError, ValueError):
        return None


def get_cached_map(
    installation_id: int, repo: str, commit_sha: str,
) -> dict | None:
    """Return the cached map payload (a plain dict) for the key, or None.

    Returns None on: a real miss, an expired row (older than the L2 TTL), a
    disabled L2 (TTL=0), OR any DB error. The fail-soft guarantee: a None return
    is indistinguishable from 'not cached', so the caller simply rebuilds — never
    breaks. The DB is never allowed to raise out of this function.
    """
    ttl = _ttl_seconds()
    if ttl <= 0:
        return None  # L2 disabled via kill switch.
    try:
        c = require_client()
        resp = (
            c.table(_TABLE).select("*")
            .eq("installation_id", installation_id)
            .eq("repo", repo)
            .eq("commit_sha", commit_sha)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            logger.info(
                "map_cache_l2 repo=%s sha=%s l2=miss", repo, commit_sha,
            )
            return None
        row = rows[0]
        age = _age_seconds(row.get("created_at"))
        if age is None or age > ttl:
            logger.info(
                "map_cache_l2 repo=%s sha=%s l2=expired age=%s ttl=%d",
                repo, commit_sha, None if age is None else int(age), ttl,
            )
            return None
        logger.info(
            "map_cache_l2 repo=%s sha=%s l2=hit age=%d", repo, commit_sha, int(age),
        )
        return row.get("payload")
    except Exception:
        # Fail-soft: any DB error (missing table, outage, parse) degrades to a
        # miss. Locate proceeds on L1-only exactly as it does today.
        logger.warning(
            "map_cache_l2 repo=%s sha=%s l2 read failed; degrading to L1-only",
            repo, commit_sha, exc_info=True,
        )
        return None


def put_cached_map(
    installation_id: int, repo: str, commit_sha: str, payload: dict,
) -> None:
    """UPSERT the serialized map payload for the key. Fail-soft: any DB error
    logs a warning and no-ops, so a failed L2 write never breaks the build (the
    map is already in L1 and was returned to the caller).

    UPSERT on the unique (installation_id, repo, commit_sha): a same-SHA
    force-push refreshes the row in place + bumps updated_at, rather than
    accumulating duplicates.
    """
    try:
        c = require_client()
        now = utc_now()
        c.table(_TABLE).upsert(
            {
                "installation_id": installation_id,
                "repo": repo,
                "commit_sha": commit_sha,
                "payload": payload,
                "created_at": now,
                "updated_at": now,
            },
            on_conflict="installation_id,repo,commit_sha",
        ).execute()
        logger.info(
            "map_cache_l2 repo=%s sha=%s l2=write", repo, commit_sha,
        )
    except Exception:
        logger.warning(
            "map_cache_l2 repo=%s sha=%s l2 write failed; in-process cache unaffected",
            repo, commit_sha, exc_info=True,
        )


def sweep_expired_map_cache() -> int:
    """Opportunistic cleanup: delete rows older than the L2 TTL. Returns the
    number deleted (0 on disabled L2 or any DB error).

    Not on the request path — intended for a lifespan/cron hook (wiring is out of
    scope for this ticket; reads already filter by TTL so an unswept stale row is
    never served). Fail-soft like the rest: never raises.
    """
    ttl = _ttl_seconds()
    if ttl <= 0:
        return 0
    try:
        c = require_client()
        cutoff = (
            datetime.now(timezone.utc).timestamp() - ttl
        )
        cutoff_iso = (
            datetime.fromtimestamp(cutoff, tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
        )
        resp = (
            c.table(_TABLE).select("id")
            .lt("created_at", cutoff_iso)
            .execute()
        )
        ids = [r["id"] for r in (resp.data or [])]
        if ids:
            c.table(_TABLE).delete().in_("id", ids).execute()
            logger.info("map_cache_l2 swept count=%d ttl=%d", len(ids), ttl)
        return len(ids)
    except Exception:
        logger.warning("map_cache_l2 sweep failed", exc_info=True)
        return 0
