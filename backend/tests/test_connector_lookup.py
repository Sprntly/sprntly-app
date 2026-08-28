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


# ── the Jira shim's copy is a CONTRACT, pinned exactly ───────────────────────
#
# tests/test_jira_lookup.py asserts these branches with substrings ("couldn't
# find", "couldn't reach Jira"), which stayed green while the actual sentences
# drifted to the framework's generic wording — a Jira user was being told to name
# "the channel, ticket, file or person" and to reconnect "that connection". These
# tests pin the full strings so the next refactor can't drift them silently.

_JIRA_NOT_CONNECTED = (
    "I can pull live details from your Jira — tickets, epics, comments, "
    "and their status — but Jira isn't connected yet (or its access "
    "needs refreshing). Connect **Jira** in Settings → Connectors and "
    "I'll be able to read your issues."
)
_JIRA_EMPTY = (
    "I looked in Jira but couldn't find the issue(s) your question "
    "refers to. Double-check the issue key or try naming the project."
)
_JIRA_UNREACHABLE = (
    "I couldn't reach Jira to look that up just now. Please retry in a "
    "moment — if it keeps failing, your Jira connection may need "
    "reconnecting in Settings → Connectors."
)


def _jira_session():
    from app.connectors.jira_fetch import JiraSession

    return JiraSession(access_token="tok", cloud_id="cid",
                       site_url="https://acme.atlassian.net")


def test_jira_not_connected_reads_the_knowledge_graph(monkeypatch):
    """Jira not connected no longer dead-ends on the verbatim connect copy — its
    tasks are synced into the graph, so the KG-only loop runs instead. The connect
    copy is still passed as `not_connected_text` for the flag-off contract, but
    jira_lookup now passes include_knowledge_graph=True."""
    from app import jira_lookup
    from app.connector_lookup import knowledge_graph as kg
    from app.connectors import jira_fetch

    monkeypatch.setattr(jira_fetch, "open_session", lambda cid: None)
    monkeypatch.setattr(kg, "search", lambda eid, q: "KG: PROJ-1 is In Review.")
    monkeypatch.setattr(
        jira_lookup, "run_tool_loop",
        lambda **k: k["dispatch"](kg.TOOL_NAME, {"query": "PROJ-1"}),
    )
    monkeypatch.setattr(jira_lookup, "_log", lambda *a, **k: None)
    out = jira_lookup.answer(enterprise_id="co", question="status of PROJ-1")
    assert "In Review" in out["answer"]
    assert out["answer"] != _JIRA_NOT_CONNECTED
    assert out["_skill_source"] == "jira-lookup"
    assert out["_skill_action"] == "Jira lookup"


def test_jira_empty_result_copy_is_verbatim(monkeypatch):
    from app import jira_lookup
    from app.connectors import jira_fetch

    monkeypatch.setattr(jira_fetch, "open_session", lambda cid: _jira_session())
    monkeypatch.setattr(jira_lookup, "run_tool_loop", lambda **k: "  ")
    monkeypatch.setattr(jira_lookup, "_log", lambda *a, **k: None)
    out = jira_lookup.answer(enterprise_id="co", question="status of ZZZ-9")
    assert out["answer"] == _JIRA_EMPTY
    assert "channel" not in out["answer"] and "file" not in out["answer"]


def test_jira_unreachable_copy_is_verbatim(monkeypatch):
    from app import jira_lookup
    from app.connectors import jira_fetch

    monkeypatch.setattr(jira_fetch, "open_session", lambda cid: _jira_session())

    def boom(**kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr(jira_lookup, "run_tool_loop", boom)
    out = jira_lookup.answer(enterprise_id="co", question="status of PROJ-1")
    assert out["answer"] == _JIRA_UNREACHABLE
    assert "your Jira connection" in out["answer"]


def test_jira_uses_its_own_system_prompt_not_the_framework_head(monkeypatch):
    from app import jira_lookup
    from app.connectors import jira_fetch

    monkeypatch.setattr(jira_fetch, "open_session", lambda cid: _jira_session())
    captured = {}
    monkeypatch.setattr(jira_lookup, "run_tool_loop",
                        lambda **k: captured.update(k) or "answered")
    monkeypatch.setattr(jira_lookup, "_log", lambda *a, **k: None)
    jira_lookup.answer(enterprise_id="co", question="status of PROJ-1")
    # Jira's verbatim prompt still LEADS the system block, unchanged, and the
    # framework head is still NOT injected...
    assert captured["system"].startswith(jira_lookup._SYSTEM)
    assert "Rules that hold for every source" not in captured["system"]
    # ...but the knowledge-graph block is now appended, so the tuned prompt is
    # told the KG tool it is offered exists (Jira's tasks are synced to the graph).
    assert "## Sprntly knowledge graph" in captured["system"]
    assert "accumulated knowledge" in captured["system"]


def test_generic_adapters_still_get_the_framework_copy():
    """The overrides are per-adapter, not a global change of default."""
    out = ca.answer(enterprise_id="co-a", question="q",
                    providers=[FakeProvider("slack")],
                    run_loop=lambda **k: "   ", log=lambda *a: None)
    assert "Try naming the channel, ticket, file or person more exactly" in out["answer"]


# ── partial-connection honesty ───────────────────────────────────────────────

def test_unreachable_sources_are_named_in_the_system_block():
    """"check slack and hubspot" with only Slack connected: an answer from Slack
    alone must not read as an answer about both."""
    captured = {}
    ca.answer(enterprise_id="co-a", question="check slack and clickup",
              providers=[FakeProvider("slack"), FakeProvider("clickup", connected=False)],
              run_loop=lambda **k: captured.update(k) or "x", log=lambda *a: None)
    assert "## Not available for this question" in captured["system"]
    assert "Clickup" in captured["system"].split("## Not available")[1]
    assert "do not let an answer from the other source(s) imply" in captured["system"]


def test_caller_supplied_unavailable_names_reach_the_system_block():
    captured = {}
    ca.answer(enterprise_id="co-a", question="check slack and zendesk",
              providers=[FakeProvider("slack")], unavailable_names=["Zendesk"],
              run_loop=lambda **k: captured.update(k) or "x", log=lambda *a: None)
    assert "Zendesk" in captured["system"]


def test_no_unavailable_section_when_everything_resolved():
    captured = {}
    ca.answer(enterprise_id="co-a", question="q", providers=[FakeProvider("slack")],
              run_loop=lambda **k: captured.update(k) or "x", log=lambda *a: None)
    assert "Not available for this question" not in captured["system"]


# ── registry (which sources chat can read, and honest copy for the rest) ─────

def test_registry_resolves_shipped_adapters():
    for name in registry.LOOKUP_PROVIDERS:
        provider = registry.provider_for(name)
        assert provider is not None and provider.provider == name


def test_registry_returns_none_for_a_source_with_no_adapter():
    assert registry.provider_for("zendesk") is None
    # Still genuinely adapter-less: syncs into the KG, no live read.
    assert registry.provider_for("sprinklr") is None
    assert registry.provider_for("figma") is None


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
        enterprise_id="co-a", question="what's in sprinklr", history=None,
        hints={"sprinklr"},
    )
    assert "can't query it live in chat yet" in out["answer"]


