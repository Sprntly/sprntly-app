"""Persistence for `delegation_followup_sends` — the idempotent per-company
send-ledger the autonomous task follow-up sweep (`app/delegation_followup.py`)
writes to. Mirrors `db/invite_reminders.py`'s record/read shape.

All access is via `require_client()` (service-role; the scheduler is a
trusted server process, not a browser). The `unique (delegation_id,
check_key, channel)` constraint on the table is the hard idempotency
guard — `record_send` does not swallow the resulting conflict itself
(callers pre-check with `send_exists` and are expected to treat a raised
insert as "already sent")."""
from __future__ import annotations

import uuid
from datetime import datetime

from app.db.client import require_client, retry_on_disconnect, utc_now


@retry_on_disconnect
def record_send(
    *,
    delegation_id: int,
    company_id: str,
    assignee_user_id: str,
    check_key: str,
    channel: str,
    status: str = "sent",
) -> dict:
    """Insert one send-ledger row. `channel` is one of 'dm' | 'email' |
    'escalation'; `status` is 'sent' | 'skipped' (a skipped row still
    counts as delivered for that check_key — no retro-blast if a
    transport later starts working). Raises on a duplicate
    `(delegation_id, check_key, channel)` — the caller pre-checks with
    `send_exists` and treats a race here as already-sent."""
    return (
        require_client()
        .table("delegation_followup_sends")
        .insert(
            {
                "id": str(uuid.uuid4()),
                "delegation_id": delegation_id,
                "company_id": company_id,
                "assignee_user_id": assignee_user_id,
                "check_key": check_key,
                "channel": channel,
                "status": status,
                "sent_at": utc_now(),
            }
        )
        .execute()
        .data[0]
    )


@retry_on_disconnect
def send_exists(delegation_id: int, check_key: str, channel: str) -> bool:
    """Whether a send-ledger row already exists for this exact
    `(delegation_id, check_key, channel)` — the sweep's idempotency
    pre-check (the unique constraint is the hard guard behind it)."""
    rows = (
        require_client()
        .table("delegation_followup_sends")
        .select("id")
        .eq("delegation_id", delegation_id)
        .eq("check_key", check_key)
        .eq("channel", channel)
        .limit(1)
        .execute()
        .data
        or []
    )
    return bool(rows)


@retry_on_disconnect
def sends_for_person_since(assignee_user_id: str, since: datetime) -> list[dict]:
    """Every send-ledger row for this assignee at/after `since`, across ALL
    their tasks (spec §2 — caps are per-PERSON, not per-task). The cap
    query: the caller counts `len(...)` for the daily/weekly windows.
    Scoped purely by `assignee_user_id`, which already isolates a
    different assignee's (and by extension a different company's, when
    the two never share a user) rows out."""
    return (
        require_client()
        .table("delegation_followup_sends")
        .select("*")
        .eq("assignee_user_id", assignee_user_id)
        .gte("sent_at", since.isoformat() if hasattr(since, "isoformat") else since)
        .execute()
        .data
        or []
    )


@retry_on_disconnect
def sends_for_delegation(delegation_id: int, channel: str | None = None) -> list[dict]:
    """Every send-ledger row for one delegation, oldest-first, optionally
    filtered to one `channel` — used by the sweep's unanswered-DM count
    (the email-escalation gate) and the escalation cycle count."""
    q = (
        require_client()
        .table("delegation_followup_sends")
        .select("*")
        .eq("delegation_id", delegation_id)
    )
    if channel is not None:
        q = q.eq("channel", channel)
    return q.order("sent_at").execute().data or []
