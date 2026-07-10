"""Unit tests for the Jira assignee helpers in app.connectors.jira_oauth:
list_assignable_users (member picker) plus the assignee_account_id field on
create_issue / update_issue. All Atlassian HTTP is mocked at the requests layer.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.connectors import jira_oauth


def _resp(status=200, json_body=None):
    m = MagicMock()
    m.status_code = status
    m.ok = 200 <= status < 300
    m.json.return_value = json_body if json_body is not None else []
    m.text = ""
    return m


# ── list_assignable_users ────────────────────────────────────────────────────


def test_list_assignable_users_maps_fields():
    page = [
        {"accountId": "acc-1", "displayName": "Sam Rivera",
         "emailAddress": "sam@acme.co", "active": True,
         "avatarUrls": {"24x24": "https://a/24"}},
        {"accountId": "acc-2", "displayName": "Jo Lee",
         "emailAddress": None, "active": False, "avatarUrls": {}},
    ]
    with patch.object(jira_oauth.requests, "get", return_value=_resp(200, page)) as mg:
        out = jira_oauth.list_assignable_users("tok", "cloud-1", "KAN")
    assert out == [
        {"accountId": "acc-1", "displayName": "Sam Rivera", "email": "sam@acme.co",
         "active": True, "avatarUrl": "https://a/24"},
        {"accountId": "acc-2", "displayName": "Jo Lee", "email": None,
         "active": False, "avatarUrl": None},
    ]
    # project + auth threaded into the assignable/search call.
    assert mg.call_args.args[0].endswith("/rest/api/3/user/assignable/search")
    assert mg.call_args.kwargs["params"]["project"] == "KAN"
    assert mg.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"
    assert "query" not in mg.call_args.kwargs["params"]


def test_list_assignable_users_forwards_query():
    with patch.object(jira_oauth.requests, "get", return_value=_resp(200, [])) as mg:
        jira_oauth.list_assignable_users("tok", "c", "KAN", query="sam")
    assert mg.call_args.kwargs["params"]["query"] == "sam"


def test_list_assignable_users_skips_rows_without_account_id():
    page = [{"displayName": "no id"}, {"accountId": "acc-1", "displayName": "ok"}]
    with patch.object(jira_oauth.requests, "get", return_value=_resp(200, page)):
        out = jira_oauth.list_assignable_users("tok", "c", "KAN")
    assert [u["accountId"] for u in out] == ["acc-1"]


def test_list_assignable_users_empty_on_error():
    with patch.object(jira_oauth.requests, "get", return_value=_resp(403)):
        assert jira_oauth.list_assignable_users("tok", "c", "KAN") == []


def test_list_assignable_users_paginates_until_short_page():
    full = [{"accountId": f"a{i}"} for i in range(50)]
    tail = [{"accountId": "a50"}]
    with patch.object(jira_oauth.requests, "get",
                      side_effect=[_resp(200, full), _resp(200, tail)]) as mg:
        out = jira_oauth.list_assignable_users("tok", "c", "KAN")
    assert len(out) == 51
    assert mg.call_count == 2
    assert mg.call_args_list[1].kwargs["params"]["startAt"] == 50


# ── assignee field on create_issue / update_issue ────────────────────────────


def test_create_issue_sets_assignee():
    created = _resp(200, {"id": "1", "key": "KAN-9"})
    with (
        patch.object(jira_oauth.requests, "post", return_value=created) as mp,
        patch.object(jira_oauth, "_site_url_for_cloud", return_value="https://x.net"),
    ):
        jira_oauth.create_issue(
            "tok", "c", project_key="KAN", summary="S",
            assignee_account_id="acc-42",
        )
    fields = mp.call_args.kwargs["json"]["fields"]
    assert fields["assignee"] == {"accountId": "acc-42"}


def test_create_issue_omits_assignee_when_none():
    created = _resp(200, {"id": "1", "key": "KAN-9"})
    with (
        patch.object(jira_oauth.requests, "post", return_value=created) as mp,
        patch.object(jira_oauth, "_site_url_for_cloud", return_value=None),
    ):
        jira_oauth.create_issue("tok", "c", project_key="KAN", summary="S")
    assert "assignee" not in mp.call_args.kwargs["json"]["fields"]


def test_update_issue_reassigns():
    with (
        patch.object(jira_oauth.requests, "put", return_value=_resp(204)) as mp,
        patch.object(jira_oauth, "_site_url_for_cloud", return_value=None),
    ):
        jira_oauth.update_issue("tok", "c", "KAN-1", assignee_account_id="acc-7")
    assert mp.call_args.kwargs["json"]["fields"]["assignee"] == {"accountId": "acc-7"}


def test_update_issue_unassigns_on_empty_string():
    with (
        patch.object(jira_oauth.requests, "put", return_value=_resp(204)) as mp,
        patch.object(jira_oauth, "_site_url_for_cloud", return_value=None),
    ):
        jira_oauth.update_issue("tok", "c", "KAN-1", assignee_account_id="")
    # "" is our explicit unassign sentinel → Jira's accountId:null.
    assert mp.call_args.kwargs["json"]["fields"]["assignee"] == {"accountId": None}