@pytest.mark.parametrize("provider", ["asana", "google_meet"])
def test_a_newly_adapted_source_runs_the_loop_instead_of_apologising(
    provider, monkeypatch
):
    """REGRESSION, and the reason both of these had a test asserting the
    opposite until now.

    Asana and Google Meet were DEFERRED: a question naming either got the
    honest "it syncs into your knowledge graph, but I can't query it live"
    copy. Now that each has an adapter, that sentence would be a false
    apology about a source we CAN read — so the hint must reach the tool
    loop, and neither name may survive in DEFERRED.
    """
    seen = {}
    monkeypatch.setattr(ca, "answer", lambda **k: seen.update(k) or {"answer": "x"})

    registry.answer_for_hints(
        enterprise_id="co-a", question=f"what's in {provider}", history=None,
        hints={provider},
    )

    assert [p.provider for p in seen["providers"]] == [provider]
    assert provider not in registry.DEFERRED
    assert provider in registry.LOOKUP_PROVIDERS


def test_sweep_cap_covers_every_adapter_that_exists():
    """MAX_SWEEP_PROVIDERS is a SLICE END over the named-but-unslotted sources
    (`supported[MAX_TOOL_PROVIDERS:MAX_SWEEP_PROVIDERS]`). Left at a literal
    while adapters grew past it, the last-ranked named sources would fall back
    into the apology list — a coverage regression invisible in a diff.

    Also pins the split the lead named: coverage comes from the sweep, DEPTH
    from the loop, so the tool cap must NOT have moved.
    """
    assert registry.MAX_SWEEP_PROVIDERS >= len(registry.LOOKUP_PROVIDERS)
    assert registry.MAX_TOOL_PROVIDERS == 3
    assert registry.MAX_PROVIDERS_PER_LOOKUP == registry.MAX_TOOL_PROVIDERS


@pytest.mark.parametrize("provider", ["asana", "google_meet"])
def test_new_adapter_satisfies_the_lookup_provider_contract(provider):
    """base.LookupProvider is a runtime-checkable Protocol, and the framework
    (answer.py) calls all four of its methods on anything the registry hands
    back. A partial adapter would blow up at answer time, on a live tenant."""
    from app.connector_lookup.base import LookupProvider, RecordsCapable

    adapter = registry.provider_for(provider)
    assert isinstance(adapter, LookupProvider)
    # The sweep needs RawRecord output, not just prose — an adapter without it
    # silently discards every hit it just paid to fetch.
    assert isinstance(adapter, RecordsCapable)
    assert adapter.provider == provider
    assert adapter.display_name and adapter.keywords
    assert adapter.system_block().strip()
    names = [t["name"] for t in adapter.tools()]
    assert len(names) == len(set(names)) and names
    for tool in adapter.tools():
        assert tool["description"].strip()
        assert tool["input_schema"]["type"] == "object"


@pytest.mark.parametrize("provider", ["asana", "google_meet"])
def test_new_adapter_dispatch_records_is_none_for_an_unknown_tool(provider):
    """`dispatch_records` returning None is the contract for "nothing to add" —
    `_AdapterLeg.run` reads it as "fall back to dispatch". An adapter that
    raised here instead would turn a harmless call into an unread source."""
    adapter = registry.provider_for(provider)
    session = LookupSession(provider=provider, handle=object())
    assert adapter.dispatch_records(session, "not_a_real_tool", {}) is None


def test_supported_hint_runs_the_loop_with_that_provider(monkeypatch):
    seen = {}
    monkeypatch.setattr(ca, "answer", lambda **k: seen.update(k) or {"answer": "x"})
    registry.answer_for_hints(enterprise_id="co-a", question="check slack",
                              history=None, hints={"slack"})
    assert [p.provider for p in seen["providers"]] == ["slack"]


def test_three_named_providers_all_get_tools(monkeypatch):
    """The cap was 2, which truncated a three-source question into an apology.
    Three named sources now all reach the loop."""
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
    assert chosen == {"clickup", "slack", "jira"}
    assert len(chosen) == registry.MAX_TOOL_PROVIDERS
    # Everything fit, so nothing was primed and nothing is written off.
    assert seen["primed_context"] == ""
    assert seen["budget_penalty_s"] == 0.0
    assert seen["unavailable_names"] == []


def test_tool_cap_still_prefers_connected_providers_when_it_binds(monkeypatch):
    """With more named sources than tool slots, the ones we can actually read
    keep priority — a cap must never spend a slot on a disconnected source."""
    from app import db

    monkeypatch.setattr(db, "list_connections", lambda cid: [
        {"provider": "clickup"}, {"provider": "confluence"}, {"provider": "hubspot"},
    ])
    monkeypatch.setattr(db, "list_slack_connections", lambda cid: [])
    monkeypatch.setattr(registry, "MAX_TOOL_PROVIDERS", 2)
    seen = {}
    monkeypatch.setattr(ca, "answer", lambda **k: seen.update(k) or {"answer": "x"})
    monkeypatch.setattr(
        "app.connector_lookup.sweep.sweep",
        lambda eid, q, **k: _StubSweep([]),
    )
    registry.answer_for_hints(
        enterprise_id="co-a", question="check jira, clickup and confluence",
        history=None, hints={"jira", "clickup", "confluence"},
    )
    chosen = {p.provider for p in seen["providers"]}
    assert chosen == {"clickup", "confluence"}  # jira has no connection row


class _StubSweep:
    """Stands in for a SweepResult without touching a connector.

    Takes PROVIDER KEYS, matching `covered_providers()` — the real contract.
    Display names would not do: the sweep qualifies some of them ("HubSpot
    (deals)") and a local leg reports under what it reads ("calls") rather than
    the provider feeding it.
    """

    def __init__(self, covered, block="LIVE CROSS-SOURCE SWEEP — ...\n### HubSpot\nrows"):
        self._covered = set(covered)
        self._block = block if covered else ""

    def covered_providers(self):
        return set(self._covered)

    def render(self):
        return self._block


def test_overflow_sources_are_swept_and_primed_into_the_loop(monkeypatch):
    """BREADTH: a source past the tool cap is no longer apologised for — it is
    searched in parallel and its results are handed to the model."""
    from app import db

    monkeypatch.setattr(db, "list_connections", lambda cid: [
        {"provider": "jira"}, {"provider": "clickup"},
        {"provider": "confluence"}, {"provider": "hubspot"},
    ])
    monkeypatch.setattr(db, "list_slack_connections", lambda cid: [])
    swept = {}

    def _fake_sweep(eid, q, **kwargs):
        swept.update(enterprise_id=eid, question=q, **kwargs)
        return _StubSweep({"hubspot"})

    monkeypatch.setattr("app.connector_lookup.sweep.sweep", _fake_sweep)
    seen = {}
    monkeypatch.setattr(ca, "answer", lambda **k: seen.update(k) or {"answer": "x"})

    registry.answer_for_hints(
        enterprise_id="co-a",
        question="what do jira, clickup, confluence and hubspot say about billing",
        history=None, hints={"jira", "clickup", "confluence", "hubspot"},
    )

    # The three that fit get tools; the fourth got the parallel probe instead.
    assert len(seen["providers"]) == registry.MAX_TOOL_PROVIDERS
    assert swept["only"] == {"hubspot"}
    assert swept["min_terms"] == 1
    assert swept["budget_s"] == registry.SWEEP_PRIME_BUDGET_S
    assert "LIVE CROSS-SOURCE SWEEP" in seen["primed_context"]
    # And crucially it is NOT reported as a source we failed to check.
    assert seen["unavailable_names"] == []
    # The loop's wall clock shrinks by what priming spent, rather than stacking.
    assert seen["budget_penalty_s"] == registry.SWEEP_PRIME_BUDGET_S


