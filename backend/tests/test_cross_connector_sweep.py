"""Cross-connector sweep — the bounded parallel gather behind a source-agnostic
chat question (app/connector_lookup/sweep.py).

No network, no LLM, no real connector: adapters are fakes registered through
`registry.provider_for`, the local legs are patched at `app.call_index` /
`app.db`, and the fan-out runs against real threads so the timeout path is the
real timeout path rather than a mocked one.

What these lock down, in the order they matter:

- a slow or broken source cannot take the answer with it;
- a source that was NOT read is always NAMED, so a partial sweep can never be
  reported as a complete search;
- nothing is opened for a company that has not connected it, and every session
  is opened from the authenticated enterprise_id;
- a company with no connectors pays nothing and changes nothing.
"""
from __future__ import annotations

import time

import pytest

from app.connector_lookup import sweep as cs
from app.connector_lookup.base import LookupSession


class FakeAdapter:
    """Minimal LookupProvider whose dispatch is scriptable per test."""

    def __init__(self, name, *, connected=True, result="rows", raises=None, delay=0.0):
        self.provider = name
        self.display_name = name.title()
        self.keywords = (name,)
        self._connected = connected
        self._result = result
        self._raises = raises
        self._delay = delay
        self.opened_with: list[str] = []
        self.calls: list[tuple[str, dict]] = []

    def open_session(self, enterprise_id):
        self.opened_with.append(enterprise_id)
        if not self._connected:
            return None
        return LookupSession(provider=self.provider, handle={"tenant": enterprise_id})

    def tools(self):
        return []

    def system_block(self):
        return ""

    def dispatch(self, session, name, inp):
        self.calls.append((name, inp))
        if self._delay:
            time.sleep(self._delay)
        if self._raises is not None:
            raise self._raises
        return self._result


@pytest.fixture
def wire(monkeypatch):
    """Install fake adapters + silence both local legs.

    Returns a callable: `wire({"jira": FakeAdapter(...)}, connected=[...])`.
    Local legs are OFF by default so a live-fan-out test isn't also exercising
    the call index; the tests that want them patch them back on.
    """
    from app.connector_lookup import registry

    def _install(adapters: dict, *, connected=None):
        names = list(adapters) if connected is None else list(connected)
        monkeypatch.setattr(
            registry, "connected_providers", lambda eid: list(names)
        )
        monkeypatch.setattr(
            registry, "provider_for", lambda name: adapters.get(name)
        )
        return adapters

    monkeypatch.setattr(cs, "_has_calls", lambda eid: False)
    monkeypatch.setattr(cs, "_has_github", lambda eid: False)
    return _install


# ─────────────────────────── the term gate ───────────────────────────


@pytest.mark.parametrize(
    "question",
    [
        "make it shorter",
        "make it shorter and punchier",
        "thanks!",
        "yes",
        "rewrite that as bullets",
        "",
    ],
)
def test_no_topic_means_no_sweep(question, wire):
    """The latency gate. An instruction about the ANSWER, or an acknowledgement,
    must not cost a round of I/O against every connector."""
    jira = FakeAdapter("jira")
    wire({"jira": jira})

    result = cs.sweep("ent-1", question)

    assert result.sources == []
    assert result.render() == ""
    assert jira.opened_with == []


def test_topic_question_sweeps(wire):
    jira = FakeAdapter("jira", result="PROJ-1 Checkout redesign")
    wire({"jira": jira})

    result = cs.sweep("ent-1", "where did the checkout redesign land?")

    assert result.terms[:2] == ["checkout", "redesign"]
    assert [s.key for s in result.read] == ["jira"]
    assert "PROJ-1 Checkout redesign" in result.render()


# ─────────────────────────── multi-source assembly ───────────────────────────


def test_assembles_every_connected_source(wire, monkeypatch):
    """The whole point: one question, one block, every source named."""
    adapters = {
        "jira": FakeAdapter("jira", result="PROJ-9 Billing migration"),
        "slack": FakeAdapter("slack", result="#eng: billing cutover is friday"),
        "confluence": FakeAdapter("confluence", result="Billing runbook v3"),
    }
    wire(adapters)
    monkeypatch.setattr(cs, "_has_calls", lambda eid: True)
    monkeypatch.setattr(
        cs, "_leg_calls", lambda eid, terms: "2 indexed calls match: Billing sync"
    )

    result = cs.sweep("ent-1", "what is the state of the billing migration?")
    block = result.render()

    assert {s.key for s in result.read} == {"jira", "slack", "confluence", "calls"}
    for fragment in (
        "PROJ-9 Billing migration",
        "#eng: billing cutover is friday",
        "Billing runbook v3",
        "2 indexed calls match: Billing sync",
    ):
        assert fragment in block
    # Every source is attributed by name, which is what lets the model cite it.
    for heading in ("### Jira", "### Slack", "### Confluence", "### Recorded calls"):
        assert heading in block
    assert "searched for: " in block


