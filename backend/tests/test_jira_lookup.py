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


def test_is_jira_lookup_tracker_agnostic_pm_noun():
    # The PM noun ("ticket"/"issue"/"epic"...) + a read verb is the PRIMARY
    # trigger — no "jira" word or issue key required. This is what makes the
    # path serve any connected tracker (Jira today, Asana / ClickUp later): the
    # bare "get me tickets" must route to the live lookup, not the stale KG.
    for q in [
        "get me tickets",
        "get me my tickets",
        "show me the open bugs",
        "find the checkout epic",
        "pull up the tickets in review",
        "get all my stories",
    ]:
        assert is_jira_lookup(q), q


def test_is_jira_lookup_negative():
    for q in [
        "generate a PRD for onboarding",
        "prioritize these features",
        "what's our churn rate?",
        "we shipped in the UTF-8 encoding update",   # lowercase false friend, no jira/PM context
        "summarize this document",
        "create a ticket for the login bug",         # create → user-stories, not this path
        "push these stories to jira",                # push flow → veto
        "delete PROJ-142",                           # unimplemented → veto, not a silent no-op
        # Merely NAMING Jira (as a competitor) is not a lookup — must not hijack
        # a competitive-intelligence request.
        "do a competitive analysis of Linear, Jira and Asana",
        "how does our roadmap compare to Jira and Asana?",
    ]:
        assert not is_jira_lookup(q), q


def test_is_jira_lookup_routes_changes_to_an_existing_issue():
    """Changing an issue is this path's job now — it can propose an edit that the
    user confirms. These used to be vetoed and answered as prose by a skill with
    no ability to touch Jira."""
    for q in [
        "update PROJ-142 to done",
        "update the duedate on PROJ-142 to august 31 2028",
        "move DEV-88 to In Review",
        "assign the login bug to Ada",
        "set the priority on ABC-7 to High",
        "add a comment on PROJ-1 saying we're blocked",
    ]:
        assert is_jira_lookup(q), q


def test_is_jira_lookup_change_needs_an_issue_in_sight():
    """A mutation verb alone is not a tracker command — without a key or a PM
    noun it is someone talking about something else entirely."""
    for q in [
        "update the roadmap",
        "change our pricing page",
        "move the launch date",
    ]:
        assert not is_jira_lookup(q), q


def test_is_jira_lookup_bare_this_followup():
    """"give me full details on this" — the most natural follow-up there is.

    Reported live: the previous turn had just fetched KAN-1033, and this message
    dropped to the generic agent, which replied that it had no details for that
    ticket. The anaphora rule only matched "this/that <noun>", so a bare "this"
    as the object of a preposition was invisible to it.
    """
    thread = [
        {"role": "user", "content": "get me the ticket about cars"},
        {"role": "assistant", "content": "KAN-1033 — Build a car driving feature (In Review)."},
    ]
    for q in [
        "give me full details on this",
        "more about this",
        "can you tell me more about that",
        "what's the description for this",
        "give me the details for this?",
    ]:
        assert is_jira_lookup(q, thread), q


def test_bare_this_does_not_hijack_a_pivot():
    """A bare this/that is only referential in the two positions the rule allows;
    it must not turn every sentence containing "that" into a tracker read."""
    thread = [
        {"role": "user", "content": "get me the ticket about cars"},
        {"role": "assistant", "content": "KAN-1033 — Build a car driving feature."},
    ]
    for q in [
        "i think that we should ship next week",
        "prioritize these features",          # pivot veto still wins
        "generate a PRD for this",            # PRD command, not a tracker read
    ]:
        assert not is_jira_lookup(q, thread), q


def test_is_jira_lookup_sticky_change_followup_in_thread():
    """"move it to In Review" names no issue at all — only the thread makes it
    resolvable, which is exactly how people talk once a ticket is on screen."""
    thread = [
        {"role": "user", "content": "get me the ticket about cars"},
        {"role": "assistant", "content": "KAN-1033 — Build a car driving feature (In Review)."},
    ]
    for q in ["move it to Done", "set its due date to august 31 2028",
              "assign it to me", "change the priority to High"]:
        assert is_jira_lookup(q, thread), q


