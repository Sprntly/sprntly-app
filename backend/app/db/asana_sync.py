"""Ticket → Asana task gid mapping, backing idempotent pushes.

One row per (company, destination project gid, ticket) — see the
`asana_task_map` table. `ticket_id` is the Story's content-derived stable_id
(hash of title + body), the same id the Tickets tab keys per-ticket edits off,
so a re-push of an unchanged ticket resolves to the same Asana task rather than
creating a duplicate. A genuinely different ticket (edited title/body) hashes
differently and is created fresh — the intended behavior. Mirrors
app.db.clickup_sync / app.db.jira_sync.
"""
from __future__ import annotations

import logging

from app.db.client import require_client, retry_on_disconnect

logger = logging.getLogger(__name__)


@retry_on_disconnect
def get_asana_task_gid(company_id: str, project_gid: str, ticket_id: str) -> str | None:
    """The Asana task previously created for this ticket in this project, or None."""
    resp = (
        require_client().table("asana_task_map")
        .select("asana_task_gid")
        .eq("company_id", company_id).eq("project_gid", project_gid).eq("ticket_id", ticket_id)
        .limit(1).execute()
    )
    rows = resp.data or []
    return rows[0]["asana_task_gid"] if rows else None


@retry_on_disconnect
def save_asana_task_gid(
    company_id: str, project_gid: str, ticket_id: str, asana_task_gid: str
) -> None:
    """Upsert the ticket → Asana task mapping (unique per company+project+ticket)."""
    require_client().table("asana_task_map").upsert(
        {
            "company_id": company_id,
            "project_gid": project_gid,
            "ticket_id": ticket_id,
            "asana_task_gid": asana_task_gid,
        },
        on_conflict="company_id,project_gid,ticket_id",
    ).execute()