def test_render_orders_local_legs_before_live(wire, monkeypatch):
    """Local legs are DB reads that already ran; they lead the block so the
    cheapest, most reliable context survives the total-length ceiling."""
    wire({"jira": FakeAdapter("jira", result="jira rows")})
    monkeypatch.setattr(cs, "_has_calls", lambda eid: True)
    monkeypatch.setattr(cs, "_leg_calls", lambda eid, terms: "call rows")

    block = cs.sweep("ent-1", "billing migration status").render()

    assert block.index("### Recorded calls") < block.index("### Jira")


# ────────────────────── a slow / failing source degrades ──────────────────────


def test_slow_source_is_abandoned_and_named(wire):
    """The central latency guarantee: one hung connector costs the budget, not
    the answer, and it is reported as unread rather than as silence."""
    slow = FakeAdapter("slack", result="never arrives", delay=5.0)
    fast = FakeAdapter("jira", result="PROJ-3 Checkout")
    wire({"slack": slow, "jira": fast})

    started = time.monotonic()
    result = cs.sweep("ent-1", "checkout redesign status", budget_s=0.4)
    elapsed = time.monotonic() - started

    assert elapsed < 3.0, "a slow source must not hold the answer open"
    assert [s.key for s in result.read] == ["jira"]
    unread = {s.key: s for s in result.unread}
    assert unread["slack"].status == cs.STATUS_TIMEOUT
    assert result.budget_exceeded is True
    assert "slack=timeout" in result.outcome_summary()
    block = result.render()
    assert "PROJ-3 Checkout" in block
    assert "Sources NOT covered by this sweep" in block
    assert "Slack: did not answer within the time budget" in block


def test_raising_source_is_named_not_swallowed(wire):
    boom = FakeAdapter("hubspot", raises=RuntimeError("401 Unauthorized"))
    ok = FakeAdapter("jira", result="PROJ-4 Pricing")
    wire({"hubspot": boom, "jira": ok})

    result = cs.sweep("ent-1", "pricing page rollout")

    assert [s.key for s in result.read] == ["jira"]
    unread = {s.key: s for s in result.unread}
    assert unread["hubspot"].status == cs.STATUS_ERROR
    assert "RuntimeError" in unread["hubspot"].detail
    assert "HubSpot (deals):" in result.render()


def test_empty_source_is_reported_as_empty_not_missing(wire):
    """A connected source with no keyword match is a DIFFERENT fact from a
    source that failed, and the model has to be able to tell them apart."""
    wire({"jira": FakeAdapter("jira", result="hit"),
          "slack": FakeAdapter("slack", result="")})

    result = cs.sweep("ent-1", "checkout redesign")

    unread = {s.key: s for s in result.unread}
    assert unread["slack"].status == cs.STATUS_EMPTY
    assert "returned nothing for these terms" in unread["slack"].unread_reason()


def test_local_leg_failure_does_not_stop_live_legs(wire, monkeypatch):
    wire({"jira": FakeAdapter("jira", result="PROJ-1")})
    monkeypatch.setattr(cs, "_has_calls", lambda eid: True)

    def _boom(eid, terms):
        raise ValueError("index unavailable")

    monkeypatch.setattr(cs, "_leg_calls", _boom)

    result = cs.sweep("ent-1", "checkout redesign")

    assert [s.key for s in result.read] == ["jira"]
    assert {s.key: s.status for s in result.unread}["calls"] == cs.STATUS_ERROR


def test_provider_discovery_failure_degrades_to_no_sweep(monkeypatch):
    """An unreachable DB must produce a plain answer, never a 500."""
    from app.connector_lookup import registry

    def _boom(eid):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(registry, "connected_providers", _boom)
    monkeypatch.setattr(cs, "_has_calls", lambda eid: False)
    monkeypatch.setattr(cs, "_has_github", lambda eid: False)

    result = cs.sweep("ent-1", "checkout redesign status")

    assert result.sources == []
    assert result.render() == ""


# ─────────────────────────── zero connectors ───────────────────────────


