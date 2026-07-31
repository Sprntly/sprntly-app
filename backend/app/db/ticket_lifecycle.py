"""Per-ticket lifecycle: live, held back from the tracker, or deleted.

    active    the default — syncs to the bound tracker as it always has
    excluded  stays in Sprntly, is NEVER pushed; a tracker copy that already
              exists is removed, because the point of excluding a ticket is
              that it does not exist in the PM tool
    deleted   gone from Sprntly too, and removed from the tracker

Stored on `ticket_edits` (see 20260731120000_ticket_edits_lifecycle.sql), not
in `prd_tickets.stories`: stories is regenerated wholesale from the PRD, so a
flag written there would be erased by the next regeneration and silently
re-push a ticket the user had deleted.

Both non-active states mean the same thing to the tracker — the ticket must
not be there — and differ only in whether Sprntly still shows it. That is why
one column carries both rather than two independent booleans that could
contradict each other.
"""
from __future__ import annotations

import logging

from app.db.client import require_client, retry_on_disconnect, utc_now

logger = logging.getLogger(__name__)

ACTIVE = "active"
EXCLUDED = "excluded"
DELETED = "deleted"
LIFECYCLES = (ACTIVE, EXCLUDED, DELETED)

#: The states whose tickets must not exist in the tracker.
OFF_TRACKER = (EXCLUDED, DELETED)


def is_active(value: str | None) -> bool:
    """Whether a stored lifecycle value means "sync this ticket normally".
    NULL/missing reads as active — every row that predates the column, and
    every ticket never edited, behaves exactly as it does today."""
    return (value or ACTIVE) == ACTIVE


@retry_on_disconnect
def set_lifecycle(company_id: str, ticket_key: str, lifecycle: str) -> None:
    """Move one ticket to `lifecycle`, creating its ticket_edits row if needed.

    `updated_at` is bumped so the sync pass sees a fresh local change and acts
    on it now rather than at the next tick — the same signal an ordinary edit
    uses.
    """
    if lifecycle not in LIFECYCLES:
        raise ValueError(f"unknown lifecycle {lifecycle!r}")
    require_client().table("ticket_edits").upsert(
        {
            "company_id": company_id,
            "ticket_key": ticket_key,
            "lifecycle": lifecycle,
            "updated_at": utc_now(),
        },
        on_conflict="company_id,ticket_key",
    ).execute()


@retry_on_disconnect
def get_lifecycle(company_id: str, ticket_key: str) -> str:
    """One ticket's lifecycle; ACTIVE when it has no override row."""
    rows = (
        require_client().table("ticket_edits").select("lifecycle")
        .eq("company_id", company_id).eq("ticket_key", ticket_key)
        .limit(1).execute().data
        or []
    )
    return (rows[0].get("lifecycle") if rows else None) or ACTIVE


@retry_on_disconnect
def lifecycle_by_key(company_id: str, prd_id: int | None = None) -> dict[str, str]:
    """`{ticket_key: lifecycle}` for every NON-ACTIVE ticket, company-wide or
    for one PRD.

    Only the non-active rows are returned — they are the rare ones, and a
    caller's question is always "should I hide/skip this?", which an absent key
    answers with "no". Keeping the majority out of the map is what lets the
    ticket lists filter with one small query instead of joining every row.
    """
    q = (
        require_client().table("ticket_edits").select("ticket_key, lifecycle")
        .eq("company_id", company_id).neq("lifecycle", ACTIVE)
    )
    if prd_id is not None:
        q = q.like("ticket_key", f"prd-{prd_id}-%")
    return {
        r["ticket_key"]: r["lifecycle"]
        for r in (q.execute().data or [])
        if r.get("ticket_key") and not is_active(r.get("lifecycle"))
    }
