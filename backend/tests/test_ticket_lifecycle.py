"""Deleting a ticket, and holding one back from the PM tool.

Two states, one rule: a ticket that is `deleted` or `excluded` MUST NOT exist
in the bound tracker. They differ only in whether Sprntly still shows it —
excluded keeps the ticket, deleted does not.

The delete is SOFT (a mark on ticket_edits) because `prd_tickets.stories` is
regenerated wholesale from the PRD: a hard delete there would be undone by the
next regeneration and silently re-push a ticket the user removed. These tests
pin that, the tracker-side removal, and the fact that every surface listing
tickets honors the mark.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.stories.generate import Story
from tests._company_helpers import company_client
from tests.test_ticket_sync import CID, FakeTracker, _seed_prd_tickets, _sync_cfg, fake_tracker  # noqa: F401

KEY = "prd-7-guest-alert-data-model"


@pytest.fixture
def client(isolated_settings, monkeypatch) -> TestClient:
    return company_client(monkeypatch).client


# ── The routes ───────────────────────────────────────────────────────────────


def test_delete_marks_the_ticket_rather_than_dropping_the_row(client: TestClient):
    """A hard delete would be erased by the next PRD regeneration, which
    rebuilds prd_tickets.stories from scratch and knows nothing about it."""
    with patch("app.stories.sync.kick_prd_sync_from_key", return_value=False):
        resp = client.delete(f"/v1/tickets/{KEY}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["lifecycle"] == "deleted"

    from app.db.ticket_lifecycle import get_lifecycle

    assert get_lifecycle(client.app.state.company_id if False else _cid(client), KEY) == "deleted"


def _cid(client: TestClient) -> str:
    """The signed-in company (the helper seeds exactly one)."""
    from app.db.client import require_client

    return require_client().table("companies").select("id").execute().data[0]["id"]


def test_delete_kicks_the_tracker_sync(client: TestClient):
    """The tracker side is left to the sync pass — it already knows how to
    delete-or-close, drop the mapping and RETRY a failed removal."""
    with patch("app.stories.sync.kick_prd_sync_from_key", return_value=True) as kick:
        body = client.delete(f"/v1/tickets/{KEY}").json()
    kick.assert_called_once()
    assert kick.call_args[0][1] == KEY
    assert body["tracker_sync_started"] is True


@pytest.mark.parametrize("state", ["excluded", "deleted", "active"])
def test_lifecycle_route_sets_each_state(client: TestClient, state):
    with patch("app.stories.sync.kick_prd_sync_from_key", return_value=False):
        resp = client.put(f"/v1/tickets/{KEY}/lifecycle", json={"lifecycle": state})
    assert resp.status_code == 200, resp.text
    from app.db.ticket_lifecycle import get_lifecycle

    assert get_lifecycle(_cid(client), KEY) == state


def test_lifecycle_route_rejects_an_unknown_state(client: TestClient):
    resp = client.put(f"/v1/tickets/{KEY}/lifecycle", json={"lifecycle": "archived"})
    assert resp.status_code == 422


def test_lifecycle_survives_other_field_saves(client: TestClient):
    """The mark shares a row with every other override, so an ordinary edit
    must not quietly resurrect a deleted ticket."""
    with patch("app.stories.sync.kick_prd_sync_from_key", return_value=False):
        client.delete(f"/v1/tickets/{KEY}")
        client.put(f"/v1/tickets/{KEY}/fields", json={"priority": "P1 — High"})
    from app.db.ticket_lifecycle import get_lifecycle

    assert get_lifecycle(_cid(client), KEY) == "deleted"


def test_untouched_ticket_reads_as_active(isolated_settings):
    """No override row at all — every ticket that predates this feature."""
    from app.db.ticket_lifecycle import get_lifecycle, is_active

    assert get_lifecycle(CID, "prd-1-never-edited") == "active"
    assert is_active(None) and is_active("active") and not is_active("excluded")


# ── The sync pass ────────────────────────────────────────────────────────────


def _seed_bound_ticket(lifecycle: str | None = None) -> dict:
    """One pushed ticket on a ClickUp-bound PRD, optionally marked."""
    from app.stories.sync import content_hash
    from tests.test_ticket_sync import _edit_row

    base = Story(title="Login", body="Original").to_dict()
    _seed_prd_tickets(CID, 7, [base])
    tid = base["id"]
    FakeTracker.seed = {tid: {
        "title": "Login", "description": "Original", "status": "to do",
        "assignee": None, "url": "u", "updated_at": "2026-07-01T00:00:00+00:00",
    }}
    _sync_cfg(7, statuses={tid: {
        "status": "to do", "content_hash": content_hash("Login", "Original"),
        "synced_at": "2026-07-02T00:00:00+00:00", "sprntly_status": None,
    }})
    if lifecycle:
        _edit_row(CID, f"prd-7-{tid}", lifecycle=lifecycle,
                  updated_at="2026-07-10T00:00:00+00:00")
    return base


@pytest.mark.parametrize("state", ["deleted", "excluded"])
def test_sync_removes_a_marked_ticket_from_the_tracker(
    isolated_settings, fake_tracker, state  # noqa: F811
):
    from app.stories.sync import run_prd_sync

    base = _seed_bound_ticket(state)
    result = run_prd_sync(CID, 7)

    tracker = fake_tracker.instances[0]
    assert tracker.removed == [f"ref-{base['id']}"]
    assert tracker.cleared == [base["id"]]     # mapping dropped
    assert result["removed"] == 1
    # And it took NO part in the ordinary sync — never pushed, never imported.
    assert tracker.pushed == [] and result["imported"] == 0
    # Its stale tracker state leaves the sync row too.
    from app.db.ticket_sync import get_sync_config

    assert base["id"] not in (get_sync_config(CID, 7)["statuses"] or {})


def test_sync_keeps_the_mapping_when_removal_fails(
    isolated_settings, fake_tracker  # noqa: F811
):
    """Nothing landed, so nothing is forgotten — the next pass retries. This is
    what makes the removal eventually-consistent rather than one best-effort
    shot at delete time."""
    from app.stories.sync import run_prd_sync

    _seed_bound_ticket("deleted")
    FakeTracker.removal_fails = True

    result = run_prd_sync(CID, 7)

    tracker = fake_tracker.instances[0]
    assert tracker.removed and tracker.cleared == []
    assert result["removed"] == 0


def test_sync_never_creates_an_excluded_ticket(isolated_settings, fake_tracker):  # noqa: F811
    """The point of excluding: a ticket that was never pushed must not be
    pushed. Removals run before creates so it is not created-then-deleted."""
    from tests.test_ticket_sync import _edit_row
    from app.stories.sync import run_prd_sync

    base = Story(title="Login", body="B").to_dict()
    _seed_prd_tickets(CID, 7, [base])
    _sync_cfg(7)
    _edit_row(CID, f"prd-7-{base['id']}", lifecycle="excluded")

    result = run_prd_sync(CID, 7)

    tracker = fake_tracker.instances[0]
    assert tracker.created == []      # never pushed out
    assert tracker.removed == []      # and nothing to remove
    assert result["pushed"] == 0


def test_restoring_a_ticket_re_creates_it(isolated_settings, fake_tracker):  # noqa: F811
    """Back to active → the next pass treats it as never-pushed (its mapping
    was dropped on removal) and creates it fresh."""
    from tests.test_ticket_sync import _edit_row
    from app.stories.sync import run_prd_sync

    base = _seed_bound_ticket("excluded")
    run_prd_sync(CID, 7)                       # removes it
    _edit_row(CID, f"prd-7-{base['id']}", lifecycle="active")

    run_prd_sync(CID, 7)                       # restores it

    assert fake_tracker.instances[-1].created == ["Login"]


def test_merged_stories_skip_non_active_tickets(isolated_settings):
    from tests.test_ticket_sync import _edit_row
    from app.stories.sync import merged_stories_for_prd

    keep = Story(title="Keep", body="B").to_dict()
    drop = Story(title="Drop", body="B").to_dict()
    _seed_prd_tickets(CID, 7, [keep, drop])
    _edit_row(CID, f"prd-7-{drop['id']}", lifecycle="deleted")

    assert [s.title for s in merged_stories_for_prd(CID, 7)] == ["Keep"]


# ── Read surfaces ────────────────────────────────────────────────────────────


def test_for_prd_hides_deleted_and_tags_excluded(client: TestClient):
    """The tab's own read. Deleted tickets are gone; excluded ones stay (the
    user chose to keep them) carrying the flag the row badge renders."""
    cid = _cid(client)
    keep = Story(title="Keep", body="B").to_dict()
    hide = Story(title="Hide", body="B").to_dict()
    hold = Story(title="Hold", body="B").to_dict()
    _seed_prd_tickets(cid, 7, [keep, hide, hold])
    from tests.test_ticket_sync import _edit_row

    _edit_row(cid, f"prd-7-{hide['id']}", lifecycle="deleted")
    _edit_row(cid, f"prd-7-{hold['id']}", lifecycle="excluded")

    stories = client.get("/v1/stories/for-prd/7").json()["stories"]
    by_title = {s["title"]: s for s in stories}
    assert set(by_title) == {"Keep", "Hold"}
    assert by_title["Hold"]["lifecycle"] == "excluded"
    assert "lifecycle" not in by_title["Keep"]


def test_mcp_list_hides_deleted_and_flags_excluded(isolated_settings):
    from tests.test_ticket_sync import _edit_row
    from app.routes.internal_mcp import list_tickets

    keep = Story(title="Keep", body="B").to_dict()
    hide = Story(title="Hide", body="B").to_dict()
    hold = Story(title="Hold", body="B").to_dict()
    _seed_prd_tickets(CID, 7, [keep, hide, hold])
    _edit_row(CID, f"prd-7-{hide['id']}", lifecycle="deleted")
    _edit_row(CID, f"prd-7-{hold['id']}", lifecycle="excluded")

    out = list_tickets(CID)
    by_title = {t["title"]: t for t in out["tickets"]}
    assert set(by_title) == {"Keep", "Hold"}
    assert by_title["Hold"]["lifecycle"] == "excluded"
    assert "lifecycle" not in by_title["Keep"]


def test_push_route_refuses_a_non_active_ticket(client: TestClient):
    """These routes take an explicit task list, so a stale tab could ask to
    push a ticket the user just removed — the server, not the client, is where
    that has to be caught."""
    cid = _cid(client)
    from tests.test_ticket_sync import _edit_row

    _edit_row(cid, "prd-7-a", lifecycle="deleted")
    _edit_row(cid, "prd-7-b", lifecycle="excluded")

    with patch("app.routes.tickets._clickup_access_token", return_value="tok"), \
         patch("app.connectors.clickup_oauth.create_task",
               return_value={"id": "T1", "url": "u"}) as create:
        body = client.post("/v1/tickets/push-clickup", json={
            "list_id": "L1",
            "tasks": [
                {"task_id": "prd-7-a", "title": "Deleted one"},
                {"task_id": "prd-7-b", "title": "Excluded one"},
                {"task_id": "prd-7-c", "title": "Fine one"},
            ],
        }).json()

    assert [c.kwargs["name"] for c in create.call_args_list] == ["Fine one"]
    assert {e["task_id"] for e in body["errors"]} == {"prd-7-a", "prd-7-b"}
    assert [c["task_id"] for c in body["created"]] == ["prd-7-c"]


def test_chat_ticket_list_omits_deleted(isolated_settings):
    """A deleted ticket must never be offered as a rewrite target — proposing
    one would hand the user a confirm button that writes to a ticket they
    removed."""
    from tests.test_ticket_sync import _edit_row
    from app.ticket_update import _render_ticket_list

    keep = Story(title="Keep", body="B").to_dict()
    hide = Story(title="Hide", body="B").to_dict()
    _seed_prd_tickets(CID, 7, [keep, hide])
    _edit_row(CID, f"prd-7-{hide['id']}", lifecycle="deleted")

    rendered = _render_ticket_list(CID, 7)
    assert "Keep" in rendered and "Hide" not in rendered