def test_company_with_no_connectors_sweeps_nothing(wire):
    """Nothing connected → no sources, no block, and the caller composes exactly
    the prompt it composed before this feature existed."""
    wire({}, connected=[])

    result = cs.sweep("ent-nothing", "what is the state of the billing migration?")

    assert result.sources == []
    assert result.read == []
    assert result.render() == ""
    assert result.outcome_summary() == "no-sources"


def test_no_enterprise_id_sweeps_nothing(wire):
    wire({"jira": FakeAdapter("jira")})

    assert cs.sweep("", "billing migration status").render() == ""
    assert cs.sweep(None, "billing migration status").render() == ""


def test_all_sources_empty_renders_nothing(wire):
    """A block that says only "I checked three sources and found nothing" invites
    the model to assert the absence. Absence from a keyword probe is not
    evidence of absence, so we say nothing at all."""
    wire({"jira": FakeAdapter("jira", result=""),
          "slack": FakeAdapter("slack", result="")})

    result = cs.sweep("ent-1", "checkout redesign")

    assert result.read == []
    assert len(result.unread) == 2
    assert result.render() == ""


# ─────────────────────────── auth scoping ───────────────────────────


def test_sessions_open_only_for_the_authenticated_tenant(wire):
    """Every session is opened from the enterprise_id the request authenticated
    as. The model contributes search TERMS and nothing else — it is not in the
    loop at all, so it can never name a tenant, an installation or a token."""
    adapters = {
        "jira": FakeAdapter("jira", result="a"),
        "slack": FakeAdapter("slack", result="b"),
    }
    wire(adapters)

    cs.sweep("ent-alpha", "checkout redesign status")

    for adapter in adapters.values():
        assert adapter.opened_with == ["ent-alpha"]


def test_unconnected_provider_is_never_opened(wire):
    """A provider absent from this company's connection rows is not probed at
    all — not opened, not dispatched, not mentioned."""
    jira = FakeAdapter("jira", result="a")
    hubspot = FakeAdapter("hubspot", result="secret deals")
    wire({"jira": jira, "hubspot": hubspot}, connected=["jira"])

    result = cs.sweep("ent-1", "checkout redesign status")

    assert hubspot.opened_with == []
    assert hubspot.calls == []
    assert [s.key for s in result.sources] == ["jira"]
    assert "secret deals" not in result.render()


def test_connected_but_unopenable_is_reported_as_not_searched(wire):
    """`open_session` returning None means the connection row exists but no
    session could be opened. Reporting that as "returned nothing for these
    terms" would tell the model a source was searched when it never was — the
    exact confusion this status exists to prevent."""
    wire({"jira": FakeAdapter("jira", result="a"),
          "slack": FakeAdapter("slack", connected=True, result="b"),
          "confluence": FakeAdapter("confluence", connected=False)})

    result = cs.sweep("ent-1", "checkout redesign status")

    assert {s.key for s in result.read} == {"jira", "slack"}
    unread = {s.key: s for s in result.unread}
    assert unread["confluence"].status == cs.STATUS_UNAVAILABLE
    assert "was NOT searched" in unread["confluence"].unread_reason()
    assert "returned nothing" not in unread["confluence"].unread_reason()


# ─────────────────────────── caps ───────────────────────────


def test_per_source_result_is_capped_with_an_honest_marker(wire):
    wire({"jira": FakeAdapter("jira", result="x" * (cs.PER_SOURCE_CHARS + 5_000))})

    block = cs.sweep("ent-1", "checkout redesign").render()

    assert "truncated" in block
    assert len(block) < cs.PER_SOURCE_CHARS + 1_000


def test_total_ceiling_drops_whole_sources_and_names_them(wire, monkeypatch):
    """Over the total budget, a low-priority source is DROPPED rather than cut
    mid-item — a half-rendered list of issues reads as a complete one — and the
    drop is disclosed."""
    big = "y" * cs.PER_SOURCE_CHARS
    adapters = {p: FakeAdapter(p, result=big) for p in cs.LIVE_PROVIDERS}
    wire(adapters)
    monkeypatch.setattr(cs, "TOTAL_CHARS", cs.PER_SOURCE_CHARS * 2)

    result = cs.sweep("ent-1", "checkout redesign status")

    assert len(result.read) == 2
    dropped = [s for s in result.sources if s.status == cs.STATUS_DROPPED]
    assert dropped, "over-budget sources must be dropped, not trimmed"
    assert "dropped from this prompt for length" in result.render()


def test_terms_are_capped(wire):
    wire({"jira": FakeAdapter("jira", result="a")})
    question = " ".join(f"topic{i}" for i in range(40))

    result = cs.sweep("ent-1", question)

    assert len(result.terms) == cs.MAX_TERMS


