"""ClickUp lookup — session, live reads, rendering, tracker generalization.

Mirrors tests/test_jira_lookup.py: no network/LLM/DB — `requests`, the token
store and the connection row are patched.

The tracker tests cover the bug this adapter exists to fix: a ClickUp-only
company asking "show me my open tickets" used to be told to connect Jira.
"""
from __future__ import annotations

import json

import pytest
import requests

import app.connectors.clickup_fetch as cf
from app.connector_lookup import answer as ca
from app.connector_lookup import tracker
from app.connector_lookup.clickup import PROVIDER


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.ok = 200 <= status < 300
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            err = requests.HTTPError(f"http {self.status_code}")
            err.response = self
            raise err


def _session():
    return cf.ClickUpSession(access_token="tok", team_ids=["T1"],
                             team_names={"T1": "Acme"})


def _task(tid="abc1", name="Fix checkout", status="in progress", **extra):
    payload = {
        "id": tid, "name": name,
        "status": {"status": status},
        "list": {"name": "Sprint 4"},
        "priority": {"priority": "high"},
        "assignees": [{"username": "ada"}],
        "date_updated": "1750000000000",
        "url": f"https://app.clickup.com/t/{tid}",
        "text_content": "checkout page 500s on submit",
    }
    payload.update(extra)
    return payload


# ── session (tenancy + the raw-token auth quirk) ──────────────────────────────

def test_open_session_none_when_not_connected(monkeypatch):
    from app import db

    monkeypatch.setattr(db, "get_connection", lambda cid, prov: None)
    assert cf.open_session("co") is None


def test_open_session_reads_only_the_authenticated_company(monkeypatch):
    """Cross-tenant isolation: the credential is fetched by (company, provider)
    through db.get_connection — the one chokepoint — and nothing else."""
    from app import db

    seen = []
    monkeypatch.setattr(db, "get_connection",
                        lambda cid, prov: seen.append((cid, prov)) or {"token_json_encrypted": "enc"})
    monkeypatch.setattr(cf, "decrypt_token_json", lambda enc: json.dumps({"access_token": "tok-a"}))
    monkeypatch.setattr(cf.requests, "get",
                        lambda *a, **k: _Resp({"teams": [{"id": "T1", "name": "Acme"}]}))
    session = cf.open_session("co-a")
    assert seen == [("co-a", "clickup")]
    assert session.access_token == "tok-a"
    assert session.team_ids == ["T1"]


def test_session_sends_the_raw_token_with_no_bearer_prefix(monkeypatch):
    """ClickUp's documented quirk, and the easiest thing to get wrong: the token
    goes RAW in Authorization. A `Bearer ` prefix 401s every read."""
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured.update({"url": url, "headers": headers, "timeout": timeout})
        return _Resp({"tasks": []})

    monkeypatch.setattr(cf.requests, "get", fake_get)
    cf.search_tasks(_session(), text="x")
    assert captured["headers"]["Authorization"] == "tok"
    assert not captured["headers"]["Authorization"].startswith("Bearer")
    # …and the framework's HTTP bound, not the puller's 30s.
    assert captured["timeout"] == 15


def test_open_session_none_when_token_sees_no_workspace(monkeypatch):
    from app import db

    monkeypatch.setattr(db, "get_connection", lambda cid, prov: {"token_json_encrypted": "enc"})
    monkeypatch.setattr(cf, "decrypt_token_json", lambda enc: json.dumps({"access_token": "t"}))
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: _Resp({"teams": []}))
    assert cf.open_session("co") is None


def test_open_session_none_when_token_unreadable(monkeypatch):
    from app import db
    from app.connectors.tokens import TokenEncryptionError

    monkeypatch.setattr(db, "get_connection", lambda cid, prov: {"token_json_encrypted": "enc"})

    def boom(enc):
        raise TokenEncryptionError("bad key")

    monkeypatch.setattr(cf, "decrypt_token_json", boom)
    assert cf.open_session("co") is None


def test_rejected_token_raises_reconnect_error(monkeypatch):
    """Case 2: ClickUp issues no refresh token, so a 401 means reconnect — and it
    must reach the model as words, not a traceback."""
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: _Resp({"err": "auth"}, status=401))
    with pytest.raises(cf.ClickUpAuthError) as exc:
        cf.search_tasks(_session(), text="x")
    assert "reconnect ClickUp" in str(exc.value)


def test_rejected_token_reaches_the_model_as_words(monkeypatch):
    """…and the framework turns that exception into a readable tool result, so the
    loop can tell the user to reconnect instead of erroring out."""
    monkeypatch.setattr(PROVIDER, "open_session",
                        lambda eid: ca.LookupSession(provider="clickup", handle=_session()))
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: _Resp({}, status=401))
    out = ca.answer(
        enterprise_id="co", question="my open tickets", providers=[PROVIDER],
        run_loop=lambda **k: k["dispatch"]("clickup_search_tasks", {"text": "x"}),
        log=lambda *a: None,
    )
    assert "reconnect ClickUp" in out["answer"]
    assert "Traceback" not in out["answer"]


