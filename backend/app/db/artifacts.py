"""Aggregated artifact listing for the All-Chats "Artifacts" tab.

A read-only fan-out over the three generated-artifact tables — PRDs, prototypes,
and evidence — unified into one recency-sorted list for a single company.

Tenant scoping is split because the two surfaces key off the tenant
differently (verified against the existing queries):

  - PRDs / evidences are scoped by the BRIEF's `dataset` slug:
    briefs.dataset = <company slug>  →  briefs.id  →  prds/evidences.brief_id.
    (Mirrors app.deps.ownership's brief→dataset→company chain.)

  - Prototypes are scoped by `workspace_id`, which the Design Agent routes set
    to `company.company_id` (the company UUID) — see routes/design_agent.py
    (`workspace_id = company.company_id`) and db/prototypes.py. So prototypes
    are filtered by the company UUID, NOT the slug.

The route passes BOTH (the slug for PRDs/evidence, the UUID for prototypes) so
each surface is scoped the way its own writers scoped it. Joins are done in
Python (fetch brief ids for the dataset → prds/evidences by brief_id IN (...);
prototypes by workspace_id; then map prd_id → title for prototype titles)
because the PostgREST client makes multi-table SQL joins awkward — the same
in-code-join posture db/prds.latest_prd_for_dataset already uses.
"""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from app.db.client import require_client, retry_on_disconnect
from app.design_agent.storage import fresh_preview_image_url


def _relativize_local(url: str | None) -> str | None:
    """Strip a dev/localhost scheme+host so the URL renders relative.

    A dev-baked preview URL (`http://localhost:8000/...` or `http://127.0.0.1/...`)
    is host- and port-specific, which breaks once the page is served from a
    different frontend port. Dropping scheme+host leaves a root-relative path that
    resolves under whatever host serves the page (and, in dev, through the
    `web/public/prototype-bundles` symlink). A prod signed URL (any non-localhost
    absolute URL) passes through UNCHANGED so its host + signing token survive.
    """
    if not url:
        return url
    parts = urlsplit(url)
    if parts.scheme in ("http", "https") and parts.hostname in ("localhost", "127.0.0.1"):
        # strip scheme+host → path(+query) so it resolves under whatever host serves the page
        return urlunsplit(("", "", parts.path, parts.query, ""))
    return url

# Hard cap on the unified list. Recency-sorted, so the cap keeps the newest
# 200 artifacts; older ones are dropped (acceptable for a listing view — the
# brief/PRD screens remain the source of truth for deep history).
_LIST_CAP = 200