def test_max_sources_bounds_the_fan_out(wire, monkeypatch):
    adapters = {p: FakeAdapter(p, result="a") for p in cs.LIVE_PROVIDERS}
    wire(adapters)
    monkeypatch.setattr(cs, "MAX_SOURCES", 2)

    result = cs.sweep("ent-1", "checkout redesign status")

    assert len(result.sources) == 2


# ─────────────────────────── local legs ───────────────────────────


def test_calls_leg_reads_the_index_not_the_fireflies_api(monkeypatch, wire):
    """The index is the reason listing went 168s → 4s. The sweep must never
    reach for the live call API, whose latency is the thing being avoided."""
    from app import call_index

    class _Call:
        call_date, title, account, summary = "2026-08-01", "Acme QBR", "Acme", "renewal"

    wire({}, connected=[])
    monkeypatch.setattr(cs, "_has_calls", lambda eid: True)
    monkeypatch.setattr(call_index, "resolve_calls", lambda eid, q, **k: [_Call()])

    block = cs.sweep("ent-1", "what did Acme say about renewal?").render()

    assert "Acme QBR" in block
    assert "### Recorded calls" in block


def test_calls_leg_reports_index_size_on_a_keyword_miss(monkeypatch, wire):
    """"13 calls, none matching" is a materially different answer from "no
    calls", and only the first one is true."""
    from app import call_index

    wire({}, connected=[])
    monkeypatch.setattr(cs, "_has_calls", lambda eid: True)
    monkeypatch.setattr(call_index, "resolve_calls", lambda eid, q, **k: [])
    monkeypatch.setattr(call_index, "count_calls", lambda eid, **k: 13)

    block = cs.sweep("ent-1", "checkout redesign status").render()

    assert "13 recorded calls are indexed" in block
    assert "not transcripts" in block


def test_github_leg_matches_open_prs_by_title(monkeypatch, wire):
    from app import db

    wire({}, connected=[])
    monkeypatch.setattr(cs, "_has_github", lambda eid: True)
    monkeypatch.setattr(db, "list_open_pull_requests", lambda eid: [
        {"repo_full_name": "acme/app", "pr_number": 7,
         "title": "Checkout redesign step 1", "is_draft": 0},
        {"repo_full_name": "acme/app", "pr_number": 8,
         "title": "Bump deps", "is_draft": 0},
    ])

    block = cs.sweep("ent-1", "checkout redesign status").render()

    assert "acme/app#7" in block
    assert "Bump deps" not in block


def test_github_leg_is_scoped_to_the_calling_tenant(monkeypatch, wire):
    from app import db

    seen: list[str] = []
    wire({}, connected=[])
    monkeypatch.setattr(cs, "_has_github", lambda eid: True)
    monkeypatch.setattr(
        db, "list_open_pull_requests", lambda eid: (seen.append(eid), [])[1]
    )

    cs.sweep("ent-beta", "checkout redesign status")

    assert seen == ["ent-beta"]


# ─────────────────────────── the flag ───────────────────────────


def test_flag_defaults_on_and_explicit_false_opts_out():
    from app.entitlements import cross_connector_sweep_enabled

    assert cross_connector_sweep_enabled({}) is True
    assert cross_connector_sweep_enabled(None) is True
    assert cross_connector_sweep_enabled({"other": False}) is True
    assert cross_connector_sweep_enabled({"chat_cross_connector_sweep": False}) is False
    assert cross_connector_sweep_enabled({"chat_cross_connector_sweep": True}) is True


def test_global_setting_beats_the_per_company_flag(monkeypatch):
    """The operational lever has to win, or an incident needs a DB write per
    company to stop the bleeding."""
    from app import qa_agent
    from app.config import settings

    monkeypatch.setattr(settings, "chat_cross_connector_sweep", False)
    assert qa_agent._cross_connector_sweep_enabled("ent-1") is False

    monkeypatch.setattr(settings, "chat_cross_connector_sweep", True)
    monkeypatch.setattr(
        "app.entitlements.read_feature_flags", lambda cid: {}
    )
    assert qa_agent._cross_connector_sweep_enabled("ent-1") is True


def test_sweep_context_never_raises(monkeypatch):
    from app import qa_agent

    monkeypatch.setattr(qa_agent, "_cross_connector_sweep_enabled", lambda eid: True)
    monkeypatch.setattr(
        cs, "context_block",
        lambda eid, q: (_ for _ in ()).throw(RuntimeError("everything is on fire")),
    )

    assert qa_agent._sweep_context("ent-1", "billing migration status") == ""


