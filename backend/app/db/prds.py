"""PRDs — backed by the `prds` table in Supabase.

One row per generation attempt for a (brief_id, insight_index, variant).
`status` walks generating → ready (or failed/invalidated).
"""
import logging

from app.db.client import require_client, retry_on_disconnect

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


def complete_prd_2part(prd_id: int, title: str, human_md: str, llm_part: str) -> None:
    """Complete a 2-part PRD (prd-author skill): Part A (human-readable) goes to
    `payload_md` — what the frontend renders, unchanged — and Part B (the
    LLM-readable Implementation Spec) goes to the `llm_part` column for
    downstream coding-agent consumption.
    """
    c = require_client()
    c.table("prds").update({
        "title": title,
        "payload_md": human_md,
        "llm_part": llm_part,
        "status": "ready",
        "error": None,
    }).eq("id", prd_id).execute()


def fail_prd(prd_id: int, error: str) -> None:
    c = require_client()
    c.table("prds").update({
        "status": "failed",
        "error": (error or "")[:500],
    }).eq("id", prd_id).execute()


@retry_on_disconnect
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
    try:
        from app.db.prd_patches import apply_patches_to_prd_md, list_applied_patches
        patches = list_applied_patches(prd_id=prd_id)
    except Exception:
        # prd_patches table may not exist yet (P3-09 migration pending).
        # Gracefully fall back to the raw row — no patch folding.
        return row
    if not patches:
        return row                      # fast path: no fold, raw row returned as-is
    rendered = dict(row)                # copy — never mutate the row object in place
    rendered["payload_md"] = apply_patches_to_prd_md(row.get("payload_md") or "", patches)
    return rendered


@retry_on_disconnect
def list_prd_generations(prd_id: int) -> list[dict]:
    """All generation attempts sharing this PRD's (brief_id, insight_index),
    newest first. Each regeneration creates a new prds row; this returns the
    whole family so the Version History can offer prior generations. Returns []
    when the PRD doesn't exist. None-safe on insight_index (filtered in Python so
    a NULL insight_index groups correctly, mirroring db/artifacts.py)."""
    c = require_client()
    row = get_prd(prd_id)
    if row is None:
        return []
    resp = (
        c.table("prds")
        .select("id, title, status, generated_at, insight_index")
        .eq("brief_id", row["brief_id"])
        .order("generated_at", desc=True)
        .execute()
    )
    ins = row.get("insight_index")
    return [r for r in (resp.data or []) if r.get("insight_index") == ins]


@retry_on_disconnect
def latest_prd_for_dataset(dataset: str) -> dict | None:
    """Most recent ready PRD whose brief belongs to `dataset`."""
    c = require_client()
    # Find the latest brief for this dataset, then the latest ready PRD for it.
    brief_resp = (
        c.table("briefs")
        .select("id")
        .eq("dataset", dataset)
        .eq("is_current", True)
        .order("generated_at", desc=True)
        .limit(1)
        .execute()
    )
    if not brief_resp.data:
        return None
    brief_id = brief_resp.data[0]["id"]
    resp = (
        c.table("prds")
        .select("*")
        .eq("brief_id", brief_id)
        .eq("status", "ready")
        .order("id", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


@retry_on_disconnect
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


@retry_on_disconnect
def list_prds_by_brief(brief_id: int, variant: str = "v1") -> list[dict]:
    """Return the NEWEST ready PRD per insight for a (brief, variant), ordered
    by insight_index ascending.

    One query — not per-insight — returning at minimum `id` + `insight_index`.
    Used by GET /v1/design-agent/brief-prototype-map to build context-aware cards
    without iterating over every possible insight_index individually.

    Only `status='ready'` rows are returned; generating/failed/invalidated rows are
    excluded so callers only see PRDs that can back a prototype.

    A regenerated PRD (force / a second generation) creates a NEW `prds` row while
    the prior one stays ready, so an insight can have several ready rows. This
    function collapses each insight to its newest (highest-id) row — the one the
    user just generated — so the consumer (brief-prototype-map) binds exactly one
    deterministic, freshest prd_id per insight. Without the collapse the route
    emitted duplicate per-insight entries and the frontend's last-wins map could
    land on a stale prd_id (the regenerated PRD silently never surfaced).
    """
    c = require_client()
    resp = (
        c.table("prds")
        .select("id, insight_index, title, status")
        .eq("brief_id", brief_id)
        .eq("variant", variant)
        .eq("status", "ready")
        .execute()
    )
    # Collapse to the newest (highest-id) ready PRD per insight, then return them
    # in insight_index order. Done in Python rather than via SQL ordering so the
    # result is deterministic regardless of the driver's tie-break behaviour.
    newest_by_insight: dict[int, dict] = {}
    for row in resp.data or []:
        idx = row["insight_index"]
        kept = newest_by_insight.get(idx)
        if kept is None or row["id"] > kept["id"]:
            newest_by_insight[idx] = row
    return [newest_by_insight[idx] for idx in sorted(newest_by_insight)]


# ── PRD version control ──────────────────────────────────────────────────

def update_prd_content(prd_id: int, title: str, payload_md: str) -> dict | None:
    """Update the PRD's title and markdown content. Returns the updated row."""
    c = require_client()
    c.table("prds").update({
        "title": title,
        "payload_md": payload_md,
    }).eq("id", prd_id).execute()
    return get_prd(prd_id)


def save_prd_version(prd_id: int, title: str, payload_md: str, saved_by: str = "user") -> dict:
    """Save a snapshot of the PRD as a version in the prd_versions table.
    Creates the table row and returns it."""
    from app.db.client import utc_now
    c = require_client()
    # Count existing versions to determine version number
    existing = c.table("prd_versions").select("id").eq("prd_id", prd_id).execute()
    version_number = len(existing.data) + 1 if existing.data else 1
    resp = c.table("prd_versions").insert({
        "prd_id": prd_id,
        "version_number": version_number,
        "title": title,
        "payload_md": payload_md,
        "saved_by": saved_by,
        "saved_at": utc_now(),
    }).execute()
    logger.info("prd_version_saved prd_id=%s version=%s", prd_id, version_number)
    return resp.data[0]


def list_prd_versions(prd_id: int) -> list[dict]:
    """List all saved versions of a PRD, newest first."""
    c = require_client()
    resp = (
        c.table("prd_versions")
        .select("*")
        .eq("prd_id", prd_id)
        .order("version_number", desc=True)
        .execute()
    )
    return resp.data or []


def get_prd_version(version_id: int) -> dict | None:
    """Get a specific version by its id."""
    c = require_client()
    resp = c.table("prd_versions").select("*").eq("id", version_id).limit(1).execute()
    return resp.data[0] if resp.data else None


def restore_prd_version(prd_id: int, version_id: int) -> dict | None:
    """Restore a PRD to a specific version. Saves the current content as a new
    version first (so nothing is lost), then overwrites the PRD with the
    version's content."""
    # Get the version to restore
    version = get_prd_version(version_id)
    if not version or version["prd_id"] != prd_id:
        return None
    # Save current state as a version before overwriting
    current = get_prd(prd_id)
    if current:
        save_prd_version(prd_id, current.get("title", ""), current.get("payload_md", ""), saved_by="auto-save before restore")
    # Overwrite PRD with version content
    return update_prd_content(prd_id, version["title"], version["payload_md"])