def test_overflow_source_the_sweep_could_not_read_stays_in_the_honest_list(
    monkeypatch
):
    """Breadth must not become a lie: a source the sweep failed to read is still
    declared uncovered."""
    from app import db

    monkeypatch.setattr(db, "list_connections", lambda cid: [
        {"provider": "jira"}, {"provider": "clickup"},
        {"provider": "confluence"}, {"provider": "hubspot"},
    ])
    monkeypatch.setattr(db, "list_slack_connections", lambda cid: [])
    monkeypatch.setattr(
        "app.connector_lookup.sweep.sweep", lambda eid, q, **k: _StubSweep([])
    )
    seen = {}
    monkeypatch.setattr(ca, "answer", lambda **k: seen.update(k) or {"answer": "x"})

    registry.answer_for_hints(
        enterprise_id="co-a",
        question="what do jira, clickup, confluence and hubspot say about billing",
        history=None, hints={"jira", "clickup", "confluence", "hubspot"},
    )

    assert seen["primed_context"] == ""
    assert "HubSpot" in seen["unavailable_names"]


def test_priming_failure_never_breaks_the_lookup(monkeypatch):
    from app import db

    monkeypatch.setattr(db, "list_connections", lambda cid: [
        {"provider": "jira"}, {"provider": "clickup"},
        {"provider": "confluence"}, {"provider": "hubspot"},
    ])
    monkeypatch.setattr(db, "list_slack_connections", lambda cid: [])

    def _boom(eid, q, **k):
        raise RuntimeError("sweep exploded")

    monkeypatch.setattr("app.connector_lookup.sweep.sweep", _boom)
    seen = {}
    monkeypatch.setattr(ca, "answer", lambda **k: seen.update(k) or {"answer": "x"})

    out = registry.answer_for_hints(
        enterprise_id="co-a",
        question="what do jira, clickup, confluence and hubspot say about billing",
        history=None, hints={"jira", "clickup", "confluence", "hubspot"},
    )

    assert out == {"answer": "x"}
    assert seen["primed_context"] == ""
    assert "HubSpot" in seen["unavailable_names"]


def test_one_and_two_provider_lookups_never_prime(monkeypatch):
    """The common case must compose byte-identically to before this change —
    including the Jira shim, whose verbatim prompt knows nothing of priming."""
    from app import db

    monkeypatch.setattr(db, "list_connections", lambda cid: [
        {"provider": "jira"}, {"provider": "clickup"},
    ])
    monkeypatch.setattr(db, "list_slack_connections", lambda cid: [])
    called = []
    monkeypatch.setattr(
        "app.connector_lookup.sweep.sweep",
        lambda eid, q, **k: called.append(eid) or _StubSweep([]),
    )
    seen = {}
    monkeypatch.setattr(ca, "answer", lambda **k: seen.update(k) or {"answer": "x"})

    registry.answer_for_hints(
        enterprise_id="co-a", question="check jira and clickup",
        history=None, hints={"jira", "clickup"},
    )

    assert called == []
    assert seen["primed_context"] == ""
    assert seen["budget_penalty_s"] == 0.0


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


def test_registry_tells_the_loop_which_half_it_is_not_reading(monkeypatch):
    """"check slack and zendesk" — the loop must be told Zendesk went unread, or
    a Slack-only answer sounds like it covered both."""
    seen = {}
    monkeypatch.setattr(ca, "answer", lambda **k: seen.update(k) or {"answer": "x"})
    registry.answer_for_hints(enterprise_id="co-a", question="check slack and zendesk",
                              history=None, hints={"slack", "zendesk"})
    assert seen["unavailable_names"] == ["Zendesk"]


def test_three_connected_sources_are_all_covered_and_none_apologised_for(
    monkeypatch
):
    """Was `test_registry_reports_providers_dropped_by_the_two_cap`, which
    asserted that one of three named sources got dropped and named as unread.
    That WAS the bug — three named, three covered, nothing to apologise for."""
    from app import db

    monkeypatch.setattr(db, "list_connections",
                        lambda cid: [{"provider": "clickup"}, {"provider": "jira"}])
    monkeypatch.setattr(db, "list_slack_connections", lambda cid: [{"id": "1"}])
    seen = {}
    monkeypatch.setattr(ca, "answer", lambda **k: seen.update(k) or {"answer": "x"})
    registry.answer_for_hints(enterprise_id="co-a",
                              question="check slack, jira and clickup",
                              history=None, hints={"slack", "jira", "clickup"})
    assert {p.provider for p in seen["providers"]} == {"slack", "jira", "clickup"}
    assert seen["unavailable_names"] == []


def test_a_source_the_sweep_cannot_reach_wins_a_tool_slot(monkeypatch):
    """Selection is not alphabetical. Google Drive has no sweep leg, so losing a
    slot would make it unreachable; a sweepable source is still covered."""
    from app import db

    monkeypatch.setattr(db, "list_connections", lambda cid: [
        {"provider": "jira"}, {"provider": "clickup"},
        {"provider": "confluence"}, {"provider": "google_drive"},
    ])
    monkeypatch.setattr(db, "list_slack_connections", lambda cid: [])
    monkeypatch.setattr(
        "app.connector_lookup.sweep.sweep",
        lambda eid, q, **k: _StubSweep({"jira"}),
    )
    seen = {}
    monkeypatch.setattr(ca, "answer", lambda **k: seen.update(k) or {"answer": "x"})

    registry.answer_for_hints(
        enterprise_id="co-a",
        question="check jira, clickup, confluence and google drive for billing",
        history=None,
        hints={"jira", "clickup", "confluence", "google_drive"},
    )

    assert "google_drive" in {p.provider for p in seen["providers"]}


# ══════════════════ the two adapters this ticket added ══════════════════
#
# Asana and Google Meet were DEFERRED — connected, syncing into the KG, and
# unreadable from chat. These tests cover what each one actually does, and in
# particular the two properties the sweep depends on: `dispatch_records` must
# return the SAME text `dispatch` would (or the sweep silently changes what the
# model reads depending on which caller ran), and the Meet scan must stop
# ITSELF, because it is the only leg whose probe walks a customer's data rather
# than making one search call.


# ───────────────────────────── Asana ─────────────────────────────


