"""Persistence for the invite-reminder drip (Day-1 / Day-3 follow-ups).

Reads what the sweep needs — every pending workspace_invites row (across all
companies) enriched with the names the copy fills in — and records each sent
step into invite_reminder_sends so a step never double-sends and Day-3 can be
timed off Day-1's actual send.

All access is via require_client() (service-role; the scheduler is a trusted
server process, not a browser). Mirrors app/db/drip.py.
"""
from __future__ import annotations

import logging
import uuid

from app.db.client import require_client, retry_on_disconnect, utc_now

logger = logging.getLogger(__name__)


@retry_on_disconnect
def list_pending_invites_all_companies() -> list[dict]:
    """Every pending workspace_invites row, across all companies.

    Each row: {id, company_id, email, role, invited_by, created_at,
    workspace_ids}. "Pending" == the row exists (accept/revoke delete it), so
    this is exactly the set the reminder sweep should consider.
    """
    client = require_client()
    return (
        client.table("workspace_invites")
        .select("id, company_id, email, role, invited_by, created_at, workspace_ids")
        .execute()
        .data
        or []
    )


@retry_on_disconnect
def reminder_sends_by_invite(invite_ids: list[str]) -> dict[str, dict[str, str]]:
    """Map invite_id → {step_key: sent_at} for the given invites.

    Includes both 'sent' and 'skipped' rows — a skipped step still counts as
    delivered so flipping RESEND on later never retro-blasts. Empty input
    returns {} with no query.
    """
    ids = [i for i in dict.fromkeys(invite_ids) if i]
    if not ids:
        return {}
    rows = (
        require_client()
        .table("invite_reminder_sends")
        .select("invite_id, step_key, sent_at")
        .in_("invite_id", ids)
        .execute()
        .data
        or []
    )
    out: dict[str, dict[str, str]] = {}
    for r in rows:
        inv = r.get("invite_id")
        step = r.get("step_key")
        if not inv or not step:
            continue
        out.setdefault(inv, {})[step] = r.get("sent_at")
    return out


@retry_on_disconnect
def record_reminder_sent(
    *,
    invite_id: str,
    company_id: str,
    email: str,
    step_key: str,
    status: str = "sent",
) -> None:
    """Insert an invite_reminder_sends row. The UNIQUE (invite_id, step_key)
    constraint is the idempotency guard: a duplicate insert (a racing sweep)
    raises and is treated as already-sent by the caller, which has already
    filtered known steps."""
    require_client().table("invite_reminder_sends").insert(
        {
            "id": str(uuid.uuid4()),
            "invite_id": invite_id,
            "company_id": company_id,
            "email": email,
            "step_key": step_key,
            "status": status,
            "sent_at": utc_now(),
        }
    ).execute()


@retry_on_disconnect
def first_names_for_user_ids(user_ids: list[str | None]) -> dict[str, str]:
    """Map user_id → first name (first_name, else first token of full_name)
    from `profiles`. Unknown ids / no-name profiles are absent. For enriching
    each invite's inviter name."""
    ids = [u for u in dict.fromkeys(user_ids) if u]
    if not ids:
        return {}
    rows = (
        require_client()
        .table("profiles")
        .select("id, first_name, full_name")
        .in_("id", ids)
        .execute()
        .data
        or []
    )
    out: dict[str, str] = {}
    for p in rows:
        pid = p.get("id")
        if not pid:
            continue
        first = (p.get("first_name") or "").strip()
        if not first:
            full = (p.get("full_name") or "").strip()
            first = full.split()[0] if full else ""
        if first:
            out[pid] = first
    return out


@retry_on_disconnect
def first_names_for_emails(emails: list[str]) -> dict[str, str]:
    """Map lowercased email → first name from `profiles`, for the (often
    already-registered) invitee. A brand-new invitee has no profile yet, so
    their email is simply absent — the caller falls back to a friendly
    default."""
    needles = [e.strip().lower() for e in dict.fromkeys(emails) if e and e.strip()]
    if not needles:
        return {}
    rows = (
        require_client()
        .table("profiles")
        .select("email, first_name, full_name")
        .in_("email", needles)
        .execute()
        .data
        or []
    )
    out: dict[str, str] = {}
    for p in rows:
        email = (p.get("email") or "").strip().lower()
        if not email:
            continue
        first = (p.get("first_name") or "").strip()
        if not first:
            full = (p.get("full_name") or "").strip()
            first = full.split()[0] if full else ""
        if first:
            out[email] = first
    return out


@retry_on_disconnect
def display_names_for_company_ids(company_ids: list[str]) -> dict[str, str]:
    """Map company_id → display_name (the invitee-facing "workspace" name).
    Absent when the company has no display_name."""
    ids = [c for c in dict.fromkeys(company_ids) if c]
    if not ids:
        return {}
    rows = (
        require_client()
        .table("companies")
        .select("id, display_name")
        .in_("id", ids)
        .execute()
        .data
        or []
    )
    return {
        r["id"]: (r.get("display_name") or "").strip()
        for r in rows
        if r.get("id") and (r.get("display_name") or "").strip()
    }
