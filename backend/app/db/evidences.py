"""Evidence pages — same shape as PRDs but for the Evidence generator.

Kept as a separate table because the two have different lifecycles
(evidence regenerates more often) and different templates.
"""
from app.db.client import require_client


def start_evidence(
    brief_id: int,
    insight_index: int,
    title: str,
    template_version: int | None = None,
    variant: str = "v1",
) -> int:
    c = require_client()
    resp = c.table("evidences").insert({
        "brief_id": brief_id,
        "insight_index": insight_index,
        "title": title,
        "payload_md": "",
        "status": "generating",
        "template_version": template_version,
        "variant": variant,
    }).execute()
    return resp.data[0]["id"]


def invalidate_stale_evidences(current_version: int, variant: str = "v1") -> int:
    c = require_client()
    rows = (
        c.table("evidences")
        .select("id, template_version")
        .in_("status", ["ready", "generating"])
        .eq("variant", variant)
        .execute()
        .data
    )
    stale_ids = [
        r["id"] for r in rows
        if r.get("template_version") is None or r["template_version"] != current_version
    ]
    if stale_ids:
        c.table("evidences").update({"status": "invalidated"}).in_("id", stale_ids).execute()
    return len(stale_ids)


def invalidate_orphan_generating_evidences() -> int:
    c = require_client()
    rows = c.table("evidences").select("id").eq("status", "generating").execute().data
    ids = [r["id"] for r in rows]
    if ids:
        c.table("evidences").update({"status": "invalidated"}).in_("id", ids).execute()
    return len(ids)


def complete_evidence(evidence_id: int, title: str, md: str) -> None:
    c = require_client()
    c.table("evidences").update({
        "title": title,
        "payload_md": md,
        "status": "ready",
        "error": None,
    }).eq("id", evidence_id).execute()


def fail_evidence(evidence_id: int, error: str) -> None:
    c = require_client()
    c.table("evidences").update({
        "status": "failed",
        "error": (error or "")[:500],
    }).eq("id", evidence_id).execute()


def get_evidence(evidence_id: int) -> dict | None:
    c = require_client()
    resp = c.table("evidences").select("*").eq("id", evidence_id).limit(1).execute()
    return resp.data[0] if resp.data else None


def find_existing_evidence(
    brief_id: int, insight_index: int, variant: str = "v1"
) -> dict | None:
    c = require_client()
    resp = (
        c.table("evidences")
        .select("*")
        .eq("brief_id", brief_id)
        .eq("insight_index", insight_index)
        .eq("variant", variant)
        .in_("status", ["ready", "generating"])
        .order("id", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None
