"""Delete primitives on the three task-management connectors.

These back the removal half of ticket sync: today a child issue removed in
Sprntly, a deleted comment, or a dropped ClickUp tag all stay put in the
customer's tracker, because no connector had a delete call at all.

The contract every one of them shares — and what these tests pin — is the
status mapping, because the interesting cases are the non-200s:

  404/410  ALREADY gone  -> True. The caller asked for absence and absence is
           what holds; returning False would make a sync pass retry a delete
           forever against an object that no longer exists.
  403      refused       -> TrackerDeleteForbiddenError, NOT the connector's
           *AuthExpiredError. The token is fine; the account simply may not
           destroy this object (Jira's "Delete Issues" permission is the common
           one, and it is not implied by the write:jira-work scope). The caller
           closes the item instead, and a reconnect prompt would send the user
           somewhere that cannot help.
  401      bad token     -> the connector's own *AuthExpiredError (reconnect).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.connectors.tracker_errors import TrackerDeleteForbiddenError

CLOUD = "cloud-1"
TOK = "tok"


def _resp(status: int, text: str = "") -> MagicMock:
    return MagicMock(status_code=status, ok=200 <= status < 300, text=text)


# ── Jira ─────────────────────────────────────────────────────────────────────


def test_jira_delete_issue_calls_delete_with_subtasks():
    """The default carries `deleteSubtasks=true`: Jira REFUSES to delete a
    parent that still has children, and a Sprntly ticket owns the sub-tasks
    Sprntly created under it — orphaning them is the very state this fixes."""
    from app.connectors import jira_oauth

    with patch("app.connectors.jira_oauth.requests.delete",
               return_value=_resp(204)) as d:
        assert jira_oauth.delete_issue(TOK, CLOUD, "PROJ-1") is True
    url, kwargs = d.call_args[0][0], d.call_args[1]
    assert url.endswith(f"/{CLOUD}/rest/api/3/issue/PROJ-1")
    assert kwargs["params"] == {"deleteSubtasks": "true"}
    assert kwargs["headers"]["Authorization"] == f"Bearer {TOK}"


def test_jira_delete_issue_can_opt_out_of_subtasks():
    from app.connectors import jira_oauth

    with patch("app.connectors.jira_oauth.requests.delete",
               return_value=_resp(204)) as d:
        jira_oauth.delete_issue(TOK, CLOUD, "PROJ-1", delete_subtasks=False)
    assert d.call_args[1]["params"] == {"deleteSubtasks": "false"}


@pytest.mark.parametrize("status", [404, 410])
def test_jira_delete_issue_already_gone_is_success(status):
    from app.connectors import jira_oauth

    with patch("app.connectors.jira_oauth.requests.delete",
               return_value=_resp(status)):
        assert jira_oauth.delete_issue(TOK, CLOUD, "PROJ-1") is True


def test_jira_delete_issue_403_is_forbidden_not_reconnect():
    """The split that matters: a 403 on delete is a project-permission fact,
    so it must NOT surface as the reconnect error every other Jira write
    raises for the same status."""
    from app.connectors import jira_oauth

    with patch("app.connectors.jira_oauth.requests.delete",
               return_value=_resp(403, "no permission")):
        with pytest.raises(TrackerDeleteForbiddenError):
            jira_oauth.delete_issue(TOK, CLOUD, "PROJ-1")
    assert not issubclass(TrackerDeleteForbiddenError, jira_oauth.JiraAuthExpiredError)


def test_jira_delete_issue_401_is_reconnect():
    from app.connectors import jira_oauth

    with patch("app.connectors.jira_oauth.requests.delete",
               return_value=_resp(401, "bad token")):
        with pytest.raises(jira_oauth.JiraAuthExpiredError):
            jira_oauth.delete_issue(TOK, CLOUD, "PROJ-1")


def test_jira_delete_issue_other_error_is_502():
    from app.connectors import jira_oauth

    with patch("app.connectors.jira_oauth.requests.delete",
               return_value=_resp(500, "boom")):
        with pytest.raises(HTTPException) as e:
            jira_oauth.delete_issue(TOK, CLOUD, "PROJ-1")
    assert e.value.status_code == 502


def test_jira_delete_issue_comment_targets_the_nested_path():
    from app.connectors import jira_oauth

    with patch("app.connectors.jira_oauth.requests.delete",
               return_value=_resp(204)) as d:
        assert jira_oauth.delete_issue_comment(TOK, CLOUD, "PROJ-1", "10101") is True
    assert d.call_args[0][0].endswith("/issue/PROJ-1/comment/10101")


# ── ClickUp ──────────────────────────────────────────────────────────────────


def test_clickup_delete_task():
    from app.connectors import clickup_oauth

    with patch("app.connectors.clickup_oauth.requests.delete",
               return_value=_resp(200)) as d:
        assert clickup_oauth.delete_task(TOK, "abc123") is True
    assert d.call_args[0][0].endswith("/task/abc123")
    # Raw-token auth (no "Bearer " prefix) — ClickUp's long-standing quirk.
    assert d.call_args[1]["headers"]["Authorization"] == TOK


def test_clickup_delete_task_empty_body_is_not_parsed():
    """ClickUp answers a delete with an empty payload. `_write` would call
    resp.json() and blow up on it, which is why deletes have their own helper —
    a json() that raises must never reach the caller."""
    from app.connectors import clickup_oauth

    resp = _resp(200)
    resp.json.side_effect = ValueError("No JSON object could be decoded")
    with patch("app.connectors.clickup_oauth.requests.delete", return_value=resp):
        assert clickup_oauth.delete_task(TOK, "abc123") is True


def test_clickup_delete_task_comment_is_keyed_by_comment_id_alone():
    """ClickUp's comment endpoints are not nested under the task — the stored
    `ticket_comments.tracker_comment_id` is the whole address."""
    from app.connectors import clickup_oauth

    with patch("app.connectors.clickup_oauth.requests.delete",
               return_value=_resp(200)) as d:
        assert clickup_oauth.delete_task_comment(TOK, "999") is True
    assert d.call_args[0][0].endswith("/comment/999")


def test_clickup_remove_task_tag_url_encodes_the_name():
    from app.connectors import clickup_oauth

    with patch("app.connectors.clickup_oauth.requests.delete",
               return_value=_resp(200)) as d:
        assert clickup_oauth.remove_task_tag(TOK, "abc123", "needs design") is True
    assert d.call_args[0][0].endswith("/task/abc123/tag/needs%20design")


def test_clickup_delete_checklist_item_and_checklist():
    from app.connectors import clickup_oauth

    with patch("app.connectors.clickup_oauth.requests.delete",
               return_value=_resp(200)) as d:
        clickup_oauth.delete_checklist_item(TOK, "cl1", "it1")
        clickup_oauth.delete_checklist(TOK, "cl1")
    paths = [c[0][0] for c in d.call_args_list]
    assert paths[0].endswith("/checklist/cl1/checklist_item/it1")
    assert paths[1].endswith("/checklist/cl1")


@pytest.mark.parametrize("status", [404, 410])
def test_clickup_delete_already_gone_is_success(status):
    from app.connectors import clickup_oauth

    with patch("app.connectors.clickup_oauth.requests.delete",
               return_value=_resp(status)):
        assert clickup_oauth.delete_task(TOK, "abc123") is True


def test_clickup_delete_403_is_forbidden_not_reconnect():
    from app.connectors import clickup_oauth

    with patch("app.connectors.clickup_oauth.requests.delete",
               return_value=_resp(403, "forbidden")):
        with pytest.raises(TrackerDeleteForbiddenError):
            clickup_oauth.delete_task(TOK, "abc123")


def test_clickup_delete_401_is_reconnect():
    from app.connectors import clickup_oauth

    with patch("app.connectors.clickup_oauth.requests.delete",
               return_value=_resp(401, "bad token")):
        with pytest.raises(clickup_oauth.ClickUpAuthExpiredError):
            clickup_oauth.delete_task(TOK, "abc123")


def test_clickup_get_task_checklists_normalizes():
    from app.connectors import clickup_oauth

    resp = MagicMock(status_code=200, ok=True)
    resp.json.return_value = {
        "id": "abc123",
        "checklists": [
            {"id": 7, "name": "Child issues",
             "items": [{"id": 71, "name": "Write the migration"},
                       {"id": 72, "name": "Backfill"},
                       {"name": "no id — dropped"}]},
            {"name": "no id — dropped"},
        ],
    }
    with patch("app.connectors.clickup_oauth.requests.get", return_value=resp):
        out = clickup_oauth.get_task_checklists(TOK, "abc123")
    assert out == [{
        "id": "7", "name": "Child issues",
        "items": [{"id": "71", "name": "Write the migration"},
                  {"id": "72", "name": "Backfill"}],
    }]


def test_clickup_get_task_checklists_degrades_to_empty():
    """A read failure means "reconcile nothing" — never a failed sync pass."""
    from app.connectors import clickup_oauth

    resp = MagicMock(status_code=500, ok=False, text="boom")
    resp.raise_for_status.side_effect = RuntimeError("boom")
    with patch("app.connectors.clickup_oauth.requests.get", return_value=resp):
        assert clickup_oauth.get_task_checklists(TOK, "abc123") == []


# ── Asana ────────────────────────────────────────────────────────────────────


def test_asana_delete_task():
    from app.connectors import asana_oauth

    with patch("app.connectors.asana_oauth.requests.delete",
               return_value=_resp(200)) as d:
        assert asana_oauth.delete_task(TOK, "gid1") is True
    assert d.call_args[0][0].endswith("/tasks/gid1")
    assert d.call_args[1]["headers"]["Authorization"] == f"Bearer {TOK}"


def test_asana_delete_task_comment_targets_the_story():
    """A comment IS a story in Asana — the gid add_task_comment returned."""
    from app.connectors import asana_oauth

    with patch("app.connectors.asana_oauth.requests.delete",
               return_value=_resp(204)) as d:
        assert asana_oauth.delete_task_comment(TOK, "story9") is True
    assert d.call_args[0][0].endswith("/stories/story9")


@pytest.mark.parametrize("status", [404, 410])
def test_asana_delete_already_gone_is_success(status):
    from app.connectors import asana_oauth

    with patch("app.connectors.asana_oauth.requests.delete",
               return_value=_resp(status)):
        assert asana_oauth.delete_task(TOK, "gid1") is True


def test_asana_delete_403_is_forbidden_not_reconnect():
    """_raise_for maps 401 and 403 alike to the reconnect error; deletes must
    not, or an un-deletable system story would nag the user to reconnect."""
    from app.connectors import asana_oauth

    with patch("app.connectors.asana_oauth.requests.delete",
               return_value=_resp(403, "forbidden")):
        with pytest.raises(TrackerDeleteForbiddenError):
            asana_oauth.delete_task_comment(TOK, "story9")


def test_asana_delete_401_is_reconnect():
    from app.connectors import asana_oauth

    with patch("app.connectors.asana_oauth.requests.delete",
               return_value=_resp(401, "bad token")):
        with pytest.raises(asana_oauth.AsanaAuthExpiredError):
            asana_oauth.delete_task(TOK, "gid1")


def test_asana_delete_other_error_is_502():
    from app.connectors import asana_oauth

    with patch("app.connectors.asana_oauth.requests.delete",
               return_value=_resp(500, "boom")):
        with pytest.raises(HTTPException) as e:
            asana_oauth.delete_task(TOK, "gid1")
    assert e.value.status_code == 502
