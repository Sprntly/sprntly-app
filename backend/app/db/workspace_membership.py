"""Workspace membership lookups.

The connectors slice needed the backend to start checking
"is this user actually allowed to act on this workspace?" before any
connector route touches `connections`. That check is a single query
against `company_members` — the table the rest of Supabase uses for
tenant membership.

This module exists so route-level dependencies (and any future
brief/PRD code that also needs to gate on workspace membership) have
one place to ask the question, instead of inlining the same supabase
query in five different files.
"""
from __future__ import annotations

from app.db.client import require_client


def is_member(*, user_id: str, workspace_id: str) -> bool:
    """True iff there's a `company_members` row linking (user_id, workspace_id).

    Role is intentionally ignored here — connector access doesn't have a
    finer-grained permission model yet. Any membership tier (owner /
    admin / member) is sufficient.
    """
    c = require_client()
    resp = (
        c.table("company_members")
        .select("id", count="exact")
        .eq("company_id", workspace_id)
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    return bool(resp.data)
