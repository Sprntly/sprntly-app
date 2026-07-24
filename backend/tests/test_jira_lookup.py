"""On-demand Jira-lookup — intent, session, live reads, rendering, answer loop.

No network/LLM/DB: requests, the token store, accessible-resources, and the tool
loop are patched in the jira_fetch / jira_lookup namespaces.
"""
from __future__ import annotations

import json

import app.connectors.jira_fetch as jf
import app.jira_lookup as jl
from app.skill_router import is_jira_lookup


# ── intent detection ─────────────────────────────────────────────────────────

def test_is_jira_lookup_positive():
    for q in [
        "what's the status of PROJ-142?",
        "summarize the checkout epic in Jira",
        "pull up ABC-1023",
        "show me the details of BILL-7",
        "which tickets are open on the billing board in jira",
        "look up issue DEV-88",
        "give me the epic PLAT-12 and its child tickets",
    ]:
        assert is_jira_lookup(q), q


def test_is_jira_lookup_negative():
    for q in [
        "generate a PRD for onboarding",
        "prioritize these features",
        "what's our churn rate?",
        "we shipped in the UTF-8 encoding update",   # lowercase false friend, no jira/PM context
        "summarize this document",
        "create a ticket for the login bug",         # create → user-stories, not a read
        "push these stories to jira",                # write → veto
        "update PROJ-142 to done",                   # write cmd, no PM-noun/verb → no match
        # Merely NAMING Jira (as a competitor) is not a lookup — must not hijack
        # a competitive-intelligence request.
        "do a competitive analysis of Linear, Jira and Asana",
        "how does our roadmap compare to Jira and Asana?",
    ]:
        assert not is_jira_lookup(q), q


def test_is_jira_lookup_sticky_followup_in_thread():
    # A filter follow-up ("get all in to-do status") carries no "jira" word and no
    # key, so it misses statelessly — but inside an active Jira thread it must
    # route back to Jira instead of dead-ending at the scope gate.
    thread = [
        {"role": "user", "content": "can you get me tickets on jira?"},
        {"role": "assistant", "content": "Sure! Provide an Issue Key, keywords, "
                                         "project, or workflow status."},
    ]
    for q in ["get all in to do status", "only the in progress ones",
              "which are assigned to me", "the PROJ project ones"]:
        assert is_jira_lookup(q, thread), q
        assert not is_jira_lookup(q), q  # stateless miss — the thread is what carries it


def test_is_jira_lookup_followup_needs_both_thread_and_filter():
    thread = [{"role": "assistant", "content": "Here is KAN-1033 from Jira."}]
    # A generic pivot inside a Jira thread is NOT a Jira filter → falls through.
    assert not is_jira_lookup("what's our churn rate?", thread)
    assert not is_jira_lookup("prioritize these features", thread)
    # A Jira-style filter with NO Jira thread → also no match (avoids hijacking
    # unrelated conversations that happen to say "status").
    no_thread = [{"role": "assistant", "content": "Your NPS improved last month."}]
    assert not is_jira_lookup("get all in to do status", no_thread)


def test_is_jira_lookup_bare_key_needs_context():
    # A bare key with neither a PM noun nor a lookup verb doesn't hijack.
    assert not is_jira_lookup("the deploy for ABC-12 landed and metrics improved")
    # ...but add a lookup verb or PM noun and it does.
    assert is_jira_lookup("show ABC-12")
    assert is_jira_lookup("what is the ABC-12 ticket about")


# ── JQL + ADF helpers ────────────────────────────────────────────────────────

def test_build_search_jql_anchors_when_empty():
    # No filters → bounded with the created floor (unbounded JQL 400s on /search/jql).
    jql = jf._build_search_jql(text=None, project=None, status=None)
    assert 'created >= "2000-01-01"' in jql
    assert jql.endswith("ORDER BY updated DESC")


def test_build_search_jql_combines_and_escapes():
    jql = jf._build_search_jql(text='pay "now"', project="PROJ", status="In Progress")
    assert 'text ~ "pay \\"now\\""' in jql   # embedded quotes escaped
    assert 'project = "PROJ"' in jql
    assert 'status = "In Progress"' in jql
    assert " AND " in jql


def test_jql_str_escapes_backslash_before_quote():
    assert jf._jql_str('a\\b"c') == 'a\\\\b\\"c'


def test_adf_text_flattens_nested_doc():
    doc = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Hello"}]},
            {"type": "paragraph", "content": [
                {"type": "text", "text": "world"},
                {"type": "text", "text": "!"},
            ]},
        ],
    }
    assert jf._adf_text(doc) == "Hello world !"
    assert jf._adf_text(None) == ""


# ── fake requests ────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.ok = 200 <= status < 300

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


def _session():
    return jf.JiraSession(access_token="tok", cloud_id="cid", site_url="https://acme.atlassian.net")


# ── search ───────────────────────────────────────────────────────────────────

