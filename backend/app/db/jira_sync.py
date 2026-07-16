"""Ticket → Jira issue mapping, backing idempotent pushes.

One row per (company, destination project, ticket) — see the `jira_issue_map`
table. `ticket_id` is the Story's content-derived stable_id (hash of title +
body), the same id the Tickets tab keys per-ticket edits off, so a re-push of an
unchanged ticket resolves to the same Jira issue rather than creating a
duplicate. A genuinely different ticket (edited title/body) hashes differently
and is created fresh — the intended behavior. Mirrors app.db.clickup_sync.
"""
from __future__ import annotations

import logging

from app.db.client import require_client, retry_on_disconnect

logger = logging.getLogger(__name__)


@retry_on_disconnect
def get_jira_issue_key(company_id: str, project_key: str, ticket_id: str) -> str | None:
    """The Jira issue previously created for this ticket in this project, or None."""
    resp = (
        require_client().table("jira_issue_map")
        .select("jira_issue_key")
        .eq("company_id", company_id).eq("project_key", project_key).eq("ticket_id", ticket_id)
        .limit(1).execute()
    )
    rows = resp.data or []
    return rows[0]["jira_issue_key"] if rows else None


@retry_on_disconnect
def save_jira_issue_key(
    company_id: str, project_key: str, ticket_id: str, jira_issue_key: str
) -> None:
    """Upsert the ticket → Jira issue mapping (unique per company+project+ticket)."""
    require_client().table("jira_issue_map").upsert(
        {
            "company_id": company_id,
            "project_key": project_key,
            "ticket_id": ticket_id,
            "jira_issue_key": jira_issue_key,
        },
        on_conflict="company_id,project_key,ticket_id",
    ).execute()


@retry_on_disconnect
def delete_jira_issue_key(company_id: str, project_key: str, ticket_id: str) -> None:
    """Drop the ticket's mapping AND its sub-task rows (`{ticket_id}#sub#…`).
    Called when the Jira issue was DELETED in the tracker so a re-sync treats
    the ticket (and its sub-tasks) as never-pushed and re-creates them cleanly
    — no orphan rows pointing at deleted issues."""
    cli = require_client()
    cli.table("jira_issue_map").delete().eq("company_id", company_id).eq(
        "project_key", project_key
    ).eq("ticket_id", ticket_id).execute()
    cli.table("jira_issue_map").delete().eq("company_id", company_id).eq(
        "project_key", project_key
    ).like("ticket_id", f"{ticket_id}#sub#%").execute()
