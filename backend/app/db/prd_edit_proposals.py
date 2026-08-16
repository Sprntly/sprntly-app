"""Transient store for the confirmation gate on project PRD edits.

A `prd_edit_proposals` row holds an already-computed PRD-edit patch
(`proposed_html`/`proposed_title`) plus the original edit `instruction`,
keyed by an opaque single-use token. It stores an INPUT (the computed patch),
never derived state — confirm commits exactly this stored patch so the applied
content is byte-identical to what was proposed, with no second edit pass.

Two security properties live IN these helpers, not as caller parentheticals:

  - Tenant scoping: every lookup/delete filters `company_id` (and `get_proposal`
    also `workspace_id`) so a cross-tenant token matches zero rows — the token
    is caller-supplied and untrusted, so the first lookup must be tenant-scoped
    on the row's own columns, not through a derived project_id.

  - Expiry + single-use: `get_proposal` filters `expires_at > now()` so an
    expired row is never returned (dead to apply, never silently reusable), and
    `apply` deletes the row before committing so a replay of a consumed token
    finds nothing.

All access is via `require_client()` (service-role; server-side only).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.client import require_client, retry_on_disconnect, utc_now

# How long a minted proposal stays confirmable. Short by design: the token is
# single-use and the row is transient; a stale proposal is dead weight, and a
# tight window bounds the replay/confirm surface. The `expires_at > now()`
# filter in `get_proposal` makes an aged row unusable without a sweep.
PROPOSAL_TTL_SECONDS = 30 * 60


def _expires_at() -> str:
    """ISO-8601 UTC expiry `PROPOSAL_TTL_SECONDS` from now, second precision —
    same format as `utc_now()` so string-vs-string comparison in `get_proposal`
    stays chronological in both Postgres and the sqlite test mirror."""
    return (
        datetime.now(timezone.utc) + timedelta(seconds=PROPOSAL_TTL_SECONDS)
    ).replace(microsecond=0).isoformat()


@retry_on_disconnect
def create_proposal(
    *,
    token: str,
    prd_id: int,
    project_id: int,
    conversation_id: int | None,
    surface: str,
    company_id: str,
    workspace_id: str,
    instruction: str,
    base_html: str,
    proposed_title: str | None,
    proposed_html: str,
    summary: str | None,
    sections_changed: list | None,
    client_message_id: str | None,
) -> dict:
    """Insert one proposal row and return it. `expires_at` is set here from
    `PROPOSAL_TTL_SECONDS` — a caller never chooses the window."""
    client = require_client()
    return (
        client.table("prd_edit_proposals")
        .insert(
            {
                "token": token,
                "prd_id": prd_id,
                "project_id": project_id,
                "conversation_id": conversation_id,
                "surface": surface,
                "company_id": company_id,
                "workspace_id": workspace_id,
                "instruction": instruction,
                "base_html": base_html,
                "proposed_title": proposed_title,
                "proposed_html": proposed_html,
                "summary": summary,
                "sections_changed": sections_changed,
                "client_message_id": client_message_id,
                "expires_at": _expires_at(),
            }
        )
        .execute()
        .data[0]
    )


@retry_on_disconnect
def get_proposal(token: str, company_id: str, workspace_id: str) -> dict | None:
    """Return the LIVE proposal for `token`, or None. Tenant-scoped AND
    expiry-filtered in the WHERE clause: a token from another tenant, or one
    whose `expires_at` is already in the past, matches zero rows — never a
    row the caller must then re-check. This is the single choke point that
    keeps an expired or cross-tenant token from ever reaching apply."""
    client = require_client()
    rows = (
        client.table("prd_edit_proposals")
        .select("*")
        .eq("token", token)
        .eq("company_id", company_id)
        .eq("workspace_id", workspace_id)
        .gt("expires_at", utc_now())
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


@retry_on_disconnect
def delete_proposal(token: str, company_id: str) -> bool:
    """Delete a proposal (single-use consume, or cancel). The `company_id`
    filter is IN the query so a cross-tenant delete matches zero rows.
    Returns True when a row was removed."""
    client = require_client()
    resp = (
        client.table("prd_edit_proposals")
        .delete()
        .eq("token", token)
        .eq("company_id", company_id)
        .execute()
    )
    return bool(resp.count)
