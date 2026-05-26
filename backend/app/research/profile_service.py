"""Service layer for competitor profiles + signals.

Every read/write is scoped to a `workspace_id` — the caller (route
handler) passes the workspace from the authenticated session, and the
helpers below refuse to return / mutate rows owned by a different
workspace. Cross-tenant access is a 404 (not 403) so the existence of
another workspace's row isn't leaked.

The HTTP layer turns these results into JSON; nothing in here knows
about FastAPI.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from app.db.client import require_client, utc_now
from app.research.profile import (
    CompetitorProfile,
    CompetitorProfileCreate,
    CompetitorProfileUpdate,
    CompetitorSignal,
    CompetitorSignalCreate,
)


# ── exceptions ────────────────────────────────────────────────────────


class ProfileNotFound(LookupError):
    """Profile doesn't exist OR exists but belongs to another workspace."""


class SignalRejected(ValueError):
    """The signal can't be persisted (future timestamp, etc.)."""


# ── internal helpers ─────────────────────────────────────────────────


def _row_to_profile(row: dict) -> CompetitorProfile:
    return CompetitorProfile(**row)


def _row_to_signal(row: dict) -> CompetitorSignal:
    # raw_payload_json may come back as None from a row with no payload
    # set explicitly; the model expects a dict.
    if row.get("raw_payload_json") is None:
        row = {**row, "raw_payload_json": {}}
    return CompetitorSignal(**row)


def _assert_owned(profile_row: dict | None, workspace_id: str) -> dict:
    """Return the row if it exists and belongs to workspace; else raise."""
    if not profile_row or profile_row.get("workspace_id") != workspace_id:
        raise ProfileNotFound("Profile not found")
    return profile_row


# ── profiles ─────────────────────────────────────────────────────────


def create_profile(
    workspace_id: str,
    data: CompetitorProfileCreate,
) -> CompetitorProfile:
    c = require_client()
    now = utc_now()
    payload = {
        "id": uuid.uuid4().hex,
        "workspace_id": workspace_id,
        **data.model_dump(),
        "created_at": now,
        "updated_at": now,
    }
    resp = c.table("competitor_profiles").insert(payload).execute()
    return _row_to_profile(resp.data[0])


def update_profile(
    workspace_id: str,
    profile_id: str,
    data: CompetitorProfileUpdate,
) -> CompetitorProfile:
    c = require_client()
    existing = (
        c.table("competitor_profiles")
        .select("*")
        .eq("id", profile_id)
        .limit(1)
        .execute()
        .data
    )
    row = _assert_owned(existing[0] if existing else None, workspace_id)

    patch = {k: v for k, v in data.model_dump(exclude_unset=True).items()}
    if not patch:
        return _row_to_profile(row)
    patch["updated_at"] = utc_now()
    c.table("competitor_profiles").update(patch).eq("id", profile_id).execute()

    refreshed = (
        c.table("competitor_profiles")
        .select("*")
        .eq("id", profile_id)
        .limit(1)
        .execute()
        .data
    )
    return _row_to_profile(refreshed[0])


def list_profiles(workspace_id: str) -> list[CompetitorProfile]:
    c = require_client()
    resp = (
        c.table("competitor_profiles")
        .select("*")
        .eq("workspace_id", workspace_id)
        .order("created_at", desc=True)
        .execute()
    )
    return [_row_to_profile(r) for r in (resp.data or [])]


def get_profile(workspace_id: str, profile_id: str) -> CompetitorProfile:
    c = require_client()
    resp = (
        c.table("competitor_profiles")
        .select("*")
        .eq("id", profile_id)
        .limit(1)
        .execute()
    )
    row = _assert_owned(resp.data[0] if resp.data else None, workspace_id)
    return _row_to_profile(row)


def delete_profile(workspace_id: str, profile_id: str) -> None:
    c = require_client()
    existing = (
        c.table("competitor_profiles")
        .select("*")
        .eq("id", profile_id)
        .limit(1)
        .execute()
        .data
    )
    _assert_owned(existing[0] if existing else None, workspace_id)
    c.table("competitor_profiles").delete().eq("id", profile_id).execute()