def _asana_session(monkeypatch, *, workspaces, typeahead):
    """A live Asana session over stubbed HTTP. `typeahead` is
    `{workspace_gid: [hit, ...]}` or a callable raising for a chosen gid."""
    from app.connector_lookup import asana as ad
    from app.connectors import asana_oauth
    from app.stories import push as stories_push

    monkeypatch.setattr(stories_push, "_asana_creds", lambda cid: "tok-123")
    # `**kw` at the moment of authorship — this double does not assert on its
    # arguments, and a narrow signature breaks in a file the next change does
    # not appear to touch.
    monkeypatch.setattr(
        asana_oauth, "list_workspaces", lambda tok, **kw: workspaces
    )

    def _typeahead(tok, gid, query, **kw):
        found = typeahead(gid) if callable(typeahead) else typeahead.get(gid, [])
        return found

    monkeypatch.setattr(asana_oauth, "typeahead_tasks", _typeahead)
    return ad.PROVIDER, ad.PROVIDER.open_session("co-a")


def _asana_hit(gid, name, *, project="Growth", section="In progress", done=False):
    return {
        "gid": gid,
        "name": name,
        "completed": done,
        "permalink_url": f"https://app.asana.com/0/1/{gid}",
        "modified_at": "2026-08-01T10:00:00.000Z",
        "assignee": {"name": "Sam Lee"},
        "memberships": [
            {"project": {"gid": "p1", "name": project},
             "section": {"gid": "s1", "name": section}},
        ],
    }


def test_asana_open_session_is_none_when_the_credential_is_unusable(monkeypatch):
    """`open_session` must never raise (base.LookupProvider) — an expired or
    absent Asana connection is None, which the framework renders as honest
    not-connected copy rather than a stack trace in a chat answer."""
    from app.connector_lookup import asana as ad
    from app.stories import push as stories_push

    def _boom(cid):
        raise RuntimeError("Asana authorization expired")

    monkeypatch.setattr(stories_push, "_asana_creds", _boom)
    assert ad.PROVIDER.open_session("co-a") is None


def test_asana_search_spans_workspaces_and_dedupes(monkeypatch):
    provider, session = _asana_session(
        monkeypatch,
        workspaces=[{"gid": "w1", "name": "Acme"}, {"gid": "w2", "name": "Labs"}],
        typeahead={
            "w1": [_asana_hit("1", "Checkout redesign spec")],
            # Same task visible from both workspaces, plus one more.
            "w2": [_asana_hit("1", "Checkout redesign spec"),
                   _asana_hit("2", "Checkout redesign QA")],
        },
    )

    out = provider.dispatch(session, "asana_search_tasks", {"text": "checkout"})

    assert out.count("Checkout redesign spec") == 1, "a task was listed twice"
    assert "Checkout redesign QA" in out
    assert "2 Asana workspace(s) searched by task TITLE" in out


def test_asana_search_reports_the_workspaces_it_actually_searched(monkeypatch):
    """A workspace whose typeahead fails is skipped — and EXCLUDED from the
    count, so "2 workspaces searched" never means "2 attempted, 1 answered"."""
    def _typeahead(gid):
        if gid == "w2":
            raise RuntimeError("500 from Asana")
        return [_asana_hit("1", "Checkout redesign spec")]

    provider, session = _asana_session(
        monkeypatch,
        workspaces=[{"gid": "w1"}, {"gid": "w2"}],
        typeahead=_typeahead,
    )

    out = provider.dispatch(session, "asana_search_tasks", {"text": "checkout"})

    assert "1 Asana workspace(s) searched" in out


def test_asana_auth_failure_is_not_swallowed_as_an_empty_result(monkeypatch):
    """A dead token must surface as an unread source, never as "Asana has
    nothing" — the false-absence failure this whole module argues against."""
    from app.connectors import asana_oauth

    def _typeahead(gid):
        raise asana_oauth.AsanaAuthExpiredError("reconnect Asana")

    provider, session = _asana_session(
        monkeypatch, workspaces=[{"gid": "w1"}], typeahead=_typeahead
    )

    with pytest.raises(asana_oauth.AsanaAuthExpiredError):
        provider.dispatch(session, "asana_search_tasks", {"text": "checkout"})


def test_asana_empty_search_states_that_only_titles_were_matched(monkeypatch):
    """Asana's full-text search is a paid-plan API, so this matches TITLES. An
    empty result that did not say so would license "Asana has nothing about
    this" from a search that never read a single description."""
    provider, session = _asana_session(
        monkeypatch, workspaces=[{"gid": "w1"}], typeahead={"w1": []}
    )

    out = provider.dispatch(session, "asana_search_tasks", {"text": "checkout"})

    assert "no Asana task TITLE matches" in out
    assert "descriptions and comments are not searched" in out


def test_asana_dispatch_records_text_is_identical_to_dispatch(monkeypatch):
    """base.RecordsCapable requires byte-identity: the sweep calls
    `dispatch_records` INSTEAD of `dispatch`, so any divergence changes what
    the model reads depending on which caller ran."""
    provider, session = _asana_session(
        monkeypatch,
        workspaces=[{"gid": "w1"}],
        typeahead={"w1": [_asana_hit("1", "Checkout redesign spec")]},
    )
    inp = {"text": "checkout"}

    plain = provider.dispatch(session, "asana_search_tasks", inp)
    text, records = provider.dispatch_records(session, "asana_search_tasks", inp)

    assert text == plain
    assert [r.external_id for r in records] == ["1"]
    record = records[0]
    assert record.provider == "asana" and record.kind == "task"
    assert record.title == "Checkout redesign spec"
    assert record.properties["section"] == "In progress"
    assert record.render()


def test_asana_get_task_reports_a_missing_task_rather_than_inventing_one(
    monkeypatch,
):
    from app.connectors import asana_oauth

    provider, session = _asana_session(
        monkeypatch, workspaces=[{"gid": "w1"}], typeahead={"w1": []}
    )
    monkeypatch.setattr(asana_oauth, "get_task_raw", lambda tok, gid: None)

    out = provider.dispatch(session, "asana_get_task", {"task_id": "999"})

    assert "no Asana task found with id 999" in out


def test_asana_read_helpers_use_the_chat_http_bound():
    """base.HTTP_TIMEOUT is the framework's promise that one slow upstream
    cannot hold a chat answer open. `connectors/` cannot import
    `connector_lookup/`, so the coupling lives here rather than in the code."""
    from app.connector_lookup import base
    from app.connectors import asana_oauth

    assert asana_oauth._READ_TIMEOUT == base.HTTP_TIMEOUT