def test_creation_veto_does_not_fire_on_a_word_inside_the_TITLE():
    """Reported live: a ticket titled "Build thread feature" could not be found.

    "get me ticket about car build thread" was vetoed by the word "build" — a
    creation verb sitting inside the TITLE being searched for — so a plain lookup
    fell through to the scope gate and answered "I can only help with your
    product work". Position decides it: a creation verb before the PM noun is a
    command about what to make; after it, it is part of what is being looked for.
    """
    for q in [
        "get me ticket about car build thread",
        "find the ticket called build thread feature",
        "show me the issue about creating invoices",
        "pull up the ticket for the make-model picker",
    ]:
        assert is_jira_lookup(q), q


def test_creation_veto_still_fires_when_the_verb_leads():
    for q in [
        "create a ticket for the login bug",
        "generate a PRD for onboarding",
        "push these stories to jira",
        "draft tickets from this doc",
    ]:
        assert not is_jira_lookup(q), q


def test_bare_issue_key_routes_inside_a_tracker_thread():
    """"how about KAN-1038" names a key and nothing else. Statelessly that is
    not enough (a passing mention must not hijack the chat), but once the thread
    is about tickets it is unambiguous — it answered from the stale KG instead,
    listing every OTHER key it knew about."""
    thread = [
        {"role": "user", "content": "get me ticket about car"},
        {"role": "assistant", "content": "KAN-1033 — Build a car driving feature."},
    ]
    for q in ["how about KAN-1038", "and KAN-4?", "KAN-999"]:
        assert is_jira_lookup(q, thread), q
    # Still not a lookup on its own words — the thread is what carries it.
    assert not is_jira_lookup("the deploy for ABC-12 landed and metrics improved")


def test_is_jira_lookup_attribute_request_without_a_pronoun():
    """"i want to see full detail" names WHAT is wanted but not what it belongs
    to — the thread already established that. Reported live: it fell through to
    the generic agent, which replied that it had no detail for the ticket the
    turn above had just listed."""
    thread = [
        {"role": "user", "content": "get me tickets about car"},
        {"role": "assistant", "content": "KAN-1033 — Build a car driving feature."},
    ]
    for q in [
        "i want to see full detail",
        "show me the description",
        "any comments on it",
        "who is the assignee",
    ]:
        assert is_jira_lookup(q, thread), q


def test_attribute_request_still_loses_to_a_pivot():
    """The pivot veto runs first, so a tracker-ish word inside a genuine subject
    change does not drag the conversation back to Jira."""
    thread = [
        {"role": "user", "content": "get me tickets about car"},
        {"role": "assistant", "content": "KAN-1033 — Build a car driving feature."},
    ]
    for q in ["what's the status of our roadmap?", "give me the details of our churn"]:
        assert not is_jira_lookup(q, thread), q


def test_single_search_hit_returns_the_full_issue(monkeypatch):
    """One match → the whole ticket, not four fields and an offer to fetch more.

    The lean field set is there for a twenty-row list; with a single hit the
    user has already told us which ticket they mean.
    """
    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/comment"):
            return _Resp({"comments": []})
        if url.endswith("/search/jql"):
            return _Resp({"issues": [
                {"key": "KAN-1033", "fields": {
                    "summary": "Build a car driving feature",
                    "status": {"name": "In Progress"}, "issuetype": {"name": "Task"},
                }},
            ]})
        return _Resp(_issue_payload("Task"))

    monkeypatch.setattr(jf.requests, "get", fake_get)
    out = jl._make_dispatch(_session())("jira_search", {"text": "car"})
    # The full render, not the one-line hit: description and the detail sections.
    assert "description:" in out
    assert "Repro steps" in out


