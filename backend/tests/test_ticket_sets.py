"""Standalone ticket sets — the persistence layer (app/db/ticket_sets.py) and
the two behaviours that make them safe: company-scoped reads, and a row that
always reaches a terminal state.

A set is tickets generated from a chat with NO PRD behind them. The row is
created 'generating' at kick-off and flipped by the background job, so the
lifecycle assertions here are the ones the panel's spinner depends on.
"""
from __future__ import annotations

import pytest

from app.stories.generate import Story

CID = "aaaaaaaa-1111-2222-3333-444444444444"
OTHER = "bbbbbbbb-1111-2222-3333-444444444444"


# ── Lifecycle ────────────────────────────────────────────────────────────────


def test_create_returns_a_generating_row(isolated_settings):
    from app.db.ticket_sets import create_set, get_set

    sid = create_set(CID, source_text="break the checkout drop-off into tickets")
    row = get_set(CID, sid)
    assert row is not None
    assert row["status"] == "generating"
    assert row["stories"] in ([], None) or list(row["stories"]) == []
    assert row["source_text"] == "break the checkout drop-off into tickets"
    # Title is empty until the naming leg lands — the API returns it as-is and
    # the panel supplies its own copy, rather than the DB inventing a label.
    assert (row["title"] or "") == ""


def test_finish_marks_ready_with_stories(isolated_settings):
    from app.db.ticket_sets import create_set, finish_set, get_set

    sid = create_set(CID, source_text="q")
    stories = [Story(title="Fix the retry banner", body="b").to_dict()]
    finish_set(sid, title="Checkout Retry Fixes", stories=stories)

    row = get_set(CID, sid)
    assert row["status"] == "ready"
    assert row["title"] == "Checkout Retry Fixes"
    assert [s["title"] for s in row["stories"]] == ["Fix the retry banner"]


def test_finish_accepts_an_empty_run(isolated_settings):
    """A zero-ticket run is a real terminal outcome for a SET.

    The PRD path deliberately refuses to cache an empty result, because
    prd_tickets is a content-hash cache whose empty row would wedge the tab
    forever. A ticket_sets row is the artifact itself with nothing that re-kicks
    it, so refusing the write here would leave the panel spinning on a run that
    already finished — strictly worse than showing "nothing came back".
    """
    from app.db.ticket_sets import create_set, finish_set, get_set

    sid = create_set(CID, source_text="q")
    finish_set(sid, title="", stories=[])

    row = get_set(CID, sid)
    assert row["status"] == "ready"
    assert list(row["stories"] or []) == []


def test_fail_records_the_error(isolated_settings):
    from app.db.ticket_sets import create_set, fail_set, get_set

    sid = create_set(CID, source_text="q")
    fail_set(sid, "TimeoutError: read timed out")

    row = get_set(CID, sid)
    assert row["status"] == "failed"
    assert "Timeout" in (row["error"] or "")


def test_fail_truncates_a_pathological_error(isolated_settings):
    """The column is for operators; a megabyte of provider traceback is not."""
    from app.db.ticket_sets import create_set, fail_set, get_set

    sid = create_set(CID, source_text="q")
    fail_set(sid, "x" * 5000)
    assert len(get_set(CID, sid)["error"]) <= 500


# ── Tenancy — the ONLY boundary (RLS is bypassed by the service-role key) ────


def test_get_set_is_company_scoped(isolated_settings):
    """A foreign company's set reads as absent, so the route 404s it. The two
    cases must be indistinguishable — no cross-tenant existence disclosure."""
    from app.db.ticket_sets import create_set, get_set

    sid = create_set(CID, source_text="mine")
    assert get_set(CID, sid) is not None
    assert get_set(OTHER, sid) is None


def test_list_for_company_never_crosses_tenants(isolated_settings):
    from app.db.ticket_sets import create_set, list_sets_for_company

    create_set(CID, source_text="mine")
    create_set(OTHER, source_text="theirs")

    mine = list_sets_for_company(CID)
    assert [r["source_text"] for r in mine] == ["mine"]


