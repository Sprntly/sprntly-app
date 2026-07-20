"""Regression tests: themed PRDs must not shadow a brief insight's own PRD.

Ideation/chat/upload PRDs anchor to the company's CURRENT brief with
insight_index 0 as a storage sentinel and are keyed by theme_id. The brief-path
lookups (find_existing_prd — the /v1/prd/generate find-or-create — and
list_prds_by_brief — the brief-prototype-map behind the brief cards) must
therefore only ever match theme_id-NULL rows. Before this filter, generating a
PRD from chat made the brief card's "View PRD" open the newest chat document
instead of the insight's own PRD (observed on staging: a "Dark mode option"
chat PRD shadowing the bulk-onboarding brief PRD).
"""
from __future__ import annotations

from app.db.prds import (
    find_existing_prd,
    find_existing_prd_for_theme,
    list_prds_by_brief,
)

_VARIANT = "v3"


def _seed(db, *, brief_id=1, insight_index=0, title, theme_id=None, prd_id=None):
    row = {
        "brief_id": brief_id,
        "insight_index": insight_index,
        "title": title,
        "payload_md": "",
        "status": "ready",
        "variant": _VARIANT,
        "theme_id": theme_id,
    }
    if prd_id is not None:
        row["id"] = prd_id
    return db.table("prds").insert(row).execute().data[0]["id"]


def test_newer_chat_prd_does_not_shadow_brief_prd(isolated_settings):
    db = isolated_settings["supabase"]
    brief_prd = _seed(db, title="Brief insight PRD")
    _seed(db, title="Chat PRD", theme_id="chat:abc123")  # newer id, same key

    found = find_existing_prd(1, 0, variant=_VARIANT)
    assert found is not None
    assert found["id"] == brief_prd
    assert found["theme_id"] is None


def test_find_existing_prd_ignores_ideation_rows_too(isolated_settings):
    db = isolated_settings["supabase"]
    _seed(db, title="Ideation PRD", theme_id="theme-42")
    assert find_existing_prd(1, 0, variant=_VARIANT) is None


def test_themed_lookup_still_finds_its_own_row(isolated_settings):
    db = isolated_settings["supabase"]
    chat_prd = _seed(db, title="Chat PRD", theme_id="chat:abc123")
    found = find_existing_prd_for_theme(1, "chat:abc123", variant=_VARIANT)
    assert found is not None and found["id"] == chat_prd


def test_list_prds_by_brief_excludes_themed_rows(isolated_settings):
    db = isolated_settings["supabase"]
    brief_prd = _seed(db, title="Brief insight PRD")
    _seed(db, title="Chat PRD", theme_id="chat:abc123")  # newer, same insight 0
    _seed(db, insight_index=1, title="Second insight PRD")

    rows = list_prds_by_brief(1, variant=_VARIANT)
    assert [r["insight_index"] for r in rows] == [0, 1]
    assert rows[0]["id"] == brief_prd  # newest NON-themed row wins insight 0

    all_rows = list_prds_by_brief(1, variant=_VARIANT, newest_only=False)
    assert all(not r.get("theme_id") for r in all_rows)