def test_asana_list_workspaces_timeout_is_additive_with_an_inert_default(
    monkeypatch,
):
    """`list_workspaces` grew a `timeout` for ONE caller (the chat search).
    Every pre-existing caller — the ticket-sync push, the KG puller — must keep
    the sync path's `_WRITE_TIMEOUT`, so the inertness is asserted rather than
    claimed: pass nothing, get exactly what was sent before."""
    from app.connectors import asana_oauth

    seen: list = []

    class _Resp:
        status_code = 200
        ok = True
        text = ""

        def json(self):
            return {"data": []}

    monkeypatch.setattr(
        asana_oauth.requests, "get",
        lambda url, **kw: (seen.append(kw["timeout"]), _Resp())[1],
    )

    asana_oauth.list_workspaces("tok")
    assert seen == [asana_oauth._WRITE_TIMEOUT]

    asana_oauth.list_workspaces("tok", timeout=asana_oauth._READ_TIMEOUT)
    assert seen[-1] == asana_oauth._READ_TIMEOUT


# ─────────────────────────── Google Meet ───────────────────────────


def _meet_session(monkeypatch, *, conferences, transcripts, delay=0.0):
    """A live Meet session over stubbed HTTP. `transcripts` is
    `{conference_name: text}`; a name absent from it has no ready transcript."""
    import time as _t

    from app.connector_lookup import google_meet as gm
    from app.connectors.google_meet import MeetContext

    monkeypatch.setattr(
        gm, "sync_context",
        lambda cid: MeetContext(
            company_id=cid, access_token="tok", account_email="pm@acme.com"
        ),
    )
    monkeypatch.setattr(gm, "list_conference_records", lambda tok, **kw: conferences)
    monkeypatch.setattr(gm, "_speaker_map", lambda ctx, name: {"p1": "Sam Lee"})

    reads: list[str] = []

    def _transcript_text(ctx, name, speakers):
        reads.append(name)
        if delay:
            _t.sleep(delay)
        text = transcripts.get(name, "")
        return (text, ["Sam Lee"] if text else [], bool(text))

    monkeypatch.setattr(gm, "_transcript_text", _transcript_text)
    return gm.PROVIDER, gm.PROVIDER.open_session("co-a"), reads


def _conf(n, day="2026-08-0"):
    return {
        "name": f"conferenceRecords/{n}",
        "startTime": f"{day}{n}T09:00:00Z",
        "endTime": f"{day}{n}T09:45:00Z",
    }


def test_meet_open_session_is_none_when_not_connected(monkeypatch):
    from app.connector_lookup import google_meet as gm
    from app.connectors.google_meet import MeetNotConnectedError

    def _boom(cid):
        raise MeetNotConnectedError("no row")

    monkeypatch.setattr(gm, "sync_context", _boom)
    assert gm.PROVIDER.open_session("co-a") is None


def test_meet_search_matches_on_transcript_because_there_is_no_title(monkeypatch):
    """A Meet conference record carries `{name, startTime, endTime, space}` and
    NO subject line, so the only thing to keyword-match is what was said."""
    provider, session, reads = _meet_session(
        monkeypatch,
        conferences=[_conf("1"), _conf("2")],
        transcripts={
            "conferenceRecords/1": "Sam Lee: the checkout redesign slipped a week",
            "conferenceRecords/2": "Sam Lee: renewal pricing for Acme",
        },
    )

    out = provider.dispatch(
        session, "google_meet_search_transcripts", {"keywords": "checkout redesign"}
    )

    assert "conferenceRecords/1" in out
    assert "conferenceRecords/2" not in out
    assert "checkout redesign slipped" in out
    assert reads == ["conferenceRecords/1", "conferenceRecords/2"]


def test_meet_search_stops_at_the_conference_cap_and_says_so(monkeypatch):
    from app.connector_lookup import google_meet as gm

    monkeypatch.setattr(gm, "_MAX_SCAN_CONFERENCES", 2)
    provider, session, reads = _meet_session(
        monkeypatch,
        conferences=[_conf(str(i)) for i in range(1, 7)],
        transcripts={},
    )

    out = provider.dispatch(
        session, "google_meet_search_transcripts", {"keywords": "checkout"}
    )

    assert len(reads) == 2, "the scan ignored its own conference cap"
    assert "2 of 6 Google Meet call(s)" in out
    assert "stopped after the 2 most recent calls" in out


def test_meet_search_abandons_itself_at_its_own_deadline(monkeypatch):
    """THE reason this leg is allowed into the sweep at all.

    Every other live leg is one search call, so abandoning it costs one
    in-flight request. Meet walks conferences and reads a transcript per
    conference, so without an internal deadline an abandoned leg keeps working
    against a customer's Google account with nobody listening — the exact
    failure mode `google_drive` is kept out of the sweep for.
    """
    import time as _t

    from app.connector_lookup import google_meet as gm

    monkeypatch.setattr(gm, "SCAN_BUDGET_S", 0.25)
    provider, session, reads = _meet_session(
        monkeypatch,
        conferences=[_conf(str(i)) for i in range(1, 21)],
        transcripts={},
        delay=0.1,
    )

    started = _t.monotonic()
    out = provider.dispatch(
        session, "google_meet_search_transcripts", {"keywords": "checkout"}
    )
    elapsed = _t.monotonic() - started

    assert elapsed < 2.0, "the scan ran past its own deadline"
    assert 0 < len(reads) < 20, "the scan read every conference regardless"
    assert "stopped at this lookup's time budget" in out
    # A partial scan must never render as a complete search.
    assert f"{len(reads)} of 20 Google Meet call(s)" in out


def test_meet_empty_search_says_what_it_did_not_cover(monkeypatch):
    provider, session, _reads = _meet_session(
        monkeypatch,
        conferences=[_conf("1")],
        transcripts={"conferenceRecords/1": "Sam Lee: renewal pricing"},
    )

    out = provider.dispatch(
        session, "google_meet_search_transcripts", {"keywords": "checkout"}
    )

    assert "no Google Meet transcript mentions these terms" in out
    assert "transcription switched" in out


def test_meet_dispatch_records_reuses_the_cached_transcripts(monkeypatch):
    """Byte-identical text (base.RecordsCapable) AND no second fetch: the
    records are built from transcripts the search already read."""
    provider, session, reads = _meet_session(
        monkeypatch,
        conferences=[_conf("1")],
        transcripts={"conferenceRecords/1": "Sam Lee: the checkout redesign slipped"},
    )
    inp = {"keywords": "checkout"}

    plain = provider.dispatch(session, "google_meet_search_transcripts", inp)
    before = len(reads)
    text, records = provider.dispatch_records(
        session, "google_meet_search_transcripts", inp
    )

    assert text == plain
    assert len(reads) == before, "records cost a second transcript read"
    assert [r.external_id for r in records] == ["conferenceRecords/1"]
    record = records[0]
    assert record.provider == "google_meet" and record.kind == "meeting"
    assert "checkout redesign slipped" in record.text
    assert record.properties["organizer_email"] == "pm@acme.com"
    assert record.properties["has_transcript"] is True


