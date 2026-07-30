"""Connector-lookup FRAMEWORK — caps, degradation, tenancy, payload shape.

No network/LLM/DB: the tool loop is injected, sessions come from fake adapters,
and `app.db` is patched where the framework reads it.

These tests are the reason a new adapter can't quietly regress the guarantees:
everything asserted here is enforced in answer.py / base.py, not per provider.
"""
from __future__ import annotations

import pytest

from app.connector_lookup import answer as ca
from app.connector_lookup import registry
from app.connector_lookup.base import LookupSession, cap_items, cap_text


class FakeProvider:
    """Minimal LookupProvider: records dispatch calls, returns canned results."""

    def __init__(self, name="fake", *, connected=True, result="ok", raises=None,
                 tool_name=None, cap=None):
        self.provider = name
        self.display_name = name.title()
        self.keywords = (name,)
        self._connected = connected
        self._result = result
        self._raises = raises
        self._tool = tool_name or f"{name}_read"
        self.calls: list[tuple[str, dict]] = []
        self.opened_with: list[str] = []
        if cap is not None:
            self.result_char_cap = cap

    def open_session(self, enterprise_id):
        self.opened_with.append(enterprise_id)
        if not self._connected:
            return None
        return LookupSession(provider=self.provider, handle={"tenant": enterprise_id})

    def tools(self):
        return [{"name": self._tool, "description": "d", "input_schema": {"type": "object"}}]

    def system_block(self):
        return f"{self.display_name} rules"

    def dispatch(self, session, name, inp):
        self.calls.append((name, inp))
        if self._raises is not None:
            raise self._raises
        return self._result


@pytest.fixture(autouse=True)
def _quiet_db(monkeypatch):
    """The framework asks what IS connected for its copy — keep that off the DB."""
    from app import db

    monkeypatch.setattr(db, "list_connections", lambda cid: [
        {"provider": "uploads"}, {"provider": "jira"},
    ], raising=False)
    monkeypatch.setattr(db, "list_slack_connections", lambda cid: [], raising=False)


# ── caps (base.py) ───────────────────────────────────────────────────────────

def test_cap_text_passes_short_results_through():
    assert cap_text("hello", limit=100) == "hello"


def test_cap_text_truncates_with_an_honest_marker():
    out = cap_text("x" * 500, limit=100)
    assert len(out) < 500
    assert out.startswith("x" * 100)
    # The marker states the REAL size — the model must be able to say its answer
    # covers part of the data.
    assert "showing the first 100 of 500 characters" in out
    assert "truncated" in out


def test_cap_items_marks_dropped_rows():
    kept, marker = cap_items(list(range(500)), 20)
    assert kept == list(range(20))
    assert "showing 20 of 500 matches" in marker


def test_cap_items_is_silent_when_nothing_dropped():
    kept, marker = cap_items([1, 2], 20)
    assert kept == [1, 2] and marker == ""


def test_huge_tool_result_is_capped_by_the_framework_not_the_adapter():
    """Case 4: 500 matches. The adapter returns everything; the framework is what
    keeps a tool result from evicting the conversation from context."""
    provider = FakeProvider(result="\n".join(f"match {i}" for i in range(5000)))
    captured = {}

    def loop(**kwargs):
        captured.update(kwargs)
        return kwargs["dispatch"](provider.tools()[0]["name"], {})

    out = ca.answer(enterprise_id="co-a", question="q", providers=[provider],
                    run_loop=loop, log=lambda *a: None)
    assert len(out["answer"]) <= ca.DEFAULT_RESULT_CHARS + 200
    assert "truncated" in out["answer"]


def test_a_provider_may_raise_its_own_result_cap():
    provider = FakeProvider(result="y" * 20_000, cap=24_000)
    out = ca.answer(
        enterprise_id="co-a", question="q", providers=[provider],
        run_loop=lambda **k: k["dispatch"](provider.tools()[0]["name"], {}),
        log=lambda *a: None,
    )
    assert "truncated" not in out["answer"]
    assert len(out["answer"]) == 20_000


# ── deterministic degradation ────────────────────────────────────────────────