def test_search_parses_hits_and_builds_urls(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["jql"] = params["jql"]
        return _Resp({"issues": [
            {"key": "PROJ-1", "fields": {
                "summary": "Login fails", "status": {"name": "To Do"},
                "issuetype": {"name": "Bug"}, "priority": {"name": "High"},
                "assignee": {"displayName": "Ada"}, "updated": "2026-07-01",
            }},
        ]})

    monkeypatch.setattr(jf.requests, "get", fake_get)
    hits = jf.search(_session(), text="login")
    assert captured["url"].endswith("/search/jql")
    assert 'text ~ "login"' in captured["jql"]
    h = hits[0]
    assert h["key"] == "PROJ-1" and h["type"] == "Bug" and h["assignee"] == "Ada"
    assert h["url"] == "https://acme.atlassian.net/browse/PROJ-1"


def test_render_search_empty_and_nonempty():
    assert jf.render_search([]) == "No matching Jira issues."
    line = jf.render_search([{"key": "X-1", "summary": "s", "type": "Bug",
                              "status": "Done", "priority": None, "assignee": "Ada"}])
    assert "X-1: s" in line and "[Bug · Done]" in line and "@Ada" in line


# ── get_issue ────────────────────────────────────────────────────────────────

def _issue_payload(issue_type="Task", with_subtasks=False):
    fields = {
        "summary": "Checkout broken",
        "description": {"type": "doc", "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Repro steps"}]}]},
        "status": {"name": "In Progress"},
        "priority": {"name": "High"},
        "issuetype": {"name": issue_type},
        "project": {"name": "Payments"},
        "assignee": {"displayName": "Ada"},
        "reporter": {"displayName": "Grace"},
        "labels": ["billing"],
        "updated": "2026-07-02",
        "created": "2026-06-01",
        "parent": {"key": "PROJ-10", "fields": {"summary": "Epic: Payments"}},
        "subtasks": [
            {"key": "PROJ-3", "fields": {"summary": "Sub A", "status": {"name": "Done"}}},
        ] if with_subtasks else [],
    }
    return {"fields": fields}


