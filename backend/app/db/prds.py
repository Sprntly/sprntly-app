"""PRDs — backed by the `prds` table in Supabase.

One row per generation attempt for a (brief_id, insight_index, variant).
`status` walks generating → ready (or failed/invalidated).
"""
import logging

from app.db.client import require_client

logger = logging.getLogger(__name__)


def save_prd(brief_id: int, insight_index: int, title: str, md: str) -> int:
    """Insert a complete PRD (sync flow). Status='ready'."""
    c = require_client()
    resp = c.table("prds").insert({
        "brief_id": brief_id,
        "insight_index": insight_index,
        "title": title,
        "payload_md": md,
        "status": "ready",
    }).execute()
    return resp.data[0]["id"]


def start_prd(
    brief_id: int,
    insight_index: int,
    title: str,
    template_version: int | None = None,
    variant: str = "v1",
) -> int:
    """Insert an empty PRD row in 'generating' state. Returns the new id."""
    c = require_client()
    resp = c.table("prds").insert({
        "brief_id": brief_id,
        "insight_index": insight_index,
        "title": title,
        "payload_md": "",
        "status": "generating",
        "template_version": template_version,
        "variant": variant,
    }).execute()
    return resp.data[0]["id"]


def invalidate_stale_prds(current_version: int, variant: str = "v1") -> int:
    """Variant-scoped: mark any ready/generating PRD (of this variant)
    whose template_version differs from current_version as 'invalidated'.
    Returns affected row count.
    """
    c = require_client()
    # Find candidates first (PostgREST doesn't expose a single SQL
    # statement with a NULL-or-not-equal predicate, so we filter here).
    rows = (
        c.table("prds")
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
        c.table("prds").update({"status": "invalidated"}).in_("id", stale_ids).execute()
    return len(stale_ids)


def invalidate_orphan_generating_prds() -> int:
    """Mark every status='generating' PRD as 'invalidated'.

    Called from lifespan startup: any in-flight row is orphaned because
    the worker that was generating it died with the previous process.
    """
    c = require_client()
    rows = c.table("prds").select("id").eq("status", "generating").execute().data
    ids = [r["id"] for r in rows]
    if ids:
        c.table("prds").update({"status": "invalidated"}).in_("id", ids).execute()
    return len(ids)


def complete_prd(prd_id: int, title: str, md: str) -> None:
    c = require_client()
    c.table("prds").update({
        "title": title,
        "payload_md": md,
        "status": "ready",
        "error": None,
    }).eq("id", prd_id).execute()


def fail_prd(prd_id: int, error: str) -> None:
    c = require_client()
    c.table("prds").update({
        "status": "failed",
        "error": (error or "")[:500],
    }).eq("id", prd_id).execute()


def get_prd(prd_id: int) -> dict | None:
    c = require_client()
    resp = c.table("prds").select("*").eq("id", prd_id).limit(1).execute()
    return resp.data[0] if resp.data else None


def get_prd_rendered(prd_id: int) -> dict | None:
    """Canonical PRD read: the raw row with status='applied' prd_patches folded
    into payload_md at read time (F11 render-on-read). prds.payload_md is NEVER
    altered in the DB — the fold produces a derived copy.

    Returns None when the PRD does not exist (same contract as get_prd). When
    there are zero applied patches, payload_md is byte-identical to the raw row
    (apply_patches_to_prd_md returns its input unchanged) — zero blast radius.
    """
    row = get_prd(prd_id)
    if row is None:
        return None
    # Lazy import: keeps db/prds.py importable without the prd_patches module on
    # every import path, and mirrors the lazy-import discipline used elsewhere in
    # the Design Agent DB layer.
    from app.db.prd_patches import apply_patches_to_prd_md, list_applied_patches
    patches = list_applied_patches(prd_id=prd_id)
    if not patches:
        return row                      # fast path: no fold, raw row returned as-is
    rendered = dict(row)                # copy — never mutate the row object in place
    rendered["payload_md"] = apply_patches_to_prd_md(row.get("payload_md") or "", patches)
    return rendered


def find_existing_prd(
    brief_id: int, insight_index: int, variant: str = "v1"
) -> dict | None:
    """Most recent ready/generating PRD (of the given variant) for a
    (brief, insight). Variant-scoped so distinct PRD formats don't
    dedupe against each other.
    """
    c = require_client()
    resp = (
        c.table("prds")
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


def reset_prd_to_draft(prd_id: int) -> None:
    c = require_client()
    c.table("prds").update({"status": "draft"}).eq("id", prd_id).execute()
    logger.info("prd_reset_to_draft prd_id=%s", prd_id)
