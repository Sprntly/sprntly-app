"""POST /v1/jira/write — applying a change the user confirmed in chat.

This route is the only path from a conversation to a Jira mutation. The agent
cannot reach it: the lookup skill can propose a change, and applying it takes an
explicit request from the browser. These tests pin the parts that make that
split trustworthy — partial success reported honestly, and a disconnected
tenant refused rather than silently doing nothing.
"""
from __future__ import annotations

import app.connectors.jira_fetch as jf


def _session():
    return jf.JiraSession(access_token="t", cloud_id="cid",
                          site_url="https://acme.atlassian.net")


def test_write_applies_fields_status_and_comment(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    monkeypatch.setattr(jf, "open_session", lambda cid: _session())
    monkeypatch.setattr(jf, "apply_field_updates",
                        lambda s, k, ch: {"ok": True, "updated": sorted(ch)})
    monkeypatch.setattr(jf, "apply_transition",
                        lambda s, k, st: {"ok": True, "status": st})
    monkeypatch.setattr(jf, "add_comment",
                        lambda s, k, txt: {"ok": True, "comment_id": "1"})

    resp = t.client.post("/v1/jira/write", json={
        "issue_key": "PROJ-5",
        "fields": {"duedate": "2028-08-31"},
        "to_status": "In Review",
        "comment": "on it",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert set(body["applied"]) == {"fields", "status", "comment"}
    assert body["failed"] == []


def test_write_reports_partial_failure_honestly(tenant_client, monkeypatch):
    """A request can set fields, move status and comment; any part can fail on
    its own. Collapsing that into one boolean would tell the user the whole
    change landed when half of it did."""
    t = tenant_client.make(slug="acme")
    monkeypatch.setattr(jf, "open_session", lambda cid: _session())
    monkeypatch.setattr(jf, "apply_field_updates",
                        lambda s, k, ch: {"ok": True, "updated": ["duedate"]})
    monkeypatch.setattr(jf, "apply_transition",
                        lambda s, k, st: {"ok": False, "error": "not reachable"})

    resp = t.client.post("/v1/jira/write", json={
        "issue_key": "PROJ-5",
        "fields": {"duedate": "2028-08-31"},
        "to_status": "Released",
    })
    body = resp.json()
    assert body["ok"] is False
    assert body["applied"] == ["fields"]
    assert body["failed"] == ["status"]
    assert body["status"]["error"] == "not reachable"


def test_write_without_jira_connected_is_refused(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    monkeypatch.setattr(jf, "open_session", lambda cid: None)
    resp = t.client.post("/v1/jira/write",
                         json={"issue_key": "PROJ-5", "to_status": "Done"})
    assert resp.status_code == 409


def test_write_with_nothing_to_change_is_rejected(tenant_client, monkeypatch):
    t = tenant_client.make(slug="acme")
    monkeypatch.setattr(jf, "open_session", lambda cid: _session())
    resp = t.client.post("/v1/jira/write", json={"issue_key": "PROJ-5"})
    assert resp.status_code == 400


def test_write_requires_auth(tenant_client, monkeypatch):
    """Unauthenticated callers never reach Jira — the write is scoped to the
    caller's own connected workspace."""
    t = tenant_client.make(slug="acme")
    monkeypatch.setattr(jf, "open_session", lambda cid: _session())
    t.client.headers.pop("Authorization", None)
    resp = t.client.post("/v1/jira/write",
                         json={"issue_key": "PROJ-5", "to_status": "Done"})
    assert resp.status_code in (401, 403)