def test_meet_distinguishes_no_transcript_from_a_failed_read(monkeypatch):
    """Two sentences that must never collapse into one: "nobody switched
    transcription on" is a claim about the customer's settings, and a 500 from
    Google is not evidence of it."""
    from app.connector_lookup import google_meet as gm

    provider, session, _reads = _meet_session(
        monkeypatch, conferences=[_conf("1")], transcripts={}
    )
    # Populate the handle's conference cache.
    provider.dispatch(
        session, "google_meet_search_transcripts", {"keywords": "checkout"}
    )

    absent = provider.dispatch(
        session, "google_meet_get_transcript",
        {"conference_id": "conferenceRecords/1"},
    )
    assert "NOT an empty meeting" in absent

    session.handle.transcripts.clear()

    def _boom(ctx, name, speakers):
        raise RuntimeError("502 from Google")

    monkeypatch.setattr(gm, "_transcript_text", _boom)
    failed = provider.dispatch(
        session, "google_meet_get_transcript",
        {"conference_id": "conferenceRecords/1"},
    )
    assert "could not be read just now" in failed
    assert "NOT evidence that the meeting was never transcribed" in failed


def test_meet_unknown_conference_id_is_reported_with_the_retention_limit(
    monkeypatch,
):
    from app.connectors.google_meet import RETENTION_DAYS

    provider, session, _reads = _meet_session(
        monkeypatch, conferences=[], transcripts={}
    )

    out = provider.dispatch(
        session, "google_meet_get_transcript", {"conference_id": "nope"}
    )

    assert f"last {RETENTION_DAYS} days" in out
    assert "this account organized" in out


def test_meet_system_block_states_every_limit_that_is_googles(monkeypatch):
    """The three limits a Meet answer can be wrong about — no title, 30-day
    retention, organizer-only coverage — must be in the model's instructions,
    not only in ours."""
    from app.connector_lookup import google_meet as gm

    block = gm.PROVIDER.system_block()

    assert "NO TITLE" in block
    assert "30 DAYS" in block
    assert "ORGANIZED" in block
    assert "not \"never discussed\"" in block


def test_meet_read_deadline_bounds_the_http_timeout_and_the_backoff(monkeypatch):
    """The bound a between-requests clock check CANNOT provide.

    `connectors.google_meet.api_get` retries a 429 up to `_MAX_ATTEMPTS`,
    honouring `Retry-After` up to `_MAX_BACKOFF_S` (30s). So ONE call can
    legitimately occupy three HTTP timeouts plus two 30-second sleeps — about
    two minutes — and a sweep leg abandoned at the 8s budget would hold a
    worker thread for all of it, spending a customer's Google quota with
    nobody left to read the answer. That is the failure mode `google_drive` is
    kept out of the sweep for, and this is why Meet is allowed in.

    Asserts three things, all of which have to hold together: the request
    timeout is clamped to what is left, the backoff refuses to sleep past the
    deadline, and an expired deadline issues no request at all.
    """
    import time as _t

    from app.connectors import google_meet as gmc

    class _Resp:
        status_code = 429
        headers = {"Retry-After": "30"}
        text = ""

        def json(self):
            return {}

    seen: list[float] = []

    def _fake_get(url, **kw):
        seen.append(kw["timeout"])
        return _Resp()

    monkeypatch.setattr(gmc.requests, "get", _fake_get)
    monkeypatch.setattr(gmc.time, "sleep", lambda s: pytest.fail(
        "a bounded read slept through its own deadline"
    ))

    # Deadline 1s out: the request timeout is clamped well under _TIMEOUT, and
    # a 30s backoff is refused rather than slept.
    started = _t.monotonic()
    with gmc.read_deadline(_t.monotonic() + 1.0):
        with pytest.raises(gmc.MeetDeadlineExceeded):
            gmc.api_get("tok", "https://meet.example/x", what="probe")
    assert _t.monotonic() - started < 1.0, "the read waited out its own deadline"
    assert seen and seen[0] <= 1.0 < gmc._TIMEOUT

    # Deadline already passed: no request is issued at all.
    before = len(seen)
    with gmc.read_deadline(_t.monotonic() - 1.0):
        with pytest.raises(gmc.MeetDeadlineExceeded):
            gmc.api_get("tok", "https://meet.example/x", what="probe")
    assert len(seen) == before, "issued a request with no time left to receive it"


def test_meet_read_deadline_is_inert_for_every_other_caller(monkeypatch):
    """Additive with an inert default: the KG puller and the connector probe
    must see byte-identical behaviour, so the deadline is asserted ABSENT
    unless a caller opts in."""
    from app.connectors import google_meet as gmc

    class _Ok:
        status_code = 200
        ok = True
        headers: dict = {}
        text = ""

        def json(self):
            return {"conferenceRecords": []}

    seen: list[float] = []
    monkeypatch.setattr(
        gmc.requests, "get",
        lambda url, **kw: (seen.append(kw["timeout"]), _Ok())[1],
    )

    assert gmc._read_deadline.get() is None
    gmc.api_get("tok", "https://meet.example/x", what="probe")
    assert seen == [float(gmc._TIMEOUT)]

    # And the ContextVar is restored even when the bounded block raises.
    with pytest.raises(RuntimeError):
        with gmc.read_deadline(1.0):
            raise RuntimeError("boom")
    assert gmc._read_deadline.get() is None


def test_meet_deep_read_is_not_bounded_by_the_scan_budget(monkeypatch):
    """Breadth is rationed, depth is not. `google_meet_get_transcript` is
    reached AFTER a search has said which call matters, so it must be free to
    take as long as reading one transcript takes — bounding it would make the
    tool that exists to read a whole conversation return half of one."""
    from app.connector_lookup import google_meet as gm
    from app.connectors import google_meet as gmc

    provider, session, _reads = _meet_session(
        monkeypatch,
        conferences=[_conf("1")],
        transcripts={"conferenceRecords/1": "Sam Lee: the checkout redesign slipped"},
    )
    seen: list = []

    def _transcript_text(ctx, name, speakers):
        seen.append(gmc._read_deadline.get())
        return ("Sam Lee: the checkout redesign slipped", ["Sam Lee"], True)

    monkeypatch.setattr(gm, "_transcript_text", _transcript_text)

    provider.dispatch(
        session, "google_meet_get_transcript",
        {"conference_id": "conferenceRecords/1"},
    )

    assert seen == [None], "the deep read inherited the scan's deadline"


# ─── the adapter side of the review findings ───


def test_meet_listing_failure_raises_rather_than_returning_prose(monkeypatch):
    """Review HIGH, at the source. Returning a failure SENTENCE made
    `sweep._run_live` mark the leg STATUS_OK and `sweep_persist` write the
    error string into the tenant's graph. Both failure modes of the listing —
    the deadline and a transport error — must raise."""
    from app.connector_lookup import google_meet as gm
    from app.connectors.google_meet import MeetDeadlineExceeded

    provider, session, _reads = _meet_session(
        monkeypatch, conferences=[], transcripts={}
    )

    def _deadline(tok, **kw):
        raise MeetDeadlineExceeded("listing ran out of budget")

    monkeypatch.setattr(gm, "list_conference_records", _deadline)
    with pytest.raises(MeetDeadlineExceeded):
        provider.dispatch(
            session, "google_meet_search_transcripts", {"keywords": "checkout"}
        )

    def _broken(tok, **kw):
        raise RuntimeError("502 from Google")

    monkeypatch.setattr(gm, "list_conference_records", _broken)
    with pytest.raises(RuntimeError):
        provider.dispatch(
            session, "google_meet_search_transcripts", {"keywords": "checkout"}
        )