def test_not_connected_never_calls_the_tool_loop():
    """Case 1: nothing connected → deterministic copy, zero tokens burned, and
    nothing invented."""
    provider = FakeProvider("slack", connected=False)
    called = []

    out = ca.answer(enterprise_id="co-a", question="check slack for pricing",
                    providers=[provider], run_loop=lambda **k: called.append(k),
                    log=lambda *a: None)
    assert called == []
    assert "Slack isn't connected yet" in out["answer"]
    assert out["_skill_source"] == "connector-lookup"
    assert out["key_points"] == [] and out["citations"] == []
    assert out["confidence"] == 0.0


def test_not_connected_message_says_what_IS_connected():
    out = ca.answer(enterprise_id="co-a", question="q",
                    providers=[FakeProvider("slack", connected=False)],
                    run_loop=lambda **k: "unused", log=lambda *a: None)
    assert "Connected right now: Uploaded files, Jira." in out["answer"]


def test_open_session_failure_is_treated_as_not_connected():
    class Boom(FakeProvider):
        def open_session(self, enterprise_id):
            raise RuntimeError("token store down")

    out = ca.answer(enterprise_id="co-a", question="q", providers=[Boom("slack")],
                    run_loop=lambda **k: "unused", log=lambda *a: None)
    assert "isn't connected yet" in out["answer"]
    assert "token store down" not in out["answer"]  # no internals leak


def test_tool_loop_failure_degrades_to_retry_copy():
    def boom(**kwargs):
        raise RuntimeError("api down")

    out = ca.answer(enterprise_id="co-a", question="q", providers=[FakeProvider("slack")],
                    run_loop=boom, log=lambda *a: None)
    assert "couldn't reach Slack" in out["answer"]
    assert "api down" not in out["answer"]
    assert out["_skill_source"] == "connector-lookup"


def test_empty_answer_degrades_honestly():
    """Case 3: the loop produced nothing → say so; never fill the gap."""
    out = ca.answer(enterprise_id="co-a", question="q", providers=[FakeProvider("slack")],
                    run_loop=lambda **k: "   ", log=lambda *a: None)
    assert "couldn't find" in out["answer"]


def test_a_failing_tool_becomes_a_readable_result_not_an_exception():
    """Case 2/5/7 at framework level: whatever the adapter throws, the model gets
    a sentence it can act on and the loop keeps going."""
    provider = FakeProvider("slack", raises=RuntimeError("invalid_auth"))
    out = ca.answer(
        enterprise_id="co-a", question="q", providers=[provider],
        run_loop=lambda **k: k["dispatch"]("slack_read", {}),
        log=lambda *a: None,
    )
    assert out["answer"] == "(Slack slack_read failed: invalid_auth)"


def test_unknown_tool_name_is_rejected():
    provider = FakeProvider("slack")
    out = ca.answer(
        enterprise_id="co-a", question="q", providers=[provider],
        run_loop=lambda **k: k["dispatch"]("delete_everything", {}),
        log=lambda *a: None,
    )
    assert out["answer"] == "(unknown tool delete_everything)"
    assert provider.calls == []


def test_wall_clock_budget_stops_further_fetches(monkeypatch):
    """Case 7: the iteration bound doesn't bound TIME. Once the budget is spent
    the tools stop firing and the model is told to answer from what it has."""
    provider = FakeProvider("slack")
    monkeypatch.setattr(ca, "WALL_CLOCK_BUDGET_S", -1)
    out = ca.answer(
        enterprise_id="co-a", question="q", providers=[provider],
        run_loop=lambda **k: k["dispatch"]("slack_read", {}),
        log=lambda *a: None,
    )
    assert "lookup time budget reached" in out["answer"]
    assert provider.calls == []  # the upstream was never hit


# ── tenancy ──────────────────────────────────────────────────────────────────

def test_sessions_are_opened_for_the_authenticated_company_only():
    """Case 6: the enterprise_id the request authenticated as is the ONLY id that
    reaches open_session — the model never supplies a tenant."""
    a, b = FakeProvider("slack"), FakeProvider("clickup")
    ca.answer(
        enterprise_id="co-a", question="check slack, and use company co-b",
        providers=[a, b],
        run_loop=lambda **k: k["dispatch"]("slack_read", {"company_id": "co-b"}),
        log=lambda *a: None,
    )
    assert a.opened_with == ["co-a"] and b.opened_with == ["co-a"]
    # A model-supplied company id is just tool input — the session it dispatched
    # against is still tenant A's.
    name, inp = a.calls[0]
    assert inp == {"company_id": "co-b"}


