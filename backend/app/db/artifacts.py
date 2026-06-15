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

from app.db.client import require_client, retry_on_disconnect

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
        # A PRD is regenerated in place: each attempt is a new prds row sharing
        # the same (brief_id, insight_index). The artifacts list shows only the
        # LATEST generation per logical PRD; older generations are reachable from
        # the PRD's Version History (see routes/prd.py /{prd_id}/generations).
        latest_by_key: dict[tuple, dict] = {}
        for r in prd_rows:
            key = (r["brief_id"], r.get("insight_index"))
            cur = latest_by_key.get(key)
            if cur is None or (r.get("generated_at") or "") > (cur.get("generated_at") or ""):
                latest_by_key[key] = r
        for r in latest_by_key.values():
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
        .select("id, prd_id, status, created_at")
        .eq("workspace_id", company_id)
        .execute()
        .data
        or []
    )
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
            items.append({
                "type": "prototype",
                "id": r["id"],
                # Derived from the parent PRD's title (no prototype title column).
                "title": prd_title,
                "status": r.get("status") or "",
                "created_at": r.get("created_at"),
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