def test_get_issue_full_parse_and_comments(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/comment"):
            return _Resp({"comments": [
                {"author": {"displayName": "Ada"}, "body": {"type": "doc", "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "on it"}]}]}},
                {"author": {"displayName": "Bob"}, "body": None},  # empty → skipped
            ]})
        return _Resp(_issue_payload("Task", with_subtasks=True))

    monkeypatch.setattr(jf.requests, "get", fake_get)
    issue = jf.get_issue(_session(), "PROJ-5")
    assert issue["key"] == "PROJ-5" and issue["type"] == "Task"
    assert issue["description"] == "Repro steps"
    assert issue["parent"]["key"] == "PROJ-10"
    assert issue["subtasks"][0]["key"] == "PROJ-3"
    assert issue["comments"] == [{"author": "Ada", "text": "on it"}]
    assert "children" not in issue  # non-epic → no children fetch


def test_get_issue_epic_fetches_children(monkeypatch):
    seen = {"jql": None}

    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/comment"):
            return _Resp({"comments": []})
        if url.endswith("/search/jql"):
            seen["jql"] = params["jql"]
            return _Resp({"issues": [
                {"key": "PROJ-6", "fields": {"summary": "child", "status": {"name": "To Do"},
                                             "issuetype": {"name": "Story"}}},
            ]})
        return _Resp(_issue_payload("Epic"))

    monkeypatch.setattr(jf.requests, "get", fake_get)
    issue = jf.get_issue(_session(), "PROJ-10")
    assert issue["type"] == "Epic"
    assert 'parent = "PROJ-10"' in seen["jql"]
    assert issue["children"][0]["key"] == "PROJ-6"


def test_get_issue_missing_returns_none(monkeypatch):
    monkeypatch.setattr(jf.requests, "get",
                        lambda *a, **k: _Resp({}, status=404))
    assert jf.get_issue(_session(), "NOPE-1") is None


def test_render_issue_includes_sections(monkeypatch):
    issue = {
        "key": "PROJ-5", "summary": "Checkout broken", "type": "Bug",
        "status": "In Progress", "priority": "High", "project": "Payments",
        "assignee": "Ada", "reporter": "Grace", "labels": ["billing"],
        "updated": "2026-07-02", "url": "https://acme.atlassian.net/browse/PROJ-5",
        "parent": {"key": "PROJ-10", "summary": "Epic"},
        "description": "Repro steps",
        "subtasks": [{"key": "PROJ-3", "summary": "Sub A", "status": "Done"}],
        "comments": [{"author": "Ada", "text": "on it"}],
    }
    out = jf.render_issue(issue)
    assert "PROJ-5: Checkout broken" in out
    assert "Bug · In Progress · High" in out
    assert "parent: PROJ-10" in out
    assert "description:\nRepro steps" in out
    assert "PROJ-3: Sub A [Done]" in out
    assert "Ada: on it" in out


# ── open_session ─────────────────────────────────────────────────────────────

def test_open_session_none_when_not_connected(monkeypatch):
    import app.db as db
    monkeypatch.setattr(db, "get_connection", lambda cid, prov: None)
    assert jf.open_session("co") is None


def test_open_session_resolves_site(monkeypatch):
    import app.db as db
    fresh = json.dumps({"access_token": "tok", "obtained_at": 10**12, "expires_in": 3600})
    monkeypatch.setattr(db, "get_connection",
                        lambda cid, prov: {"token_json_encrypted": "enc"})
    monkeypatch.setattr(jf, "decrypt_token_json", lambda enc: fresh)
    monkeypatch.setattr(jf, "get_accessible_resources",
                        lambda tok: [{"id": "cid", "url": "https://acme.atlassian.net"}])
    s = jf.open_session("co")
    assert s and s.cloud_id == "cid" and s.site_url == "https://acme.atlassian.net"
    assert s.base.endswith("/cid/rest/api/3")


def test_open_session_none_when_no_site(monkeypatch):
    import app.db as db
    fresh = json.dumps({"access_token": "tok", "obtained_at": 10**12, "expires_in": 3600})
    monkeypatch.setattr(db, "get_connection",
                        lambda cid, prov: {"token_json_encrypted": "enc"})
    monkeypatch.setattr(jf, "decrypt_token_json", lambda enc: fresh)
    monkeypatch.setattr(jf, "get_accessible_resources", lambda tok: [])
    assert jf.open_session("co") is None


# ── dispatch + answer ────────────────────────────────────────────────────────

def test_dispatch_routes_to_tools(monkeypatch):
    s = _session()
    monkeypatch.setattr(jf, "search", lambda *a, **k: [
        {"key": "P-1", "summary": "s", "type": "Bug", "status": "To Do",
         "priority": None, "assignee": None}])
    monkeypatch.setattr(jf, "get_issue", lambda sess, key:
                        {"key": key, "summary": "x", "type": "Task", "status": "Done"})
    dispatch = jl._make_dispatch(s)
    assert "P-1: s" in dispatch("jira_search", {"text": "pay"})
    assert "ABC-9: x" in dispatch("jira_get_issue", {"issue_key": "ABC-9"})
    assert "required" in dispatch("jira_get_issue", {})
    assert "unknown tool" in dispatch("nope", {})


def test_dispatch_missing_issue_message(monkeypatch):
    monkeypatch.setattr(jf, "get_issue", lambda sess, key: None)
    out = jl._make_dispatch(_session())("jira_get_issue", {"issue_key": "NOPE-1"})
    assert "no Jira issue found with key NOPE-1" in out


def test_answer_not_connected(monkeypatch):
    monkeypatch.setattr(jl.jira_fetch, "open_session", lambda cid: None)
    p = jl.answer(enterprise_id="co", question="status of PROJ-1")
    assert "Jira isn't connected" in p["answer"]
    assert p["_skill_source"] == "jira-lookup"


def test_answer_runs_tool_loop_and_wraps(monkeypatch):
    monkeypatch.setattr(jl.jira_fetch, "open_session", lambda cid: _session())
    captured = {}

    def fake_loop(**k):
        captured.update(k)
        return "PROJ-142 is In Progress, assigned to Ada."

    monkeypatch.setattr(jl, "run_tool_loop", fake_loop)
    monkeypatch.setattr(jl, "_log", lambda *a, **k: None)
    p = jl.answer(enterprise_id="co", question="what's the status of PROJ-142?")
    assert p["answer"] == "PROJ-142 is In Progress, assigned to Ada."
    assert p["_skill_source"] == "jira-lookup"
    assert p["key_points"] == [] and p["citations"] == []
    # Both read-only tools were offered; the question rode in the user turn.
    names = {t["name"] for t in captured["tools"]}
    assert names == {"jira_search", "jira_get_issue"}
    assert "PROJ-142" in captured["user"]


def test_answer_empty_result_degrades(monkeypatch):
    monkeypatch.setattr(jl.jira_fetch, "open_session", lambda cid: _session())
    monkeypatch.setattr(jl, "run_tool_loop", lambda **k: "   ")
    monkeypatch.setattr(jl, "_log", lambda *a, **k: None)
    p = jl.answer(enterprise_id="co", question="status of ZZZ-9")
    assert "couldn't find" in p["answer"]


def test_answer_tool_loop_failure_degrades(monkeypatch):
    monkeypatch.setattr(jl.jira_fetch, "open_session", lambda cid: _session())

    def boom(**k):
        raise RuntimeError("api down")

    monkeypatch.setattr(jl, "run_tool_loop", boom)
    p = jl.answer(enterprise_id="co", question="status of PROJ-1")
    assert "couldn't reach Jira" in p["answer"]
    assert p["_skill_source"] == "jira-lookup"
