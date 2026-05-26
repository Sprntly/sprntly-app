"""Design Agent prototypes — backed by the `prototypes` + `prototype_comments`
tables in Supabase.

Spec source: Design_Agent_Spec.docx §4 (lifecycle), §5 (comments).

The Prototype object's `output_payload` (the generator's JSON skeleton)
is stored as jsonb so the route can return it without re-encoding.
Comments live in a sibling table so the read path can paginate +
filter; the lifecycle module joins them on read into a Prototype model.
"""
from __future__ import annotations

import json
from typing import Any

from app.db.client import require_client, utc_now


# ─────────────────────── prototypes ───────────────────────


def insert_prototype(
    *,
    prototype_id: str,
    workspace_id: str,
    artifact_id: str,
    status: str,
    inputs: dict[str, Any],
    output_payload: dict[str, Any],
    output_url: str | None = None,
) -> dict:
    """Insert a brand-new prototype row. Returns the inserted dict."""
    now = utc_now()
    c = require_client()
    resp = c.table("prototypes").insert({
        "id": prototype_id,
        "workspace_id": workspace_id,
        "artifact_id": artifact_id,
        "status": status,
        "inputs": inputs,
        "output_payload": output_payload,
        "output_url": output_url,
        "created_at": now,
        "updated_at": now,
    }).execute()
    return resp.data[0]


def get_prototype(prototype_id: str) -> dict | None:
    c = require_client()
    resp = (
        c.table("prototypes")
        .select("*")
        .eq("id", prototype_id)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def update_prototype(
    prototype_id: str,
    *,
    status: str | None = None,
    output_payload: dict[str, Any] | None = None,
    output_url: str | None = None,
    completed_at: str | None = None,
    exported_at: str | None = None,
) -> dict | None:
    """Patch any subset of mutable fields. Always bumps updated_at."""
    patch: dict[str, Any] = {"updated_at": utc_now()}
    if status is not None:
        patch["status"] = status
    if output_payload is not None:
        patch["output_payload"] = output_payload
    if output_url is not None:
        patch["output_url"] = output_url
    if completed_at is not None:
        patch["completed_at"] = completed_at
    if exported_at is not None:
        patch["exported_at"] = exported_at
    c = require_client()
    c.table("prototypes").update(patch).eq("id", prototype_id).execute()
    return get_prototype(prototype_id)


# ─────────────────────── prototype_comments ───────────────────────


def insert_prototype_comment(
    *,
    comment_id: str,
    prototype_id: str,
    author_user_id: str,
    section_id: str,
    text: str,
    classification: str | None,
    created_at: str,
) -> dict:
    c = require_client()
    resp = c.table("prototype_comments").insert({
        "id": comment_id,
        "prototype_id": prototype_id,
        "author_user_id": author_user_id,
        "section_id": section_id,
        "text": text,
        "classification": classification,
        "resolved": False,
        "created_at": created_at,
    }).execute()
    return resp.data[0]


def list_prototype_comments(prototype_id: str) -> list[dict]:
    c = require_client()
    resp = (
        c.table("prototype_comments")
        .select("*")
        .eq("prototype_id", prototype_id)
        .order("created_at", desc=False)
        .execute()
    )
    return resp.data or []


def mark_comments_resolved(prototype_id: str) -> int:
    """Mark every unresolved comment on a prototype as resolved.

    Called from iterate_prototype after a regen consumes the pending
    comments. Returns the count of rows updated (best-effort — the
    PostgREST update doesn't return a count, so we read then update).
    """
    c = require_client()
    rows = (
        c.table("prototype_comments")
        .select("id")
        .eq("prototype_id", prototype_id)
        .eq("resolved", False)
        .execute()
        .data
        or []
    )
    if not rows:
        return 0
    ids = [r["id"] for r in rows]
    c.table("prototype_comments").update({"resolved": True}).in_("id", ids).execute()
    return len(ids)


def patch_comment_classification(comment_id: str, classification: str) -> None:
    c = require_client()
    c.table("prototype_comments").update(
        {"classification": classification}
    ).eq("id", comment_id).execute()


__all__ = [
    "insert_prototype",
    "get_prototype",
    "update_prototype",
    "insert_prototype_comment",
    "list_prototype_comments",
    "mark_comments_resolved",
    "patch_comment_classification",
]
