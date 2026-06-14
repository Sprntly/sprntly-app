"""Drip / nudge onboarding email persistence (v0 checklist 2.1).

Reads the data the drip scheduler needs (per-company notification settings,
members with their email + days-since-joining) and records each delivered
step into drip_email_sends so steps never double-send.

All access is via require_client() (service-role; the scheduler is a trusted
server process, not a browser).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from app.db.client import require_client, retry_on_disconnect, utc_now

logger = logging.getLogger(__name__)


def _parse_ts(value) -> datetime | None:
    """Parse an ISO-8601 timestamp (with or without 'Z') to aware UTC."""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _age_days(created_at) -> int | None:
    """Whole days between `created_at` and now (UTC). None if unparseable."""
    created = _parse_ts(created_at)
    if created is None:
        return None
    delta = datetime.now(timezone.utc) - created
    return max(0, delta.days)


@retry_on_disconnect
def get_notification_settings(company_id: str) -> dict:
    """Return companies.notification_settings (JSONB) for a company, or {}."""
    client = require_client()
    result = (
        client.table("companies")
        .select("notification_settings")
        .eq("id", company_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        return {}
    ns = result.data[0].get("notification_settings")
    return ns if isinstance(ns, dict) else {}


@retry_on_disconnect
def list_members_with_email(company_id: str) -> list[dict]:
    """Members of a company shaped for the drip cycle.

    Each row: {user_id, email, name, age_days}. `age_days` is whole days
    since the member's company_members.created_at (their effective join /
    signup time for this company). Members without a resolvable email or
    created_at are still returned but with email="" / age_days=None so the
    caller can skip them.
    """
    client = require_client()
    members = (
        client.table("company_members")
        .select("user_id, created_at")
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )
    if not members:
        return []

    user_ids = [m["user_id"] for m in members if m.get("user_id")]
    profiles = (
        client.table("profiles")
        .select("id, email, full_name, first_name, last_name")
        .in_("id", user_ids)
        .execute()
        .data
        or []
    ) if user_ids else []
    by_id = {p["id"]: p for p in profiles}

    out: list[dict] = []
    for m in members:
        uid = m.get("user_id")
        if not uid:
            continue
        prof = by_id.get(uid) or {}
        full = (prof.get("full_name") or "").strip()
        first = (prof.get("first_name") or "").strip()
        name = full or first or ""
        out.append(
            {
                "user_id": uid,
                "email": (prof.get("email") or "").strip(),
                "name": name,
                "age_days": _age_days(m.get("created_at")),
            }
        )
    return out


@retry_on_disconnect
def sent_steps_for_company(company_id: str) -> set[tuple[str, str]]:
    """All (user_id, step_key) pairs already recorded for a company.

    Includes both 'sent' and 'skipped' rows — a 'skipped' step (sending
    wasn't configured) is still considered delivered so enabling Resend
    later doesn't retro-blast historical steps."""
    client = require_client()
    rows = (
        client.table("drip_email_sends")
        .select("user_id, step_key")
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )
    return {(r["user_id"], r["step_key"]) for r in rows}


@retry_on_disconnect
def record_drip_sent(
    *,
    company_id: str,
    user_id: str,
    step_key: str,
    email: str,
    status: str = "sent",
) -> None:
    """Insert a drip_email_sends row. The UNIQUE (company_id, user_id,
    step_key) constraint makes this the idempotency guard: a duplicate
    insert (a racing/retried cycle) raises and is treated as already-sent
    by the caller, which has already filtered known steps."""
    client = require_client()
    client.table("drip_email_sends").insert(
        {
            "id": str(uuid.uuid4()),
            "company_id": company_id,
            "user_id": user_id,
            "step_key": step_key,
            "email": email,
            "status": status,
            "sent_at": utc_now(),
        }
    ).execute()