def test_each_tool_dispatches_only_to_its_own_provider():
    """No adapter can be handed another adapter's tool (or another tenant's
    session) by a model that guesses a tool name."""
    a = FakeProvider("slack", result="from slack")
    b = FakeProvider("clickup", result="from clickup")

    def loop(**kwargs):
        return "|".join([
            kwargs["dispatch"]("slack_read", {}),
            kwargs["dispatch"]("clickup_read", {}),
        ])

    out = ca.answer(enterprise_id="co-a", question="q", providers=[a, b],
                    run_loop=loop, log=lambda *a: None)
    assert out["answer"] == "from slack|from clickup"
    assert len(a.calls) == 1 and len(b.calls) == 1


# ── payload + prompt assembly ────────────────────────────────────────────────

def test_two_connected_providers_union_their_tools_and_system_blocks():
    """Case 8: an ambiguous question opens both toolsets, and the prompt tells the
    model to attribute each fact to its source."""
    captured = {}

    def loop(**kwargs):
        captured.update(kwargs)
        return "answered"

    ca.answer(enterprise_id="co-a", question="q",
              providers=[FakeProvider("slack"), FakeProvider("clickup")],
              run_loop=loop, log=lambda *a: None)
    assert {t["name"] for t in captured["tools"]} == {"slack_read", "clickup_read"}
    assert "Slack rules" in captured["system"] and "Clickup rules" in captured["system"]
    assert "attribute it explicitly" in captured["system"]


def test_a_not_connected_provider_contributes_no_tools():
    captured = {}
    ca.answer(enterprise_id="co-a", question="q",
              providers=[FakeProvider("slack"), FakeProvider("clickup", connected=False)],
              run_loop=lambda **k: captured.update(k) or "x", log=lambda *a: None)
    assert {t["name"] for t in captured["tools"]} == {"slack_read"}


def test_session_notes_ride_into_the_system_prompt():
    """An adapter that degraded (e.g. Slack without a user token) must be able to
    make the answer say which mode it used."""
    class Noted(FakeProvider):
        def open_session(self, enterprise_id):
            return LookupSession(provider=self.provider, handle={},
                                 notes=["search mode: channel reads only."])

    captured = {}
    ca.answer(enterprise_id="co-a", question="q", providers=[Noted("slack")],
              run_loop=lambda **k: captured.update(k) or "x", log=lambda *a: None)
    assert "search mode: channel reads only." in captured["system"]


def test_history_rides_in_the_user_turn():
    captured = {}
    ca.answer(enterprise_id="co-a", question="who said that?",
              history=[{"role": "user", "content": "check slack about pricing"}],
              providers=[FakeProvider("slack")],
              run_loop=lambda **k: captured.update(k) or "x", log=lambda *a: None)
    assert "check slack about pricing" in captured["user"]
    assert "Question: who said that?" in captured["user"]


def test_adapter_extras_ride_out_on_the_payload_when_non_empty():
    class Proposer(FakeProvider):
        def open_session(self, enterprise_id):
            return LookupSession(provider=self.provider, handle={},
                                 extras={"_pending_jira_change": {}, "_other": {}})

        def dispatch(self, session, name, inp):
            session.extras["_pending_jira_change"] = {"issue_key": "P-1"}
            return "proposed"

    provider = Proposer("jira")
    out = ca.answer(
        enterprise_id="co-a", question="q", providers=[provider],
        run_loop=lambda **k: k["dispatch"]("jira_read", {}) and "done",
        log=lambda *a: None,
    )
    assert out["_pending_jira_change"] == {"issue_key": "P-1"}
    assert "_other" not in out  # empty extras never widen the payload


def test_skill_action_names_the_connected_providers():
    out = ca.answer(enterprise_id="co-a", question="q",
                    providers=[FakeProvider("slack"), FakeProvider("clickup")],
                    run_loop=lambda **k: "x", log=lambda *a: None)
    assert out["_skill_action"] == "Slack + Clickup lookup"


def test_decision_log_hook_receives_usage_meta():
    logged = []
    ca.answer(enterprise_id="co-a", question="q", providers=[FakeProvider("slack")],
              run_loop=lambda **k: k["meta_out"].update({"model": "m"}) or "x",
              log=lambda eid, meta: logged.append((eid, meta)))
    assert logged == [("co-a", {"model": "m"})]