# ── reads ────────────────────────────────────────────────────────────────────

def test_search_filters_client_side_and_reports_coverage(monkeypatch):
    """The v2 API has no text search, so the match is ours — and the result says
    how big the window it searched was, so the model can be honest about it."""
    pages = [
        {"tasks": [_task("a1", "Fix checkout"),
                   _task("a2", "Rename button", text_content="copy tweak")],
         "last_page": True},
    ]

    def fake_get(url, params=None, headers=None, timeout=None):
        return _Resp(pages[params["page"]])

    monkeypatch.setattr(cf.requests, "get", fake_get)
    rows, scanned = cf.search_tasks(_session(), text="checkout")
    assert [r["id"] for r in rows] == ["a1"]
    assert scanned == 2
    rendered = cf.render_search(rows, scanned)
    assert "a1: Fix checkout [in progress]" in rendered
    assert "searched the 2 most recently updated ClickUp tasks" in rendered


def test_search_pushes_status_down_to_the_api(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured.update(params or {})
        return _Resp({"tasks": [], "last_page": True})

    monkeypatch.setattr(cf.requests, "get", fake_get)
    cf.search_tasks(_session(), status="in review")
    assert captured["statuses[]"] == "in review"


def test_search_filters_by_list_name(monkeypatch):
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: _Resp(
        {"tasks": [_task("a1"), _task("a2", list={"name": "Backlog"})], "last_page": True}))
    rows, _ = cf.search_tasks(_session(), list_name="backlog")
    assert [r["id"] for r in rows] == ["a2"]


def test_search_page_walk_is_bounded(monkeypatch):
    """Bounded on purpose: no text filter upstream means an unbounded walk would
    scan a whole workspace for one chat question."""
    seen_pages = []

    def fake_get(url, params=None, headers=None, timeout=None):
        seen_pages.append(params["page"])
        return _Resp({"tasks": [_task(f"t{params['page']}-{i}") for i in range(100)]})

    monkeypatch.setattr(cf.requests, "get", fake_get)
    rows, scanned = cf.search_tasks(_session(), status="open", limit=1000)
    assert seen_pages == [0, 1, 2] == list(range(cf._MAX_PAGES))
    assert scanned == 300 and len(rows) == 300


def test_search_stops_at_the_row_limit(monkeypatch):
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: _Resp(
        {"tasks": [_task(f"t{i}") for i in range(100)]}))
    rows, _ = cf.search_tasks(_session())
    assert len(rows) == cf._SEARCH_LIMIT


def test_empty_search_says_what_it_searched(monkeypatch):
    """Case 3: no matches → an honest statement, nothing invented."""
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: _Resp({"tasks": [], "last_page": True}))
    rows, scanned = cf.search_tasks(_session(), text="zzz")
    assert rows == []
    assert "No matching ClickUp tasks" in cf.render_search(rows, scanned)


