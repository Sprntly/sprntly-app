"""Removals in Sprntly reaching the tracker.

Ticket sync propagated every EDIT but no REMOVAL: a deleted comment stayed on
the Jira issue, a removed child issue stayed on the ClickUp checklist, and a
dropped tag stayed attached. Each test here is one of those, pinned at the seam
where the removal has to cross into the tracker.

The Jira/Asana child-issue reconcile lives with its provider's suite
(test_tracker_native_sync.py, test_asana_sync.py).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.stories.generate import Story
from tests._company_helpers import company_client

KEY = "prd-7-guest-alert-data-model"


@pytest.fixture
def client(isolated_settings, monkeypatch) -> TestClient:
    return company_client(monkeypatch).client


# ── Comment deletion (Sprntly → tracker) ─────────────────────────────────────


def test_deleting_a_pushed_comment_removes_it_from_the_tracker(client: TestClient):
    """The route reads `tracker_comment_id` BEFORE deleting the row — the row
    is the only record that the comment was ever pushed, so reading it after
    the delete (or not at all, as before) leaves the tracker copy stranded with
    nothing able to find it again."""
    posted = client.post(
        f"/v1/tickets/{KEY}/comments", json={"body": "Ship behind a flag?"}
    ).json()
    from app.db.client import require_client

    require_client().table("ticket_comments").update(
        {"tracker_comment_id": "TRK-55"}
    ).eq("id", posted["id"]).execute()

    with patch("app.stories.sync.kick_comment_delete") as kick:
        resp = client.delete(f"/v1/tickets/{KEY}/comments/{posted['id']}")
    assert resp.status_code == 200
    kick.assert_called_once()
    assert kick.call_args[0][1:] == (KEY, "TRK-55")

    # And it really is gone locally.
    assert client.get(f"/v1/tickets/{KEY}/data").json()["comments"] == []


def test_deleting_a_never_pushed_comment_does_not_call_the_tracker(client: TestClient):
    """No tracker id means the comment never left Sprntly — nothing to remove
    there, and no pointless thread/API call."""
    posted = client.post(f"/v1/tickets/{KEY}/comments", json={"body": "local only"}).json()

    with patch("app.stories.sync.kick_comment_delete") as kick:
        client.delete(f"/v1/tickets/{KEY}/comments/{posted['id']}")
    kick.assert_not_called()


def test_kick_comment_delete_is_a_noop_for_an_unbound_prd(isolated_settings):
    """Nothing was ever pushed for an unbound PRD, so there is nothing to
    delete — and no thread is spawned."""
    from app.stories.sync import kick_comment_delete

    assert kick_comment_delete("cid", "prd-7-abc", "TRK-1") is False
    assert kick_comment_delete("cid", "not-a-ticket-key", "TRK-1") is False


@pytest.mark.parametrize(
    "provider,expected_call",
    [
        ("clickup", ("clickup_oauth", "delete_task_comment")),
        ("asana", ("asana_oauth", "delete_task_comment")),
        ("jira", ("jira_oauth", "delete_issue_comment")),
    ],
)
def test_tracker_delete_comment_routes_per_provider(provider, expected_call):
    """Jira addresses a comment under its issue; ClickUp and Asana address it
    by its own id. The adapter has to know the difference."""
    from app.stories import sync as sync_mod

    tracker = object.__new__(sync_mod._Tracker)
    tracker.provider = provider
    tracker._token = "tok"
    tracker._cloud = "cloud"

    module, fn = expected_call
    with patch.object(getattr(sync_mod, module), fn, return_value=True) as call:
        assert tracker.delete_comment("REF-1", "C9") is True
    if provider == "jira":
        call.assert_called_once_with("tok", "cloud", "REF-1", "C9")
    else:
        call.assert_called_once_with("tok", "C9")


def test_tracker_delete_comment_survives_a_refusal():
    """A comment cannot be "closed", so a refused delete just stays — reported
    honestly as False rather than raised into the pass."""
    from app.connectors.tracker_errors import TrackerDeleteForbiddenError
    from app.stories import sync as sync_mod

    tracker = object.__new__(sync_mod._Tracker)
    tracker.provider = "clickup"
    tracker._token = "tok"

    with patch.object(sync_mod.clickup_oauth, "delete_task_comment",
                      side_effect=TrackerDeleteForbiddenError("nope")):
        assert tracker.delete_comment("REF-1", "C9") is False


# ── ClickUp child issues (a checklist) ───────────────────────────────────────


def _checklist(items: list[tuple[str, str]]) -> list[dict]:
    return [{
        "id": "CL1", "name": "Child issues",
        "items": [{"id": i, "name": n} for i, n in items],
    }]


def test_clickup_checklist_deletes_a_removed_child():
    from app.stories import push as push_mod

    story = Story(title="Login", body="x", subtasks=["Wire the route"])
    with patch("app.connectors.clickup_oauth.delete_checklist_item") as del_item, \
         patch("app.connectors.clickup_oauth.create_checklist_item") as add_item, \
         patch("app.connectors.clickup_oauth.delete_checklist") as del_list:
        push_mod._reconcile_subtasks_checklist(
            "tok", "T1", story,
            checklists=_checklist([("I1", "Wire the route"),
                                   ("I2", "Write the migration")]),
        )
    del_item.assert_called_once_with("tok", "CL1", "I2")
    add_item.assert_not_called()
    del_list.assert_not_called()


def test_clickup_checklist_adds_a_new_child_on_a_later_push():
    """Reconcile runs on EVERY push now. It used to run only on create, so an
    edited child-issue list never reached ClickUp at all."""
    from app.stories import push as push_mod

    story = Story(title="Login", body="x",
                  subtasks=["Wire the route", "[P] Write the migration"])
    with patch("app.connectors.clickup_oauth.delete_checklist_item") as del_item, \
         patch("app.connectors.clickup_oauth.create_checklist_item") as add_item:
        push_mod._reconcile_subtasks_checklist(
            "tok", "T1", story, checklists=_checklist([("I1", "Wire the route")]),
        )
    del_item.assert_not_called()
    add_item.assert_called_once_with("tok", "CL1", "Write the migration")


def test_clickup_checklist_drops_the_whole_list_when_the_last_child_goes():
    """ClickUp keeps an emptied checklist, so removing the last child has to
    take the checklist with it — not leave a bare "Child issues" heading."""
    from app.stories import push as push_mod

    story = Story(title="Login", body="x", subtasks=[])
    with patch("app.connectors.clickup_oauth.delete_checklist") as del_list, \
         patch("app.connectors.clickup_oauth.delete_checklist_item") as del_item:
        push_mod._reconcile_subtasks_checklist(
            "tok", "T1", story, checklists=_checklist([("I1", "Wire the route")]),
        )
    del_list.assert_called_once_with("tok", "CL1")
    del_item.assert_not_called()


def test_clickup_checklist_prunes_a_duplicated_item():
    """One child issue must never render as two rows."""
    from app.stories import push as push_mod

    story = Story(title="Login", body="x", subtasks=["Wire the route"])
    with patch("app.connectors.clickup_oauth.delete_checklist_item") as del_item, \
         patch("app.connectors.clickup_oauth.create_checklist_item") as add_item:
        push_mod._reconcile_subtasks_checklist(
            "tok", "T1", story,
            checklists=_checklist([("I1", "Wire the route"),
                                   ("I2", "Wire the route")]),
        )
    del_item.assert_called_once_with("tok", "CL1", "I2")
    add_item.assert_not_called()


def test_clickup_checklist_creates_it_when_absent():
    from app.stories import push as push_mod

    story = Story(title="Login", body="x", subtasks=["Wire the route"])
    with patch("app.connectors.clickup_oauth.create_checklist",
               return_value="CLNEW") as create, \
         patch("app.connectors.clickup_oauth.create_checklist_item") as add_item:
        push_mod._reconcile_subtasks_checklist("tok", "T1", story, checklists=[])
    create.assert_called_once_with("tok", "T1", "Child issues")
    add_item.assert_called_once_with("tok", "CLNEW", "Wire the route")


def test_clickup_checklist_no_children_and_no_checklist_does_nothing():
    from app.stories import push as push_mod

    story = Story(title="Login", body="x", subtasks=[])
    with patch("app.connectors.clickup_oauth.create_checklist") as create, \
         patch("app.connectors.clickup_oauth.delete_checklist") as del_list:
        push_mod._reconcile_subtasks_checklist("tok", "T1", story, checklists=[])
    create.assert_not_called()
    del_list.assert_not_called()


def test_get_task_carries_checklists_so_the_sync_needs_no_second_read():
    """The reconcile in the two-way pass reuses the task read the pass already
    does — checklists riding along on get_task is what makes that free."""
    from app.connectors import clickup_oauth

    resp = MagicMock(status_code=200, ok=True)
    resp.json.return_value = {
        "id": "T1", "name": "Login", "status": {"status": "to do"},
        "checklists": [{"id": 7, "name": "Child issues",
                        "items": [{"id": 71, "name": "Wire the route"}]}],
    }
    with patch("app.connectors.clickup_oauth.requests.get", return_value=resp):
        state = clickup_oauth.get_task("tok", "T1")
    assert state["checklists"] == [
        {"id": "7", "name": "Child issues",
         "items": [{"id": "71", "name": "Wire the route"}]}
    ]


# ── ClickUp tags + custom-field clear ────────────────────────────────────────


def _clickup_tracker(meta: dict):
    from app.stories import sync as sync_mod

    tracker = object.__new__(sync_mod._Tracker)
    tracker.provider = "clickup"
    tracker._token = "tok"
    tracker.meta = meta
    return tracker


_TAG_META = {"fields": [{"id": "builtin:tags", "type": "labels",
                         "name": "Tags", "editable": True}]}


def test_clickup_tag_removal_pushes_only_the_difference():
    """ClickUp has no whole-list tag write, so a reconcile is add + remove per
    changed tag. Removal was missing entirely — tags were add-only."""
    from app.connectors import clickup_oauth

    tracker = _clickup_tracker(_TAG_META)
    with patch.object(clickup_oauth, "add_task_tag") as add, \
         patch.object(clickup_oauth, "remove_task_tag") as remove:
        tracker.push_custom_fields(
            "T1", {"builtin:tags": ["keep", "added"]},
            {"builtin:tags": ["keep", "dropped"]},
        )
    add.assert_called_once_with("tok", "T1", "added")
    remove.assert_called_once_with("tok", "T1", "dropped")


def test_clickup_tag_push_without_current_stays_add_only():
    """No remote snapshot → we do not know what is attached, so we add and
    remove NOTHING rather than guessing a removal."""
    from app.connectors import clickup_oauth

    tracker = _clickup_tracker(_TAG_META)
    with patch.object(clickup_oauth, "add_task_tag") as add, \
         patch.object(clickup_oauth, "remove_task_tag") as remove:
        tracker.push_custom_fields("T1", {"builtin:tags": ["a"]})
    add.assert_called_once_with("tok", "T1", "a")
    remove.assert_not_called()


def test_clearing_a_custom_field_is_stored_as_an_explicit_null(client: TestClient):
    """"Cleared" and "never set" have to be different rows, or the sync engine
    cannot tell a removal from an absence. Popping the key made them identical,
    so a clear was silently dropped."""
    client.put(f"/v1/tickets/{KEY}/fields", json={
        "custom_fields": {"cf_1": {"id": "o1", "name": "Web"}, "cf_2": "keep me"},
    })
    client.put(f"/v1/tickets/{KEY}/fields", json={"custom_fields": {"cf_1": None}})

    stored = client.get(f"/v1/tickets/{KEY}/data").json()["custom_fields"]
    assert "cf_1" in stored and stored["cf_1"] is None   # cleared, not absent
    assert stored["cf_2"] == "keep me"                    # sibling untouched


def test_clickup_cleared_custom_field_calls_the_remove_endpoint():
    """ClickUp's set endpoint has no null value, so a cleared field used to
    just stop being pushed and the stale value stayed on the task."""
    from app.connectors import clickup_oauth

    tracker = _clickup_tracker(
        {"fields": [{"id": "cf_1", "type": "text", "name": "Squad", "editable": True}]}
    )
    with patch.object(clickup_oauth, "clear_custom_field") as clear, \
         patch.object(clickup_oauth, "set_custom_field") as set_field:
        tracker.push_custom_fields("T1", {"cf_1": None})
    clear.assert_called_once_with("tok", "T1", "cf_1")
    set_field.assert_not_called()