def test_multiple_search_hits_stay_lean(monkeypatch):
    """Two or more matches keep the one-line-per-result list — expanding every
    hit is what the lean field set exists to avoid."""
    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/search/jql"):
            return _Resp({"issues": [
                {"key": "A-1", "fields": {"summary": "one", "status": {"name": "To Do"}}},
                {"key": "A-2", "fields": {"summary": "two", "status": {"name": "To Do"}}},
            ]})
        raise AssertionError("must not fetch a full issue for a multi-hit search")

    monkeypatch.setattr(jf.requests, "get", fake_get)
    out = jl._make_dispatch(_session())("jira_search", {"text": "x"})
    assert "A-1" in out and "A-2" in out
    assert "description:" not in out


# ── dispatch_records (AC1/AC2/AC3/AC4/AC5) ──────────────────────────────────


def test_dispatch_records_returns_none_for_any_tool_but_jira_search():
    from app.connector_lookup import jira as jira_adapter

    for name in ("jira_get_issue", "jira_editmeta", "jira_propose_change", "other"):
        assert jira_adapter.dispatch_records(_session(), name, {}) is None


def test_dispatch_records_multi_hit_has_no_records_but_matching_text(monkeypatch):
    """AC2/AC4: a multi-hit search keeps `dispatch`'s exact text (mutation-
    proof) but yields NO records — `jira_fetch.search`'s lean hit shape (no
    description, project or labels) cannot honestly become a puller-shaped
    RawRecord without a second HTTP call per hit, which this ticket says not
    to add."""
    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/search/jql"):
            return _Resp({"issues": [
                {"key": "A-1", "fields": {"summary": "one", "status": {"name": "To Do"}}},
                {"key": "A-2", "fields": {"summary": "two", "status": {"name": "To Do"}}},
            ]})
        raise AssertionError("must not fetch a full issue for a multi-hit search")

    from app.connector_lookup import jira as jira_adapter

    monkeypatch.setattr(jf.requests, "get", fake_get)
    text, records = jira_adapter.dispatch_records(
        _session(), "jira_search", {"text": "x"}
    )
    assert records is None
    assert text == jl._make_dispatch(_session())("jira_search", {"text": "x"})