def test_get_task_full_parse_with_comments(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/comment"):
            return _Resp({"comments": [
                {"comment_text": "shipped a fix", "user": {"username": "grace"},
                 "date": "1750000000000"},
                {"comment_text": "", "user": {"username": "skip"}},
            ]})
        return _Resp(_task(markdown_description="## Repro\nsubmit 500s",
                           tags=[{"name": "billing"}], due_date="1750000000000"))

    monkeypatch.setattr(cf.requests, "get", fake_get)
    task = cf.get_task(_session(), "abc1")
    assert task["status"] == "in progress"
    assert task["assignees"] == ["ada"]
    assert task["tags"] == ["billing"]
    assert len(task["comments"]) == 1  # the empty one is dropped
    out = cf.render_task(task)
    assert "abc1: Fix checkout" in out
    assert "status: in progress" in out
    assert "description: ## Repro" in out
    assert "grace on 2025-06-15: shipped a fix" in out


def test_get_task_missing_returns_none(monkeypatch):
    monkeypatch.setattr(cf.requests, "get", lambda *a, **k: _Resp({"err": "not found"}, status=404))
    assert cf.get_task(_session(), "nope") is None


def test_get_task_comment_failure_is_non_fatal(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/comment"):
            raise requests.ConnectionError("boom")
        return _Resp(_task())

    monkeypatch.setattr(cf.requests, "get", fake_get)
    task = cf.get_task(_session(), "abc1")
    assert task["comments"] == []


# ── adapter dispatch ─────────────────────────────────────────────────────────

def test_dispatch_routes_to_tools(monkeypatch):
    session = ca.LookupSession(provider="clickup", handle=_session())
    monkeypatch.setattr(cf, "search_tasks", lambda *a, **k: ([{
        "id": "a1", "name": "Fix checkout", "status": "open", "list": None,
        "assignee": None, "updated": None, "url": None, "priority": None}], 7))
    monkeypatch.setattr(cf, "get_task", lambda sess, tid: {
        "id": tid, "name": "x", "status": "open"})
    assert "a1: Fix checkout" in PROVIDER.dispatch(session, "clickup_search_tasks", {"text": "x"})
    assert "abc1: x" in PROVIDER.dispatch(session, "clickup_get_task", {"task_id": "abc1"})
    assert "required" in PROVIDER.dispatch(session, "clickup_get_task", {})
    assert "unknown tool" in PROVIDER.dispatch(session, "nope", {})


def test_dispatch_missing_task_message(monkeypatch):
    monkeypatch.setattr(cf, "get_task", lambda sess, tid: None)
    session = ca.LookupSession(provider="clickup", handle=_session())
    out = PROVIDER.dispatch(session, "clickup_get_task", {"task_id": "zzz"})
    assert "no ClickUp task found with id zzz" in out


def test_system_block_states_the_search_and_write_limits():
    block = PROVIDER.system_block()
    assert "no full-text task search" in block
    assert "READ-ONLY" in block


def test_adapter_offers_no_write_tool():
    """ClickUp has no confirm-card contract, so chat gets reads and nothing
    else — no propose/update tool to accidentally hand a model."""
    names = {t["name"] for t in PROVIDER.tools()}
    assert names == {"clickup_search_tasks", "clickup_get_task"}


# ── tracker generalization (the bug this fixes) ───────────────────────────────

def _connections(monkeypatch, connected: set[str]):
    from app import db

    monkeypatch.setattr(
        db, "get_connection",
        lambda cid, prov: {"token_json_encrypted": "enc"} if prov in connected else None,
    )
    monkeypatch.setattr(db, "list_connections",
                        lambda cid: [{"provider": p} for p in sorted(connected)])
    monkeypatch.setattr(db, "list_slack_connections", lambda cid: [])


def test_tracker_picks_jira_when_connected(monkeypatch):
    _connections(monkeypatch, {"jira"})
    assert tracker.pick("co", "show me my open tickets") == "jira"


def test_tracker_picks_clickup_for_a_clickup_only_company(monkeypatch):
    """The reported bug: routing was tracker-agnostic, execution was Jira-only, so
    this company was told to connect a tracker it doesn't use."""
    _connections(monkeypatch, {"clickup"})
    assert tracker.pick("co", "show me my open tickets") == "clickup"


def test_tracker_prefers_the_tracker_the_question_names(monkeypatch):
    _connections(monkeypatch, {"jira", "clickup"})
    assert tracker.pick("co", "what's in clickup right now") == "clickup"
    assert tracker.pick("co", "what's in jira right now") == "jira"
    # Neither named → Jira, the richer surface (reads + propose→confirm).
    assert tracker.pick("co", "show me my open tickets") == "jira"


def test_tracker_none_when_no_tracker_connected(monkeypatch):
    _connections(monkeypatch, set())
    assert tracker.pick("co", "show me my open tickets") is None


def test_tracker_answer_delegates_to_jira_lookup_unchanged(monkeypatch):
    _connections(monkeypatch, {"jira"})
    from app import jira_lookup

    seen = {}
    monkeypatch.setattr(jira_lookup, "answer", lambda **k: seen.update(k) or {"answer": "jira"})
    out = tracker.answer(enterprise_id="co", question="status of PROJ-1", history=[])
    assert out == {"answer": "jira"}
    assert seen == {"enterprise_id": "co", "question": "status of PROJ-1", "history": []}


def test_tracker_answer_runs_the_clickup_loop(monkeypatch):
    _connections(monkeypatch, {"clickup"})
    seen = {}
    monkeypatch.setattr(ca, "answer", lambda **k: seen.update(k) or {"answer": "clickup"})
    out = tracker.answer(enterprise_id="co", question="show my open tickets")
    assert out == {"answer": "clickup"}
    assert [p.provider for p in seen["providers"]] == ["clickup"]
    assert seen["skill_action"] == "ClickUp lookup"


def test_tracker_answer_with_no_tracker_names_both_and_what_is_connected(monkeypatch):
    _connections(monkeypatch, set())
    from app import db

    monkeypatch.setattr(db, "list_connections", lambda cid: [{"provider": "fireflies"}])
    out = tracker.answer(enterprise_id="co", question="show my open tickets")
    assert "Connect **Jira** or **ClickUp**" in out["answer"]
    assert "Connected right now: Fireflies." in out["answer"]
    assert out["_skill_action"] == "Tracker lookup"


def test_tracker_connection_check_survives_a_db_failure(monkeypatch):
    from app import db

    def boom(*a, **k):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(db, "get_connection", boom)
    monkeypatch.setattr(db, "list_connections", lambda cid: [])
    monkeypatch.setattr(db, "list_slack_connections", lambda cid: [])
    out = tracker.answer(enterprise_id="co", question="show my open tickets")
    assert "Connect **Jira** or **ClickUp**" in out["answer"]