# ── signals ──────────────────────────────────────────────────────────


def record_signal(
    profile_id: str,
    signal_data: CompetitorSignalCreate,
) -> CompetitorSignal:
    """Persist a new signal under a profile.

    Dedup rules:
      - If `url` is set, the (profile_id, url) pair is unique; a repeat
        post returns the existing row instead of inserting.
      - If `url` is None, the (profile_id, source, title, published_at)
        tuple is the dedup key.

    Rejects signals whose `published_at` is in the future — that's
    almost certainly a clock skew or parse bug, not a real event.
    """
    now_utc = datetime.now(timezone.utc)
    published = signal_data.published_at
    # Pydantic gives us tz-aware datetimes; normalize if naive.
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    if published > now_utc:
        raise SignalRejected(
            f"Signal published_at {published.isoformat()} is in the future"
        )

    c = require_client()

    # Dedup check before insert. The DB has a partial unique index for
    # defense in depth, but we want a clean "return existing row" path
    # rather than catching an integrity error.
    existing_row = _find_duplicate_signal(profile_id, signal_data)
    if existing_row is not None:
        return _row_to_signal(existing_row)

    payload = {
        "id": uuid.uuid4().hex,
        "competitor_profile_id": profile_id,
        "source": signal_data.source,
        "signal_type": signal_data.signal_type,
        "title": signal_data.title,
        "body": signal_data.body,
        "url": signal_data.url,
        "sentiment": signal_data.sentiment,
        "published_at": published.isoformat(),
        "fetched_at": utc_now(),
        "raw_payload_json": signal_data.raw_payload_json,
    }
    resp = c.table("competitor_signals").insert(payload).execute()
    return _row_to_signal(resp.data[0])


def _find_duplicate_signal(
    profile_id: str,
    signal_data: CompetitorSignalCreate,
) -> dict | None:
    c = require_client()
    if signal_data.url:
        rows = (
            c.table("competitor_signals")
            .select("*")
            .eq("competitor_profile_id", profile_id)
            .eq("url", signal_data.url)
            .limit(1)
            .execute()
            .data
        )
        return rows[0] if rows else None
    # No URL — fall back to (source, title, published_at).
    published_iso = signal_data.published_at
    if isinstance(published_iso, datetime):
        if published_iso.tzinfo is None:
            published_iso = published_iso.replace(tzinfo=timezone.utc)
        published_iso = published_iso.isoformat()
    rows = (
        c.table("competitor_signals")
        .select("*")
        .eq("competitor_profile_id", profile_id)
        .eq("source", signal_data.source)
        .eq("title", signal_data.title)
        .eq("published_at", published_iso)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


def list_signals(
    profile_id: str,
    since: Optional[datetime] = None,
    source: Optional[str] = None,
) -> list[CompetitorSignal]:
    """Signals for a profile, newest first.

    `since` and `source` are server-side filters so callers (the digest
    job, the route handler) don't have to fetch + filter in Python.
    Tenant boundary is enforced by the route layer — service callers
    should have already resolved the profile via `get_profile`.
    """
    c = require_client()
    q = (
        c.table("competitor_signals")
        .select("*")
        .eq("competitor_profile_id", profile_id)
    )
    if source is not None:
        q = q.eq("source", source)
    rows = q.order("published_at", desc=True).execute().data or []
    if since is not None:
        since_aware = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
        filtered: list[dict] = []
        for r in rows:
            pub = r.get("published_at")
            if isinstance(pub, str):
                try:
                    pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                except ValueError:
                    continue
            elif isinstance(pub, datetime):
                pub_dt = pub if pub.tzinfo else pub.replace(tzinfo=timezone.utc)
            else:
                continue
            if pub_dt >= since_aware:
                filtered.append(r)
        rows = filtered
    return [_row_to_signal(r) for r in rows]