def test_disabled_flag_means_no_sweep_context(monkeypatch):
    from app import qa_agent

    monkeypatch.setattr(qa_agent, "_cross_connector_sweep_enabled", lambda eid: False)
    called: list[str] = []
    monkeypatch.setattr(
        cs, "context_block", lambda eid, q: (called.append(eid), ("x", None))[1]
    )

    assert qa_agent._sweep_context("ent-1", "billing migration status") == ""
    assert called == []


def test_topicless_message_never_reads_the_flag(monkeypatch):
    """The term gate runs BEFORE the flag read, so a follow-up that names no
    topic costs no DB round trip at all."""
    from app import qa_agent

    def _boom(eid):
        raise AssertionError("flag must not be read for a topicless message")

    monkeypatch.setattr(qa_agent, "_cross_connector_sweep_enabled", _boom)

    assert qa_agent._sweep_context("ent-1", "make it shorter") == ""


# ─────────────────────────── prompt composition ───────────────────────────


def test_live_block_reaches_the_prompt_but_not_the_cache_prefix(
    isolated_settings, fake_llm
):
    """The sweep block is per-question retrieval. In the cacheable prefix it
    would invalidate the shared corpus cache on every single ask."""
    from app import ask_runner

    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text("legacy corpus body")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [],
        "confidence": 0.5, "unanswered": "",
    }

    ask_runner.compose_ask_answer(
        "asurion", "What changed?", enterprise_id=None,
        live_context="LIVE CROSS-SOURCE SWEEP — searched for: billing\n### Jira\nPROJ-9",
    )

    call = fake_llm["calls"][0]
    assert "PROJ-9" in call["user"]
    assert "LIVE CROSS-SOURCE SWEEP" in call["system"]
    assert "PROJ-9" not in (call["kwargs"].get("user_cacheable_prefix") or "")
    # No KG bundle here, so the KG addendum must NOT be appended: it names a
    # "LIVE CONTEXT FROM CONNECTED SOURCES" heading only render_context_section
    # emits, and pointing the model at an absent section is how a prompt starts
    # inventing what should have been in it.
    assert "LIVE CONTEXT FROM CONNECTED SOURCES" not in call["system"]


def test_sweep_addendum_defers_to_an_unresolved_reference(isolated_settings, fake_llm):
    """Both blocks land in one prompt on the direct path: the document grounding
    can report a reference it could not resolve and ask the user which document
    they meant, while this section tells the model to answer from live data.

    The sweep must lose that contest. Having swept material that looks close
    enough is not permission to pick a document for the user — a confident
    answer about the wrong document, built from real data, is worse than one
    short clarifying question.
    """
    from app import ask_runner
    from app.prompts import ASK_SYSTEM_LIVE_SWEEP_ADDENDUM

    # Anchored on #1059's LITERAL heading, not a paraphrase: the model has to
    # match a string rather than infer which of several sections we meant. If
    # that heading is ever reworded, this assertion is what fails loudly instead
    # of the precedence quietly ceasing to bind.
    assert "PRECEDENCE" in ASK_SYSTEM_LIVE_SWEEP_ADDENDUM
    assert (
        'headed "The document this message refers to is UNRESOLVED"'
        in ASK_SYSTEM_LIVE_SWEEP_ADDENDUM
    )
    assert "WINS over this section" in ASK_SYSTEM_LIVE_SWEEP_ADDENDUM

    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text("legacy corpus body")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [],
        "confidence": 0.5, "unanswered": "",
    }

    ask_runner.compose_ask_answer(
        "asurion", "what does the spec say?", enterprise_id=None,
        live_context="LIVE CROSS-SOURCE SWEEP — ...\n### Confluence\nTwo specs",
    )

    system = fake_llm["calls"][0]["system"]
    assert "The document this message refers to is UNRESOLVED" in system
    assert "never to skip it" in system


def test_no_live_block_leaves_the_prompt_untouched(isolated_settings, fake_llm):
    """Additive: a company with nothing swept gets byte-identical composition to
    before this feature."""
    from app import ask_runner

    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text("legacy corpus body")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [],
        "confidence": 0.5, "unanswered": "",
    }

    ask_runner.compose_ask_answer("asurion", "What changed?", enterprise_id=None)

    call = fake_llm["calls"][0]
    assert "LIVE CROSS-SOURCE SWEEP" not in call["system"]
    assert "LIVE CONTEXT FROM CONNECTED SOURCES" not in call["user"]
