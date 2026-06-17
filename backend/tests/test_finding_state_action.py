"""Phase 2 lifecycle — theme-keyed user-action store on brief_finding_state.

Covers the db helpers added in app/db/finding_state.py:
  - set_finding_action (upsert; creates the row if missing; preserves fingerprint)
  - list_findings_by_action (filters by action, scoped to the enterprise)
"""
from __future__ import annotations

from app.db.finding_state import (
    COMPLETED_ACTIONS,
    list_findings_by_action,
    set_finding_action,
    upsert_finding_state,
)


def _seed_company(db, cid):
    if not db.table("companies").select("id").eq("id", cid).execute().data:
        db.table("companies").insert(
            {"id": cid, "slug": f"slug-{cid}", "display_name": cid.title()}
        ).execute()
    return cid


def test_set_finding_action_creates_row_when_missing(isolated_settings):
    db = isolated_settings["supabase"]
    _seed_company(db, "ent-A")

    set_finding_action("ent-A", "theme-1", "dismissed", client=db)

    rows = db.table("brief_finding_state").select("*").eq("enterprise_id", "ent-A").execute().data
    assert len(rows) == 1
    assert rows[0]["theme_id"] == "theme-1"
    assert rows[0]["action"] == "dismissed"


def test_set_finding_action_updates_existing_and_preserves_fingerprint(isolated_settings):
    db = isolated_settings["supabase"]
    _seed_company(db, "ent-A")
    # Existing fingerprint row (surfaced default).
    upsert_finding_state(
        "ent-A", theme_id="theme-1", signal_count=3, effective_weight=2.5,
        revenue_at_stake=900000, breadth=2, last_brief_id=7, client=db,
    )

    set_finding_action("ent-A", "theme-1", "prd_created", client=db)

    rows = db.table("brief_finding_state").select("*").eq("enterprise_id", "ent-A").execute().data
    assert len(rows) == 1                       # upsert, not a second row
    r = rows[0]
    assert r["action"] == "prd_created"
    assert r["fp_signal_count"] == 3            # fingerprint preserved
    assert r["fp_revenue_at_stake"] == 900000
    assert r["last_brief_id"] == 7


def test_list_findings_by_action_filters_and_scopes_to_enterprise(isolated_settings):
    db = isolated_settings["supabase"]
    _seed_company(db, "ent-A")
    _seed_company(db, "ent-B")

    set_finding_action("ent-A", "t-prd", "prd_created", client=db)
    set_finding_action("ent-A", "t-done", "done", client=db)
    set_finding_action("ent-A", "t-dismissed", "dismissed", client=db)
    set_finding_action("ent-A", "t-surfaced", "surfaced", client=db)
    set_finding_action("ent-B", "t-other", "prd_created", client=db)   # other tenant

    completed = list_findings_by_action("ent-A", COMPLETED_ACTIONS, client=db)
    theme_ids = {r["theme_id"] for r in completed}
    assert theme_ids == {"t-prd", "t-done"}     # only prd_created/done, only ent-A