def test_list_for_conversation_is_company_scoped(isolated_settings):
    """Conversation ids are sequential, so filtering on the id ALONE would hand
    a guessed thread's sets to whoever asked."""
    from app.db.ticket_sets import create_set, list_sets_for_conversation

    create_set(CID, conversation_id=77, source_text="mine")
    create_set(OTHER, conversation_id=77, source_text="theirs")

    assert [r["source_text"] for r in list_sets_for_conversation(CID, 77)] == ["mine"]
    assert [r["source_text"] for r in list_sets_for_conversation(OTHER, 77)] == ["theirs"]


def test_find_set_story_is_company_scoped(isolated_settings):
    from app.db.ticket_sets import create_set, find_set_story, finish_set

    story = Story(title="Only mine", body="b").to_dict()
    sid = create_set(CID, source_text="q")
    finish_set(sid, title="T", stories=[story])

    found, found_set = find_set_story(CID, story["id"])
    assert found["title"] == "Only mine" and found_set == sid
    assert find_set_story(OTHER, story["id"]) == (None, None)


# ── Listing shape ────────────────────────────────────────────────────────────


def test_listing_projection_excludes_the_stories_payload():
    """A list of N sets must not carry N full ticket arrays — same posture that
    keeps `html` out of the reports listing.

    Asserted against the projection CONSTANT rather than a returned row: the
    fake Supabase ignores PostgREST column lists and hands back every column, so
    a row-shape assertion here would pass regardless of what the query asks for
    and prove nothing about production.
    """
    from app.db.ticket_sets import _LIST_COLUMNS

    cols = {c.strip() for c in _LIST_COLUMNS.split(",")}
    assert "stories" not in cols
    assert {"id", "title", "status", "created_at", "conversation_id"} <= cols


def test_listing_returns_the_display_fields(isolated_settings):
    from app.db.ticket_sets import create_set, finish_set, list_sets_for_company

    sid = create_set(CID, source_text="q")
    finish_set(sid, title="T", stories=[Story(title="A", body="b").to_dict()])

    [row] = list_sets_for_company(CID)
    assert row["title"] == "T" and row["status"] == "ready"


def test_listing_is_newest_first(isolated_settings):
    from app.db.ticket_sets import create_set, list_sets_for_company

    create_set(CID, source_text="older")
    create_set(CID, source_text="newer")
    assert [r["source_text"] for r in list_sets_for_company(CID)] == ["newer", "older"]


# ── Story identity: the thing set keys are built from ────────────────────────


def test_insight_generated_stories_carry_a_stable_id():
    """`set-{id}-{story_id}` depends on every generated story having an `id`.

    The insight path shares Story.to_dict() with the PRD path, so the id is the
    same content hash — NOT a title slug. If this ever regressed to a slug,
    editing a ticket's title would change its identity and orphan its
    jira_issue_map row, silently duplicating the issue on the next sync.
    """
    d = Story(title="Ship the thing", body="As a user…").to_dict()
    assert d["id"] and d["id"] == Story(title="Ship the thing", body="As a user…").stable_id()
    assert "-" not in d["id"]  # a hash, not a slug


@pytest.mark.parametrize("kind,expected", [("prd", "prd-4-abc"), ("set", "set-4-abc")])
def test_scope_composes_disjoint_keys(kind, expected):
    from app.stories.scope import TicketScope

    assert TicketScope(kind, 4).ticket_key({"id": "abc"}) == expected


def test_scope_key_prefix_cannot_match_a_longer_id():
    """`prd-1-` must not sweep up `prd-12-…` — the trailing dash is load-bearing
    for every LIKE the sync pass runs over ticket_edits and ticket_comments."""
    from app.stories.scope import prd_scope

    assert prd_scope(1).key_prefix == "prd-1-"
    assert not "prd-12-abc".startswith(prd_scope(1).key_prefix)


def test_scope_from_key_fails_closed():
    from app.stories.scope import scope_from_key, set_scope

    assert scope_from_key("set-9-abc") == set_scope(9)
    assert scope_from_key("bare-story-id") is None
    assert scope_from_key("prd-notanumber-x") is None
    assert scope_from_key("") is None