@retry_on_disconnect
def list_artifacts_for_company(*, dataset: str, company_id: str) -> list[dict]:
    """Unified, recency-sorted artifact list for one company.

    `dataset` is the company slug (scopes PRDs + evidences via briefs.dataset);
    `company_id` is the company UUID (scopes prototypes via workspace_id). The
    caller (routes/artifacts.py) has already tenant-gated both.

    Returns a list of normalized dicts shaped:
        {type, id, title, status, created_at, source, open}
    sorted by created_at DESC and capped at 200.
    """
    c = require_client()

    # ── Briefs for this dataset: id → week_label. Drives PRD/evidence scoping
    #    and supplies the human "from Brief <week_label>" source line. ────────
    brief_rows = (
        c.table("briefs")
        .select("id, week_label")
        .eq("dataset", dataset)
        .execute()
        .data
        or []
    )
    brief_ids = [r["id"] for r in brief_rows]
    week_label_by_brief = {r["id"]: r.get("week_label") for r in brief_rows}

    items: list[dict] = []

    if brief_ids:
        # ── PRDs (brief_id IN brief_ids) ────────────────────────────────────
        prd_rows = (
            c.table("prds")
            .select("id, brief_id, insight_index, title, status, generated_at")
            .in_("brief_id", brief_ids)
            .execute()
            .data
            or []
        )
        for r in prd_rows:
            bid = r["brief_id"]
            items.append({
                "type": "prd",
                "id": r["id"],
                "title": r.get("title") or "Untitled PRD",
                "status": r.get("status") or "",
                "created_at": r.get("generated_at"),
                "source": {
                    "brief_id": bid,
                    "week_label": week_label_by_brief.get(bid),
                    "insight_index": r.get("insight_index"),
                },
                "open": {
                    "brief_id": bid,
                    "insight_index": r.get("insight_index"),
                    "prd_id": r["id"],
                },
            })

        # ── Evidences (brief_id IN brief_ids) ───────────────────────────────
        ev_rows = (
            c.table("evidences")
            .select("id, brief_id, insight_index, title, status, generated_at")
            .in_("brief_id", brief_ids)
            .execute()
            .data
            or []
        )
        for r in ev_rows:
            bid = r["brief_id"]
            items.append({
                "type": "evidence",
                "id": r["id"],
                "title": r.get("title") or "Untitled evidence",
                "status": r.get("status") or "",
                "created_at": r.get("generated_at"),
                "source": {
                    "brief_id": bid,
                    "week_label": week_label_by_brief.get(bid),
                    "insight_index": r.get("insight_index"),
                },
                "open": {
                    "brief_id": bid,
                    "insight_index": r.get("insight_index"),
                    "evidence_id": r["id"],
                },
            })

    # ── Prototypes (workspace_id = company UUID). Title is derived from the
    #    parent PRD (prototypes have no title column). ─────────────────────────
    proto_rows = (
        c.table("prototypes")
        .select("id, prd_id, status, created_at, preview_image_url, current_checkpoint_id")
        .eq("workspace_id", company_id)
        .execute()
        .data
        or []
    )
    # Only surface prototypes that finished generating — failed/invalidated rows
    # have no usable bundle or preview and shouldn't appear in the artifacts list.
    proto_rows = [r for r in proto_rows if r.get("status") == "ready"]
    if proto_rows:
        prd_ids = sorted({r["prd_id"] for r in proto_rows if r.get("prd_id") is not None})
        prd_title_by_id: dict[int, str] = {}
        if prd_ids:
            title_rows = (
                c.table("prds")
                .select("id, title")
                .in_("id", prd_ids)
                .execute()
                .data
                or []
            )
            prd_title_by_id = {r["id"]: r.get("title") for r in title_rows}
        for r in proto_rows:
            pid = r.get("prd_id")
            prd_title = prd_title_by_id.get(pid) or "Untitled PRD"
            # Served preview URL: re-sign against the current checkpoint when we
            # have one (the stored URL is a 24h signed URL that goes stale), else
            # fall back to the stored value. Then relativize a dev/localhost-baked
            # URL so it renders regardless of frontend port; a prod signed URL is
            # left untouched.
            ckpt_id = r.get("current_checkpoint_id")
            if ckpt_id is not None:
                served = fresh_preview_image_url(
                    prototype_id=r["id"],
                    checkpoint_id=ckpt_id,
                    stored_preview_image_url=r.get("preview_image_url"),
                )
            else:
                served = r.get("preview_image_url")
            served = _relativize_local(served)
            items.append({
                "type": "prototype",
                "id": r["id"],
                # Derived from the parent PRD's title (no prototype title column).
                "title": prd_title,
                "status": r.get("status") or "",
                "created_at": r.get("created_at"),
                "preview_image_url": served,
                "source": {
                    "prd_id": pid,
                    "prd_title": prd_title,
                },
                "open": {
                    "prototype_id": r["id"],
                    "prd_id": pid,
                },
            })

    # Recency sort (newest first). created_at is an ISO-8601 string; lexical
    # sort matches chronological order for same-format UTC timestamps. None
    # timestamps (shouldn't happen — all three tables default the column) sort
    # last via an empty-string fallback.
    items.sort(key=lambda it: it.get("created_at") or "", reverse=True)
    return items[:_LIST_CAP]
