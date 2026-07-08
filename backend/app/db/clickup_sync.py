"""Ticket → ClickUp task id mapping, backing idempotent pushes.

One row per (company, destination list, ticket) — see the `clickup_task_map`
table. `ticket_id` is the Story's content-derived stable_id (hash of title +
body), the same id the Tickets tab keys per-ticket edits off, so a re-push of an
unchanged ticket resolves to the same ClickUp task and UPDATEs it rather than
creating a duplicate. A genuinely different ticket (edited title/body) hashes
differently and is created fresh — the intended behavior.
"""
from __future__ import annotations

import logging

from app.db.client import require_client, retry_on_disconnect

logger = logging.getLogger(__name__)


@retry_on_disconnect
def get_clickup_task_id(company_id: str, list_id: str, ticket_id: str) -> str | None:
    """The ClickUp task previously created for this ticket in this list, or None."""
    resp = (
        require_client().table("clickup_task_map")
        .select("clickup_task_id")
        .eq("company_id", company_id).eq("list_id", list_id).eq("ticket_id", ticket_id)
        .limit(1).execute()
    )
    rows = resp.data or []
    return rows[0]["clickup_task_id"] if rows else None


@retry_on_disconnect
def save_clickup_task_id(
    company_id: str, list_id: str, ticket_id: str, clickup_task_id: str
) -> None:
    """Upsert the ticket → ClickUp task mapping (unique per company+list+ticket)."""
    require_client().table("clickup_task_map").upsert(
        {
            "company_id": company_id,
            "list_id": list_id,
            "ticket_id": ticket_id,
            "clickup_task_id": clickup_task_id,
        },
        on_conflict="company_id,list_id,ticket_id",
    ).execute()