def test_meet_a_scan_that_opened_nothing_raises(monkeypatch):
    """The smaller form of the same defect. "0 of 12 calls had their transcript
    read" is honest prose and is still not a SEARCH — and non-empty text is
    what becomes STATUS_OK and gets persisted."""
    from app.connector_lookup import google_meet as gm
    from app.connectors.google_meet import MeetDeadlineExceeded

    monkeypatch.setattr(gm, "SCAN_BUDGET_S", -1.0)  # already expired on entry
    provider, session, reads = _meet_session(
        monkeypatch,
        conferences=[_conf(str(i)) for i in range(1, 5)],
        transcripts={},
    )

    with pytest.raises(MeetDeadlineExceeded):
        provider.dispatch(
            session, "google_meet_search_transcripts", {"keywords": "checkout"}
        )
    assert reads == []


def test_meet_a_genuine_empty_result_is_still_prose(monkeypatch):
    """The other side of the line, so the fix does not over-reach: a search
    that really ran and matched nothing is a REAL answer about a real search
    and must stay a rendered result, not an exception."""
    provider, session, reads = _meet_session(
        monkeypatch,
        conferences=[_conf("1")],
        transcripts={"conferenceRecords/1": "Sam Lee: renewal pricing"},
    )

    out = provider.dispatch(
        session, "google_meet_search_transcripts", {"keywords": "checkout"}
    )

    assert reads == ["conferenceRecords/1"]
    assert "no Google Meet transcript mentions these terms" in out


def test_meet_hit_cap_relationship_is_pinned():
    """`cap_items(hits, _MAX_HITS)` is unreachable while the two caps are
    equal, because hits are a subset of the conferences read. Kept as a
    backstop and pinned here, so raising the scan cap past it becomes a
    deliberate decision about how many calls to NAME."""
    from app.connector_lookup import google_meet as gm

    assert gm._MAX_HITS >= gm._MAX_SCAN_CONFERENCES


def test_asana_search_is_bounded_by_its_own_wall_clock(monkeypatch):
    """Review MEDIUM. One search was `list_workspaces` + up to 3 sequential
    typeahead calls at 15s each — ~60s — while `sweep._run_live` abandons the
    leg at 8s with `shutdown(wait=False)`, which does not cancel a running
    thread. The worker kept calling a customer's Asana for ~52s with nobody
    listening: the exact shape `google_drive` is kept out of the sweep for.

    Asserts the clamp (each call gets what is LEFT, never the full 15s) and the
    stop (the scan abandons remaining workspaces rather than walking them all).
    """
    import time as _t

    from app.connector_lookup import asana as ad
    from app.connectors import asana_oauth

    # Above _MIN_CALL_TIMEOUT_S, or the first call is skipped before it is
    # ever made and the clamp has nothing to demonstrate.
    monkeypatch.setattr(ad, "SCAN_BUDGET_S", 1.4)
    timeouts: list = []

    def _slow_typeahead(tok, gid, query, *, timeout=None, **kw):
        timeouts.append(timeout)
        _t.sleep(0.6)
        return []

    provider, session = _asana_session(
        monkeypatch,
        workspaces=[{"gid": "w1"}, {"gid": "w2"}, {"gid": "w3"}],
        typeahead={"w1": [_asana_hit("1", "Checkout redesign spec")]},
    )
    monkeypatch.setattr(asana_oauth, "typeahead_tasks", _slow_typeahead)

    started = _t.monotonic()
    try:
        provider.dispatch(session, "asana_search_tasks", {"text": "checkout"})
    except (TimeoutError, RuntimeError):
        pass  # "nothing searched" is a legitimate outcome at this budget
    elapsed = _t.monotonic() - started

    assert elapsed < 3.0, "the scan ran past its own budget"
    assert len(timeouts) < 3, "walked every workspace regardless of the clock"
    assert timeouts and all(
        t is not None and t <= ad.SCAN_BUDGET_S for t in timeouts
    ), f"a call was given the full socket timeout instead of the remainder: {timeouts}"


def test_asana_partial_coverage_says_it_stopped_early(monkeypatch):
    """A budget stop is honest prose, not an exception, PROVIDED something was
    actually searched — the result must say more workspaces exist."""
    import time as _t

    from app.connector_lookup import asana as ad
    from app.connectors import asana_oauth

    monkeypatch.setattr(ad, "SCAN_BUDGET_S", 0.9)
    provider, session = _asana_session(
        monkeypatch, workspaces=[{"gid": "w1"}, {"gid": "w2"}, {"gid": "w3"}],
        typeahead={},
    )

    def _one_then_slow(tok, gid, query, *, timeout=None, **kw):
        _t.sleep(0.5)
        return [_asana_hit("1", "Checkout redesign spec")] if gid == "w1" else []

    monkeypatch.setattr(asana_oauth, "typeahead_tasks", _one_then_slow)

    out = provider.dispatch(session, "asana_search_tasks", {"text": "checkout"})

    assert "stopped at this lookup's time budget" in out
    assert "Checkout redesign spec" in out


def test_asana_nothing_searched_raises_rather_than_reporting_empty(monkeypatch):
    """Same HIGH-finding line as Meet's: a search where every workspace failed
    is not an empty RESULT, and prose here would be promoted to STATUS_OK and
    written to the graph."""
    from app.connectors import asana_oauth

    def _always_fails(gid):
        raise RuntimeError("500 from Asana")

    provider, session = _asana_session(
        monkeypatch, workspaces=[{"gid": "w1"}, {"gid": "w2"}],
        typeahead=_always_fails,
    )

    with pytest.raises(RuntimeError):
        provider.dispatch(session, "asana_search_tasks", {"text": "checkout"})


def test_asana_read_helpers_accept_a_clamped_timeout():
    """The clamp needs somewhere to land. Additive with inert defaults, so the
    sync path and the KG puller keep `_READ_TIMEOUT`."""
    import inspect

    from app.connectors import asana_oauth

    for fn in (asana_oauth.typeahead_tasks, asana_oauth.get_task_raw,
               asana_oauth.list_workspaces):
        sig = inspect.signature(fn)
        assert "timeout" in sig.parameters, fn.__name__
        assert sig.parameters["timeout"].default is None, fn.__name__


# ── KG-only fallback + KG-under-verbatim-system enablers ─────────────────────
# When `include_knowledge_graph=True`, a caller with nothing live to read no
# longer dead-ends on the connect copy — it degrades to a KG-only tool loop —
# and a caller passing a verbatim `system_text` (Jira) still gets the KG tool,
# with the KG system block appended so the tuned prompt knows it exists. Every
# assertion here is enforced in answer.py, gated on the flag; flag-off callers
# are byte-identical to before (see test_not_connected_with_flag_off_is_unchanged).