def test_dispatch_records_zero_hit_has_no_records(monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/search/jql"):
            return _Resp({"issues": []})
        raise AssertionError("no issue fetch on zero hits")

    from app.connector_lookup import jira as jira_adapter

    monkeypatch.setattr(jf.requests, "get", fake_get)
    text, records = jira_adapter.dispatch_records(
        _session(), "jira_search", {"text": "nothing matches this"}
    )
    assert records is None
    assert text == "No matching Jira issues."


def test_dispatch_records_single_hit_text_matches_dispatch_exactly(monkeypatch):
    """AC5, mutation-proof: dispatch_records's text for a single-hit search
    must be byte-identical to dispatch's own output for the same call — both
    resolve to the SAME jira_fetch.render_issue(get_issue(...)) call."""
    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/comment"):
            return _Resp({"comments": []})
        if url.endswith("/search/jql"):
            return _Resp({"issues": [
                {"key": "PROJ-5", "fields": {"summary": "Checkout broken",
                                             "status": {"name": "In Progress"}}},
            ]})
        return _Resp(_issue_payload("Task"))

    from app.connector_lookup import jira as jira_adapter

    monkeypatch.setattr(jf.requests, "get", fake_get)
    # fake_get is a pure function of its closure (no consumed/stateful mock),
    # so calling both dispatch() and dispatch_records() against it is a fair
    # like-for-like comparison of two independent calls to the same fixture.
    expected = jl._make_dispatch(_session())("jira_search", {"text": "checkout"})
    text, records = jira_adapter.dispatch_records(
        _session(), "jira_search", {"text": "checkout"}
    )
    assert text == expected
    assert records is not None and len(records) == 1


def test_ac4_sweep_and_pull_records_are_byte_identical_for_a_single_hit(monkeypatch):
    """AC4 — THE LOAD-BEARING TEST for Jira.

    Exercises the REAL scheduled-pull puller (`kg_ingest.pullers.jira.pull`)
    and the REAL sweep-side `connector_lookup.jira.dispatch_records` against
    mocked HTTP responses describing the SAME issue, and asserts their
    `RawRecord.render()` outputs are byte-identical — the exact collision
    AC7's ledger dedupe depends on. Not a hand-reconstruction of either
    side's shape: both real code paths run, end to end, against one shared
    fixture.
    """
    from app.connector_lookup import jira as jira_adapter
    from app.kg_ingest.pullers import jira as jira_puller

    issue_fields = _issue_payload("Story")["fields"]

    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/oauth/token/accessible-resources"):
            return _Resp([{"id": "cid"}])
        if url.endswith("/comment"):
            return _Resp({"comments": []})
        if url.endswith("/search/jql"):
            # Both the puller's bulk listing (wide _FIELDS) and
            # jira_fetch.search's narrow search hit land here — return the
            # full field set either way; each caller reads only the subset
            # it actually requested. Real Jira would narrow this by
            # `fields=`, but what varies BY CALLER is the code reading the
            # response, which is exactly what this test needs to exercise.
            return _Resp({"issues": [{"key": "PROJ-5", "fields": issue_fields}],
                         "isLast": True})
        if url.endswith("/issue/PROJ-5"):
            return _Resp({"fields": issue_fields, "names": {}})
        raise AssertionError(f"unexpected URL in AC4 fixture: {url}")

    monkeypatch.setattr(jf.requests, "get", fake_get)

    pull_record = next(jira_puller.pull("tok"))

    monkeypatch.setattr(jf.requests, "get", fake_get)
    text, sweep_records = jira_adapter.dispatch_records(
        _session(), "jira_search", {"text": "checkout"}
    )
    assert sweep_records is not None and len(sweep_records) == 1
    sweep_record = sweep_records[0]

    assert sweep_record.render() == pull_record.render(), (
        "AC4: the sweep's single-hit record must render byte-identical to "
        "the scheduled pull's record for the same issue"
    )
    # AC3 — external_id is the SAME identifier the puller uses (the issue key).
    assert sweep_record.external_id == pull_record.external_id == "PROJ-5"
    assert sweep_record.provider == pull_record.provider == "jira"
    assert sweep_record.kind == pull_record.kind == "issue"
    assert "description:" in text  # single-hit dispatch still returns the full issue


def test_is_jira_lookup_bare_yes_accepts_the_assistants_offer():
    """The lookup ends with "Would you like me to fetch the full details?" and
    the natural reply is one word.

    Reported live: the answer to that offer was "yes", which carries no ticket,
    verb, pronoun or filter — so every signal this router looks for was absent
    and it fell through to the generic agent, which then said it had no details
    for the ticket the turn above had just listed.
    """
    thread = [
        {"role": "user", "content": "get me ticket about car"},
        {"role": "assistant", "content": "I found 1 ticket related to \"car\": "
                                         "KAN-1033 — Build a car driving feature. "
                                         "Would you like me to fetch the full "
                                         "details of this ticket?"},
    ]
    for q in ["yes", "Yes", "yes please", "yeah", "sure", "ok", "go ahead", "do it"]:
        assert is_jira_lookup(q, thread), q
        # Nothing about the word itself is a tracker read — the thread is the
        # only reason it means anything.
        assert not is_jira_lookup(q), q


def test_bare_yes_needs_an_actual_question_above_it():
    """Gated on the assistant having asked something. Otherwise a stray "ok" in
    a tracker thread would be read as a fetch request."""
    no_question = [
        {"role": "user", "content": "get me ticket about car"},
        {"role": "assistant", "content": "KAN-1033 — Build a car driving feature."},
    ]
    assert not is_jira_lookup("yes", no_question)
    assert not is_jira_lookup("ok", no_question)


def test_bare_yes_outside_a_tracker_thread_is_ignored():
    """A "yes" answering some other question must not reach the tracker."""
    other = [
        {"role": "user", "content": "should we raise prices?"},
        {"role": "assistant", "content": "Would you like me to model that?"},
    ]
    assert not is_jira_lookup("yes", other)


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


def test_is_jira_lookup_anaphoric_followup():
    # The reported miss: the lookup answered "get me ticket about cars", then the
    # follow-up referred to the issue by pronoun only. It carries no PM noun, no
    # key and no filter word, so judging it on its own words dead-ended it at the
    # scope gate ("I can only help with your product work"). The thread is what
    # carries it back to the live tracker.
    thread = [
        {"role": "user", "content": "get me ticket about cars"},
        {"role": "assistant", "content": "KAN-1033 — Build a car driving feature. "
                                         "Type: Task. Status: In Review."},
    ]
    for q in ["can you get me all the details about it?",
              "tell me more about it",
              "who is it assigned to?",
              "what's the description on that one"]:
        assert is_jira_lookup(q, thread), q
        assert not is_jira_lookup(q), q  # stateless miss — the thread carries it


def test_is_jira_lookup_thread_detected_from_users_own_question():
    # Thread detection reads the user's OWN earlier questions, not just the
    # assistant's answers — a tracker ask whose reply named no key at all still
    # keeps the thread open for the next turn.
    thread = [
        {"role": "user", "content": "get me tickets about cars"},
        {"role": "assistant", "content": "I couldn't find anything matching that."},
    ]
    assert is_jira_lookup("can you get me all the details about it?", thread)


def test_is_jira_lookup_followup_pivot_veto():
    thread = [
        {"role": "user", "content": "get me ticket about cars"},
        {"role": "assistant", "content": "KAN-1033 — Build a car driving feature."},
    ]
    # Pronoun + read verb, but the subject has moved off the tracker → normal
    # routing, not a live Jira read.
    for q in ["prioritize these features",
              "what's the status of our roadmap?",
              "get me the details of our churn",
              "write a prd for it"]:  # write phrasing also vetoed
        assert not is_jira_lookup(q, thread), q


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


# ── every pullable field ─────────────────────────────────────────────────────

def test_get_issue_requests_all_fields_with_names(monkeypatch):
    """The fetch asks Jira for EVERYTHING plus the field-name map.

    It used to send a fixed 14-field list, which by construction could not name
    a customer's custom fields — start date, story points and sprint were simply
    absent from the request, so no amount of prompting could surface them.
    """
    seen = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/comment"):
            return _Resp({"comments": []})
        seen["params"] = params
        return _Resp(_issue_payload("Task"))

    monkeypatch.setattr(jf.requests, "get", fake_get)
    jf.get_issue(_session(), "PROJ-5")
    assert seen["params"]["fields"] == "*all"
    # Without expand=names a custom field can only be shown as customfield_10031.
    assert seen["params"]["expand"] == "names"


def test_get_issue_surfaces_custom_and_date_fields_under_human_names(monkeypatch):
    """Fields outside the curated set ride along under their display names."""
    payload = _issue_payload("Task")
    payload["fields"].update({
        "customfield_10015": "2028-01-05",          # Start date
        "customfield_10031": 8,                      # Story Points
        "components": [{"name": "Checkout"}, {"name": "API"}],
        "resolution": None,                          # empty → must not render
        "timetracking": {"originalEstimate": "3d"},  # composite → JSON fallback
    })
    payload["names"] = {
        "customfield_10015": "Start date",
        "customfield_10031": "Story Points",
        "components": "Components",
        "timetracking": "Time tracking",
    }

    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/comment"):
            return _Resp({"comments": []})
        return _Resp(payload)

    monkeypatch.setattr(jf.requests, "get", fake_get)
    issue = jf.get_issue(_session(), "PROJ-5")
    by_name = {f["name"]: f["value"] for f in issue["other_fields"]}

    assert by_name["Start date"] == "2028-01-05"
    assert by_name["Story Points"] == "8"
    assert by_name["Components"] == "Checkout, API"          # list flattened
    assert "3d" in by_name["Time tracking"]                  # composite kept
    # Empty values are dropped: *all returns the whole catalogue, and most of it
    # is null for any one issue.
    assert "Resolution" not in by_name
    # Curated fields are NOT duplicated into the catch-all block.
    assert "Summary" not in by_name and "summary" not in by_name

    text = jf.render_issue(issue)
    assert "other fields:" in text
    assert "Start date: 2028-01-05" in text
    # `created` was fetched but never rendered before — a dead field in the request.
    assert "created:" in text


# ── editmeta: what is actually writable ──────────────────────────────────────

_EDITMETA_PAYLOAD = {
    "fields": {
        "duedate": {"name": "Due date", "required": False,
                    "schema": {"type": "date"}, "operations": ["set"]},
        "priority": {"name": "Priority", "required": False,
                     "schema": {"type": "priority"}, "operations": ["set"],
                     "allowedValues": [{"name": "High"}, {"name": "Low"}]},
        "labels": {"name": "Labels", "required": False,
                   "schema": {"type": "array", "items": "string"},
                   "operations": ["add", "set", "remove"]},
    }
}


def test_get_editmeta_parses_types_and_allowed_values(monkeypatch):
    monkeypatch.setattr(jf.requests, "get",
                        lambda *a, **k: _Resp(_EDITMETA_PAYLOAD))
    meta = jf.get_editmeta(_session(), "PROJ-5")
    by_id = {f["id"]: f for f in meta["fields"]}

    # The date type is what lets "august 31 2028" become a legal write without a
    # date parser on our side — the model shapes the value to the schema.
    assert by_id["duedate"]["type"] == "date"
    # A closed set of options is what stops a priority being invented that this
    # site's scheme has never had.
    assert by_id["priority"]["allowed_values"] == ["High", "Low"]
    assert by_id["labels"]["items"] == "string"


def test_get_editmeta_missing_returns_none(monkeypatch):
    monkeypatch.setattr(jf.requests, "get",
                        lambda *a, **k: _Resp({}, status=404))
    assert jf.get_editmeta(_session(), "NOPE-1") is None


def test_render_editmeta_states_that_status_is_not_a_field():
    text = jf.render_editmeta({"key": "PROJ-5", "fields": [
        {"id": "duedate", "name": "Due date", "type": "date", "items": "",
         "required": False, "operations": ["set"], "allowed_values": [],
         "allowed_truncated": False},
    ]})
    assert "Due date (id: duedate)" in text and "type: date" in text
    # Jira does not edit status as a field; it moves through a transition. Saying
    # so in the tool result is what stops the agent attempting a field write that
    # silently does nothing.
    assert "not a field edit" in text


# ── proposing a change (still no write) ──────────────────────────────────────

def _propose_env(monkeypatch, *, editmeta=None, transitions=None):
    """Patch the reads a preview does: the issue, its editmeta, its transitions."""
    def fake_get(url, params=None, headers=None, timeout=None):
        if url.endswith("/comment"):
            return _Resp({"comments": []})
        if url.endswith("/editmeta"):
            return _Resp(editmeta if editmeta is not None else _EDITMETA_PAYLOAD)
        return _Resp(_issue_payload("Task"))

    monkeypatch.setattr(jf.requests, "get", fake_get)
    monkeypatch.setattr(jf, "list_transitions",
                        lambda s, k: transitions if transitions is not None else
                        [{"id": "31", "name": "Review", "to_status_name": "In Review"}])


def test_preview_change_describes_before_and_after(monkeypatch):
    _propose_env(monkeypatch)
    out = jf.preview_change(_session(), "PROJ-5", fields={"duedate": "2028-08-31"})
    assert out["ok"]
    assert "Due date" in out["text"] and "2028-08-31" in out["text"]
    # The user must be able to tell nothing has happened yet.
    assert "NOT applied yet" in out["text"]
    # The change handed back is exactly what the confirm endpoint will execute.
    assert out["change"]["issue_key"] == "PROJ-5"
    assert out["change"]["fields"] == {"duedate": "2028-08-31"}


def test_preview_change_rejects_a_field_the_user_cannot_edit(monkeypatch):
    _propose_env(monkeypatch)
    out = jf.preview_change(_session(), "PROJ-5", fields={"customfield_99": "x"})
    # Caught at preview, where the user can see it — not as a raw Jira 400 after
    # they have already confirmed.
    assert not out["ok"]
    assert "not editable" in out["text"]


def test_preview_change_rejects_an_illegal_option(monkeypatch):
    _propose_env(monkeypatch)
    out = jf.preview_change(_session(), "PROJ-5", fields={"priority": "Critical"})
    assert not out["ok"]
    assert "not a legal value" in out["text"] and "High" in out["text"]


def test_preview_change_rejects_an_unreachable_status(monkeypatch):
    _propose_env(monkeypatch)
    out = jf.preview_change(_session(), "PROJ-5", to_status="Released")
    # Status moves along a workflow transition; one that is not legal from here
    # must fail loudly with the options, or the user is told it worked.
    assert not out["ok"]
    assert "not reachable" in out["text"] and "In Review" in out["text"]


def test_propose_tool_never_writes(monkeypatch):
    """The tool validates and describes. Any HTTP verb other than GET would be a
    write, and none is reachable from the agent's loop."""
    _propose_env(monkeypatch)
    called = {"put": 0, "post": 0}
    monkeypatch.setattr(jf.requests, "put", lambda *a, **k: called.__setitem__("put", 1))
    monkeypatch.setattr(jf.requests, "post", lambda *a, **k: called.__setitem__("post", 1))

    proposal: dict = {}
    dispatch = jl._make_dispatch(_session(), proposal)
    out = dispatch("jira_propose_change", {"issue_key": "PROJ-5",
                                           "fields": {"duedate": "2028-08-31"}})
    assert "NOT applied yet" in out
    assert called == {"put": 0, "post": 0}
    # The proposal is handed out for the UI to confirm.
    assert proposal["fields"] == {"duedate": "2028-08-31"}


def test_propose_tool_requires_something_to_change(monkeypatch):
    _propose_env(monkeypatch)
    dispatch = jl._make_dispatch(_session(), {})
    assert "at least one of" in dispatch("jira_propose_change", {"issue_key": "PROJ-5"})


def test_encode_for_editmeta_shapes_values_by_schema():
    """Jira rejects a mis-encoded field with a 400 naming only the field id, so
    the shape is driven by the schema rather than the field's name."""
    assert jf.encode_for_editmeta({"type": "date"}, "2028-08-31") == "2028-08-31"
    assert jf.encode_for_editmeta({"type": "number"}, "8") == 8.0
    assert jf.encode_for_editmeta({"type": "priority"}, "High") == {"name": "High"}
    assert jf.encode_for_editmeta({"type": "user"}, "acc-1") == {"accountId": "acc-1"}
    # Labels are a bare list of strings; components are objects matched by name.
    assert jf.encode_for_editmeta({"type": "array", "items": "string"}, "urgent") == ["urgent"]
    assert jf.encode_for_editmeta(
        {"type": "array", "items": "component"}, ["API"]) == [{"name": "API"}]


def test_editmeta_tool_is_offered_and_dispatched(monkeypatch):
    assert any(t["name"] == "jira_editmeta" for t in
               [jl._SEARCH_TOOL, jl._GET_ISSUE_TOOL, jl._EDITMETA_TOOL])
    monkeypatch.setattr(jf.requests, "get",
                        lambda *a, **k: _Resp(_EDITMETA_PAYLOAD))
    dispatch = jl._make_dispatch(_session())
    out = dispatch("jira_editmeta", {"issue_key": "PROJ-5"})
    assert "Editable fields on PROJ-5" in out
    assert dispatch("jira_editmeta", {}) == "(jira_editmeta: 'issue_key' is required)"


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
    # A single search hit is expanded to the full issue (see the single-hit test
    # above), so this asserts the KEY made it through rather than the lean line.
    assert "P-1" in dispatch("jira_search", {"text": "pay"})
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
    # Every tool was offered; the question rode in the user turn. None of them
    # can mutate Jira: the three reads read, and jira_propose_change only
    # validates and describes a change for the user to confirm.
    names = {t["name"] for t in captured["tools"]}
    assert names == {"jira_search", "jira_get_issue", "jira_editmeta",
                     "jira_propose_change"}
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
