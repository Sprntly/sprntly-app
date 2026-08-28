"""db.artifacts.list_document_artifacts — the PRD/evidence index an OPEN reads.

Two properties that only show up at this layer, both about ORDER of operations:

  1. `openable_only` filters unopenable rows BEFORE the regeneration family
     collapses to its newest row. Collapsing first and filtering after is the
     bug: a backend restart flips every in-flight PRD to `invalidated`
     (db/prds.invalidate_orphan_generating_prds — a documented recurring event,
     not a hypothetical), which makes the dead row the newest in its family and
     the whole document unreachable from chat, while the Artifacts tab happily
     lists it.

  2. `brief_anchored` distinguishes a real insight_index from the storage
     SENTINEL a chat/ideation/uploaded PRD carries. The panel's Evidence tab
     resolves (brief_id, insight_index) into a document, so getting this wrong
     shows the brief's first finding under an unrelated PRD.

The unified listing (`list_artifacts_for_company`) is covered in
test_routes_artifacts.py; this file only exercises the half the chat open uses.
"""
from __future__ import annotations

import json

from app.db.artifacts import list_document_artifacts, prd_is_brief_anchored


def _seed_brief(dataset: str = "acme", week_label: str = "Week of Aug 1") -> int:
    from app.db.client import require_client

    return (
        require_client()
        .table("briefs")
        .insert({
            "dataset": dataset,
            "week_label": week_label,
            "payload": json.dumps({}),
            "is_current": True,
        })
        .execute()
        .data[0]["id"]
    )


def _seed_prd(*, brief_id: int, title: str, status: str = "ready",
              generated_at: str, insight_index: int = 0,
              theme_id: str | None = None, source: str = "brief") -> int:
    from app.db.client import require_client

    return (
        require_client()
        .table("prds")
        .insert({
            "brief_id": brief_id,
            "insight_index": insight_index,
            "title": title,
            "status": status,
            "source": source,
            "theme_id": theme_id,
            "generated_at": generated_at,
            # READ, so the auto-archive rule (db.prds.is_hidden_from_library)
            # does not hold these rows out of the listing. These tests are
            # about family collapse and brief anchoring; stamped rather than
            # re-sourced, because `source` decides what a family IS.
            "first_read_at": "2026-08-25T00:00:00Z",
        })
        .execute()
        .data[0]["id"]
    )


def _by_id(items: list[dict]) -> dict[int, dict]:
    return {i["id"]: i for i in items if i["type"] == "prd"}


def test_a_restart_invalidated_head_does_not_hide_its_ready_generation(
    isolated_settings,
):
    """THE case. Same family (one brief insight), two generations: the newer one
    was killed mid-flight by a restart. The ready one must still be openable."""
    brief_id = _seed_brief()
    ready = _seed_prd(
        brief_id=brief_id, title="Compliance Reporting",
        generated_at="2026-08-01T00:00:00Z",
    )
    dead = _seed_prd(
        brief_id=brief_id, title="Compliance Reporting", status="invalidated",
        generated_at="2026-08-02T00:00:00Z",
    )

    # The LISTING keeps showing the newest row, invalidated or not — a failed
    # generation is something the user should see in their artifact library.
    listed = _by_id(list_document_artifacts(dataset="acme"))
    assert set(listed) == {dead}

    # The OPEN index falls back to the newest row that can actually be shown.
    openable = _by_id(list_document_artifacts(dataset="acme", openable_only=True))
    assert set(openable) == {ready}


def test_a_family_whose_every_generation_died_is_simply_absent(isolated_settings):
    brief_id = _seed_brief()
    _seed_prd(brief_id=brief_id, title="Doomed", status="failed",
              generated_at="2026-08-01T00:00:00Z")
    _seed_prd(brief_id=brief_id, title="Doomed", status="invalidated",
              generated_at="2026-08-02T00:00:00Z")

    assert _by_id(list_document_artifacts(dataset="acme", openable_only=True)) == {}


def test_generating_rows_stay_openable(isolated_settings):
    """A PRD mid-generation is not a dead row — it has a panel to open into
    (the streaming/loading state). Only failed and invalidated are excluded."""
    brief_id = _seed_brief()
    pid = _seed_prd(brief_id=brief_id, title="In Flight", status="generating",
                    generated_at="2026-08-01T00:00:00Z")

    assert set(_by_id(list_document_artifacts(dataset="acme", openable_only=True))) == {pid}


def test_the_default_listing_is_unchanged_by_the_flag(isolated_settings):
    """`openable_only` defaults False so the Artifacts tab keeps its behaviour."""
    brief_id = _seed_brief()
    pid = _seed_prd(brief_id=brief_id, title="Broken", status="failed",
                    generated_at="2026-08-01T00:00:00Z")

    assert set(_by_id(list_document_artifacts(dataset="acme"))) == {pid}


def test_brief_anchoring_marks_the_sentinel_insight_index(isolated_settings):
    """A chat PRD's insight_index 0 means "no insight", not insight zero."""
    brief_id = _seed_brief()
    brief_prd = _seed_prd(
        brief_id=brief_id, title="From The Brief", insight_index=2,
        generated_at="2026-08-01T00:00:00Z",
    )
    chat_prd = _seed_prd(
        brief_id=brief_id, title="From Chat", insight_index=0,
        theme_id="chat:abc123", source="chat",
        generated_at="2026-08-01T00:00:00Z",
    )
    upload_prd = _seed_prd(
        brief_id=brief_id, title="Uploaded", insight_index=0, source="upload",
        generated_at="2026-08-01T00:00:00Z",
    )

    items = _by_id(list_document_artifacts(dataset="acme"))
    assert items[brief_prd]["brief_anchored"] is True
    assert items[chat_prd]["brief_anchored"] is False
    assert items[upload_prd]["brief_anchored"] is False


def test_prd_is_brief_anchored_matches_the_family_rule():
    """The helper is derived from _prd_family_key, so the two cannot drift."""
    assert prd_is_brief_anchored(
        {"brief_id": 1, "insight_index": 3, "theme_id": None, "source": "brief"}
    )
    assert not prd_is_brief_anchored(
        {"brief_id": 1, "insight_index": 0, "theme_id": "chat:x", "source": "chat"}
    )
    assert not prd_is_brief_anchored(
        {"id": 9, "brief_id": 1, "insight_index": 0, "theme_id": None,
         "source": "upload"}
    )
    # A legacy row with no `source` falls through to the insight branch, i.e.
    # historical behaviour.
    assert prd_is_brief_anchored(
        {"brief_id": 1, "insight_index": 1, "theme_id": None, "source": None}
    )