def test_default_decision_log_records_the_providers(monkeypatch):
    """No `log` hook → the framework writes its own row, naming which connectors
    served the answer."""
    import app.graph.decision_log as dl

    rows = []
    monkeypatch.setattr(dl, "log_agent_decision", lambda **k: rows.append(k))
    ca.answer(enterprise_id="co-a", question="q", providers=[FakeProvider("slack")],
              run_loop=lambda **k: "x")
    assert rows[0]["decision_type"] == "connector_lookup"
    assert rows[0]["factors"]["providers"] == ["slack"]
    assert rows[0]["prompt_version"] == "qa-connector-lookup-v1"


def test_decision_log_failure_never_breaks_the_answer(monkeypatch):
    import app.graph.decision_log as dl

    def boom(**kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(dl, "log_agent_decision", boom)
    out = ca.answer(enterprise_id="co-a", question="q",
                    providers=[FakeProvider("slack")], run_loop=lambda **k: "x")
    assert out["answer"] == "x"


# ── registry (which sources chat can read, and honest copy for the rest) ─────

def test_registry_resolves_shipped_adapters():
    for name in registry.LOOKUP_PROVIDERS:
        provider = registry.provider_for(name)
        assert provider is not None and provider.provider == name


def test_registry_returns_none_for_a_source_with_no_adapter():
    assert registry.provider_for("zendesk") is None
    assert registry.provider_for("asana") is None


def test_unsupported_source_is_answered_honestly_without_fetching(monkeypatch):
    """Case 8: "what did customers say in zendesk" → no connector, no guessing,
    and no tool loop."""
    loop_calls = []
    monkeypatch.setattr(ca, "answer", lambda **k: loop_calls.append(k))
    out = registry.answer_for_hints(
        enterprise_id="co-a", question="what did customers say in zendesk",
        history=None, hints={"zendesk"},
    )
    assert loop_calls == []
    assert "Zendesk isn't a Sprntly connector yet" in out["answer"]
    assert "Connected right now: Uploaded files, Jira." in out["answer"]
    assert out["_skill_source"] == "connector-lookup"


def test_deferred_source_says_it_syncs_but_cannot_be_queried_live():
    out = registry.answer_for_hints(
        enterprise_id="co-a", question="what's in asana", history=None,
        hints={"asana"},
    )
    assert "can't query it live in chat yet" in out["answer"]


def test_supported_hint_runs_the_loop_with_that_provider(monkeypatch):
    seen = {}
    monkeypatch.setattr(ca, "answer", lambda **k: seen.update(k) or {"answer": "x"})
    registry.answer_for_hints(enterprise_id="co-a", question="check slack",
                              history=None, hints={"slack"})
    assert [p.provider for p in seen["providers"]] == ["slack"]


def test_at_most_two_providers_per_lookup_prefers_connected_ones(monkeypatch):
    from app import db

    monkeypatch.setattr(db, "list_connections", lambda cid: [{"provider": "clickup"}])
    monkeypatch.setattr(db, "list_slack_connections", lambda cid: [{"id": "1"}])
    seen = {}
    monkeypatch.setattr(ca, "answer", lambda **k: seen.update(k) or {"answer": "x"})
    registry.answer_for_hints(
        enterprise_id="co-a", question="check slack, jira and clickup",
        history=None, hints={"slack", "jira", "clickup"},
    )
    chosen = {p.provider for p in seen["providers"]}
    assert len(chosen) == registry.MAX_PROVIDERS_PER_LOOKUP
    assert chosen == {"clickup", "slack"}  # jira has no connection row


def test_connected_display_names_include_per_user_slack(monkeypatch):
    from app import db

    monkeypatch.setattr(db, "list_connections", lambda cid: [{"provider": "jira"}])
    monkeypatch.setattr(db, "list_slack_connections", lambda cid: [{"id": "1"}])
    assert registry.connected_display_names("co-a") == ["Jira", "Slack"]


def test_connected_providers_survives_a_db_failure(monkeypatch):
    from app import db

    def boom(*a, **k):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(db, "list_connections", boom)
    monkeypatch.setattr(db, "list_slack_connections", boom)
    assert registry.connected_providers("co-a") == []