def test_kg_only_fallback_runs_when_nothing_connected_and_flag_on(monkeypatch):
    from app.connector_lookup import knowledge_graph as kg

    monkeypatch.setattr(
        kg, "search",
        lambda eid, q: "SEEDED KG SIGNAL: the checkout task is In Review.",
    )
    captured = {}

    def loop(**k):
        captured.update(k)
        return k["dispatch"](kg.TOOL_NAME, {"query": "checkout"})

    out = ca.answer(
        enterprise_id="co-a", question="what's the status of the checkout task?",
        providers=[FakeProvider("clickup", connected=False)],
        include_knowledge_graph=True, run_loop=loop, log=lambda *a: None,
    )
    # The loop WAS entered (not the deterministic connect copy)...
    assert captured, "the KG-only loop was never entered"
    # ...with ONLY the knowledge-graph tool offered (nothing is connected)...
    assert {t["name"] for t in captured["tools"]} == {kg.TOOL_NAME}
    # ...and the answer is drawn from the KG, not a false-deny.
    assert "checkout task is In Review" in out["answer"]
    assert "isn't connected" not in out["answer"]


def test_not_connected_with_flag_off_is_unchanged():
    called = []
    out = ca.answer(
        enterprise_id="co-a", question="check clickup for the checkout task",
        providers=[FakeProvider("clickup", connected=False)],
        run_loop=lambda **k: called.append(k), log=lambda *a: None,
    )
    assert called == []
    assert "Clickup isn't connected yet" in out["answer"]


def test_kg_tool_offered_alongside_verbatim_system_text():
    from app.connector_lookup import knowledge_graph as kg

    captured = {}
    ca.answer(
        enterprise_id="co-a", question="status of PROJ-1",
        providers=[FakeProvider("jira")],
        system_text="JIRA RULES", include_knowledge_graph=True,
        run_loop=lambda **k: captured.update(k) or "x", log=lambda *a: None,
    )
    assert kg.TOOL_NAME in {t["name"] for t in captured["tools"]}
    # The verbatim prompt is preserved AND the KG block is appended.
    assert "JIRA RULES" in captured["system"]
    assert "## Sprntly knowledge graph" in captured["system"]
    assert "accumulated knowledge" in captured["system"]  # a fragment of kg.SYSTEM


def test_verbatim_system_text_without_flag_still_excludes_kg():
    from app.connector_lookup import knowledge_graph as kg

    captured = {}
    ca.answer(
        enterprise_id="co-a", question="status of PROJ-1",
        providers=[FakeProvider("jira")], system_text="JIRA RULES",
        run_loop=lambda **k: captured.update(k) or "x", log=lambda *a: None,
    )
    assert kg.TOOL_NAME not in {t["name"] for t in captured["tools"]}
    # The tuned prompt is preserved and the KG block is NOT appended — which is
    # what this test is for. It is no longer byte-equal, because the framework
    # now also appends its presentation rule (charts): a tuned adapter prompt
    # cannot know about a renderer contract that is not its concern, and the
    # one path that skipped `_build_system` was the one answering "tickets per
    # status" with a markdown table. Asserting equality here would mean any
    # framework-wide rule could only ever reach half the adapters.
    assert captured["system"].startswith("JIRA RULES")
    assert "## Sprntly knowledge graph" not in captured["system"]
    assert "```chart" in captured["system"]


def test_kg_only_fallback_is_tenant_scoped(monkeypatch):
    from app.connector_lookup import knowledge_graph as kg

    seen = {}
    monkeypatch.setattr(
        kg, "search",
        lambda eid, q: kg.EMPTY if not eid
        else (seen.__setitem__("eid", eid) or f"kg for {eid}"),
    )
    ca.answer(
        enterprise_id="tenant-xyz", question="q",
        providers=[FakeProvider("clickup", connected=False)],
        include_knowledge_graph=True,
        run_loop=lambda **k: k["dispatch"](kg.TOOL_NAME, {"query": "x"}),
        log=lambda *a: None,
    )
    # The tenant reaching the KG dispatch is exactly the caller's tenant.
    assert seen["eid"] == "tenant-xyz"
    # A blank tenant yields the empty-KG note, never a cross-tenant read.
    out = ca.answer(
        enterprise_id="", question="q",
        providers=[FakeProvider("clickup", connected=False)],
        include_knowledge_graph=True,
        run_loop=lambda **k: k["dispatch"](kg.TOOL_NAME, {"query": "x"}),
        log=lambda *a: None,
    )
    assert kg.EMPTY in out["answer"]


def test_kg_retrieval_failure_degrades_readably(monkeypatch):
    from app.connector_lookup import knowledge_graph as kg

    def boom(eid, q):
        raise RuntimeError("pgvector down")

    monkeypatch.setattr(kg, "search", boom)
    out = ca.answer(
        enterprise_id="co-a", question="q",
        providers=[FakeProvider("clickup", connected=False)],
        include_knowledge_graph=True,
        run_loop=lambda **k: k["dispatch"](kg.TOOL_NAME, {"query": "x"}),
        log=lambda *a: None,
    )
    assert "could not be read just now" in out["answer"]
    assert "pgvector down" not in out["answer"]  # no internals leak


# ── The chart contract reaches BOTH prompt paths ─────────────────────────────
#
# This path answers the most chart-shaped questions in the product — tickets per
# status, issues per assignee, messages per channel — and until 2026-08-27 it
# had no way to draw one, so every answer was a markdown grid of numbers.
#
# Two paths assemble the system prompt and the fix has to be on both. The
# multi-source one goes through `_build_system`; an adapter with its own tuned
# prompt (Jira) passes `system_text` and never touches it. The first attempt
# landed only on `_build_system`, shipped, and "what share of our open tickets
# sits in each status?" came back as a table from an image that already carried
# the rule — because Jira was answering through the other branch.


def test_the_multi_source_prompt_carries_the_chart_contract():
    system = ca._build_system([], None, False, False)
    assert "```chart" in system
    # LAST, after every adapter block: a rule about the shape of the ANSWER has
    # to be read after everything describing where the data came from.
    assert "```chart" in system[-900:]


def test_a_tuned_adapter_prompt_gets_it_appended_too():
    """The Jira-shaped case: `system_text` is verbatim and knows nothing about
    charts, so the framework appends the rule rather than trusting the adapter
    to carry it."""
    captured = {}

    def loop(**kwargs):
        captured.update(kwargs)
        return "answered"

    provider = FakeProvider(result="ok")
    ca.answer(
        enterprise_id="co-a",
        question="how many issues does each assignee have?",
        providers=[provider],
        system_text="You are a Jira assistant. Answer from the tools.",
        run_loop=loop,
        log=lambda *a: None,
    )
    system = captured["system"]
    assert "You are a Jira assistant." in system, "the tuned prompt must survive"
    assert "```chart" in system, "the verbatim path lost the chart contract"
    assert "A COUNT PER THING IS A CHART" in system


def test_no_adapter_has_to_remember_the_contract_itself():
    """One schema, appended by the framework. An adapter shipping its own copy
    is how the two drift into different dialects — and `InlineChart.tsx` refuses
    anything that does not match the one it parses."""
    from app.connector_lookup import jira as jira_adapter

    assert "```chart" not in jira_adapter.SYSTEM
