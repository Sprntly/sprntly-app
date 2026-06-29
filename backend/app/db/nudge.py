"""DB helpers for brief-nudge delivery — idempotency ledger + brief-open state.

Two small, additive tables back the nudge cadence (see app.brief_nudge):

  brief_nudge_sends — one row per (company, user, brief, day_offset, channel)
                      that was sent, so a cadence step never double-sends on a
                      re-tick. UNIQUE on that tuple.
  brief_opens       — one row per (company, user, brief) the recipient opened,
                      so the Day 1/2/3 reminders fire ONLY while a brief is
                      still unopened (the skill's open-state gate).

Writes are best-effort: a tracking-row failure is logged and never raised, so
it can never break brief delivery (a side effect must not break the brief).
"""
from __future__ import annotations

import logging

from app.db.client import require_client, utc_now

logger = logging.getLogger(__name__)


def has_nudge_been_sent(
    company_id: str, user_id: str, brief_id: int, day_offset: int, channel: str
) -> bool:
    """True if this exact cadence step was already recorded as sent — the
    idempotency guard that keeps a re-tick from double-sending."""
    c = require_client()
    resp = (
        c.table("brief_nudge_sends")
        .select("id")
        .eq("company_id", company_id)
        .eq("user_id", user_id)
        .eq("brief_id", brief_id)
        .eq("day_offset", day_offset)
        .eq("channel", channel)
        .limit(1)
        .execute()
    )
    return bool(resp.data)


def record_nudge_sent(
    company_id: str,
    user_id: str,
    brief_id: int,
    day_offset: int,
    channel: str,
    status: str = "sent",
) -> None:
    """Record one cadence step as sent (or skipped). Best-effort; never raises."""
    c = require_client()
    try:
        c.table("brief_nudge_sends").insert(
            {
                "company_id": company_id,
                "user_id": user_id,
                "brief_id": brief_id,
                "day_offset": day_offset,
                "channel": channel,
                "status": status,
                "sent_at": utc_now(),
            }
        ).execute()
    except Exception:  # noqa: BLE001 — a ledger write must never break delivery
        logger.exception(
            "record_nudge_sent failed (company=%s brief=%s day=%s channel=%s)",
            company_id, brief_id, day_offset, channel,
        )


def mark_brief_opened(company_id: str, user_id: str, brief_id: int) -> None:
    """Mark a brief opened by a recipient (upsert) — stops further reminders.
    Best-effort; never raises."""
    c = require_client()
    try:
        c.table("brief_opens").upsert(
            {
                "company_id": company_id,
                "user_id": user_id,
                "brief_id": brief_id,
                "opened_at": utc_now(),
            },
            on_conflict="company_id,user_id,brief_id",
        ).execute()
    except Exception:  # noqa: BLE001
        logger.exception(
            "mark_brief_opened failed (company=%s user=%s brief=%s)",
            company_id, user_id, brief_id,
        )


def is_brief_unopened(company_id: str, user_id: str, brief_id: int) -> bool:
    """True when the recipient has NOT opened this brief. Reminders (Day 1+)
    only send while this is True. A query failure fails CLOSED (returns False →
    no reminder) so an outage can't spam an already-engaged user."""
    c = require_client()
    try:
        resp = (
            c.table("brief_opens")
            .select("id")
            .eq("company_id", company_id)
            .eq("user_id", user_id)
            .eq("brief_id", brief_id)
            .limit(1)
            .execute()
        )
        return not bool(resp.data)
    except Exception:  # noqa: BLE001 — fail closed: don't reminder on uncertainty
        logger.exception(
            "is_brief_unopened check failed (company=%s brief=%s) — failing closed",
            company_id, brief_id,
        )
        return False
