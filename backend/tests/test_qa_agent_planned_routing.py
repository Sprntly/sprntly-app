"""Routing when a PLAN decided the turn — the regex ladder's replacement.

`test_connector_lookup_routing.py` pins the ladder's precedence: which regex
claims which phrasing, and in what order. Every one of those assertions still
holds, because they run with `plan=None` and that path is untouched.

This file pins the OTHER contract, the one that applies once a plan exists:

  * the ladder is OFF — no regex gets to claim the turn, whatever the words are
  * the planner's `method` reaches the SAME executor the interception used
  * capability preconditions survive, because they are "can this engine serve
    this company at all", not "does this phrasing look like ours"
  * a declined engine falls through to a normal answer, never a canned refusal
  * a user's Stop still escapes

The point of the change, stated once: the ladder's ordering was load-bearing
because its rules COMPETE. "summarize the slack channel syncs from this week"
named Slack and was answered from Fireflies transcripts, because the digest's
regex saw a verb plus `syncs?`. Reordering never fixed that class of bug; a
model reading the whole sentence does not have it.
"""
from __future__ import annotations

import pytest

import app.qa_agent as qa
from app.ask_planner import Plan


@pytest.fixture
def loud_ladder(monkeypatch):
    """Make every regex decider raise. If a planned turn reaches ANY of them the
    test fails loudly, rather than passing on ladder output that happens to
    match."""
    import app.skill_router as sr
    from app import call_index

    def _boom(name):
        def _f(*a, **k):
            raise AssertionError(f"planned turn reached the regex decider {name!r}")
        return _f

    for mod, fn in (
        (call_index, "is_listing_request"),
        (call_index, "is_single_call_request"),
        (sr, "is_data_analysis_request"),
        (sr, "is_jira_lookup"),
        (sr, "is_ticket_update"),
        (sr, "is_voc_report_request"),
    ):
        if hasattr(mod, fn):
            monkeypatch.setattr(mod, fn, _boom(fn))
    # `qa` imported several of these into its own namespace at import time.
    for fn in (
        "is_data_analysis_request", "is_jira_lookup", "is_ticket_update",
        "is_voc_report_request", "is_call_digest",
    ):
        if hasattr(qa, fn):
            monkeypatch.setattr(qa, fn, _boom(fn))


def _plan(method=None, **kw):
    return Plan(action="answer", pipeline_id=method, **kw)


# ── the vocabulary and the executors cannot drift apart ──────────────────────


def test_every_machinery_id_the_planner_can_name_has_an_executor():
    """A planner naming an engine `answer()` cannot run would silently fall
    through to a generic answer, which looks like the planner being wrong when
    it was actually right."""
    from app.ask_planner import _MACHINERY_IDS

    assert set(qa._PLANNED_MACHINERY) == set(_MACHINERY_IDS)


# ── the planner's choice reaches the executor ────────────────────────────────


def test_call_listing_runs_the_index(loud_ladder, monkeypatch):
    from app import call_index

    monkeypatch.setattr(
        call_index, "answer_listing",
        lambda eid, q, fresh=None: {"answer": "3 calls", "_skill_source": "call-index"},
    )
    monkeypatch.setattr(call_index, "ensure_fresh", lambda eid: True)

    out = qa.answer(
        enterprise_id="ent", question="anything at all", dataset="acme",
        plan=_plan("call-listing"),
    )
    assert out["_skill_source"] == "call-index"


def test_ticket_update_runs_its_executor(loud_ladder, monkeypatch):
    import app.ticket_update as tu

    seen: dict = {}
    monkeypatch.setattr(
        tu, "answer",
        lambda **k: seen.update(k) or {"answer": "rewritten", "_skill_source": "ticket-update"},
    )

    out = qa.answer(
        enterprise_id="ent", question="anything at all", dataset="acme",
        prd_id=42, plan=_plan("ticket-update"),
    )
    assert out["_skill_source"] == "ticket-update"
    # The PRD target is threaded through — a ticket rewritten from a PRD needs it.
    assert seen["prd_id"] == 42


def test_a_question_the_regex_would_have_hijacked_goes_where_the_plan_says(
    loud_ladder, monkeypatch
):
    """The reported failure, inverted. This exact phrasing matched the digest's
    verb-plus-`syncs?` rule and was answered from Fireflies transcripts even
    though it names Slack. Planned, it reads Slack."""
    import app.live_read as live_read

    class _R:
        read: list = []
        def outcome_summary(self): return "slack=ok"
        def render_block(self): return "### Slack\n- #eng: shipped friday"

    seen: dict = {}
    monkeypatch.setattr(
        live_read, "read_sources",
        lambda eid, providers, **k: seen.update(providers=providers) or _R(),
    )
    monkeypatch.setattr(qa, "_persist_live_records", lambda *a, **k: None)

    block = qa._planned_live_context(
        "ent", _plan(None, sources=["slack"]),
        "summarize the slack channel syncs from this week",
    )
    assert seen["providers"] == ["slack"]
    assert "Slack" in block


# ── capability preconditions survive ─────────────────────────────────────────


def test_call_digest_declines_when_no_call_source_is_connected(
    loud_ladder, monkeypatch
):
    """The expensive one — ~168s and ~$0.23. A plan that reads a question as a
    digest for a company that records no calls must cost nothing."""
    import app.call_digest as cd

    monkeypatch.setattr(cd, "has_call_source", lambda eid: False)
    monkeypatch.setattr(
        cd, "answer",
        lambda **k: pytest.fail("the digest ran without a call source"),
    )

    assert qa._dispatch_planned_method(
        _plan("call-digest"), enterprise_id="ent", question="q", history=None,
        prd_id=None, dataset="acme", fresh=lambda: True, is_cancelled=None,
    ) is None


def test_call_digest_runs_when_a_call_source_is_connected(loud_ladder, monkeypatch):
    import app.call_digest as cd

    monkeypatch.setattr(cd, "has_call_source", lambda eid: True)
    monkeypatch.setattr(
        cd, "answer",
        lambda **k: {"answer": "digest", "_skill_source": "call-digest"},
    )

    out = qa._dispatch_planned_method(
        _plan("call-digest"), enterprise_id="ent", question="q", history=None,
        prd_id=None, dataset="acme", fresh=lambda: True, is_cancelled=None,
    )
    assert out["_skill_source"] == "call-digest"


def test_the_digest_is_handed_the_plans_window(loud_ladder, monkeypatch):
    """The 2026-08-16 failure: the planner extracted a five-week window
    (`since: 2026-07-12`) and the dispatcher dropped it, so the digest
    re-derived one from the raw text — where a digits-only regex could not read
    "the last five weeks" and fell to its 7-day default. Four days of calls
    answered a five-week question, and the report called the rest uncaptured."""
    import app.call_digest as cd

    seen: dict = {}
    monkeypatch.setattr(cd, "has_call_source", lambda eid: True)

    def _answer(**kw):
        seen["constraints"] = kw.get("constraints")
        return {"answer": "digest", "_skill_source": "call-digest"}

    monkeypatch.setattr(cd, "answer", _answer)

    qa._dispatch_planned_method(
        _plan("call-digest", constraints={"since": "2026-07-12", "until": "2026-08-16"}),
        enterprise_id="ent", question="table week by week for the last five weeks",
        history=None, prd_id=None, dataset="acme", fresh=lambda: True,
        is_cancelled=None,
    )

    assert seen["constraints"] == {"since": "2026-07-12", "until": "2026-08-16"}


def test_the_digest_receives_on_phase(loud_ladder, monkeypatch):
    """`_m_call_digest` used to swallow `on_phase` in `**_kw` — the digest's own
    GATHERING/WRITING/ANALYZING narration never reached a planned turn, which
    is the reported bug: a dead spinner on a genuinely long query-shaped ask."""
    import app.call_digest as cd

    monkeypatch.setattr(cd, "has_call_source", lambda eid: True)
    seen: dict = {}

    def _answer(**kw):
        seen["on_phase"] = kw.get("on_phase")
        return {"answer": "digest", "_skill_source": "call-digest"}

    monkeypatch.setattr(cd, "answer", _answer)

    sink = lambda label: None  # noqa: E731 — identity-compared below
    out = qa._dispatch_planned_method(
        _plan("call-digest"), enterprise_id="ent", question="q", history=None,
        prd_id=None, dataset="acme", fresh=lambda: True, is_cancelled=None,
        on_phase=sink,
    )
    assert out["_skill_source"] == "call-digest"
    assert seen["on_phase"] is sink


def test_the_digest_still_runs_with_on_phase_unset(loud_ladder, monkeypatch):
    """The advisory contract: a caller that omits `on_phase` (tests, scheduled
    callers) must behave exactly as before — `None` reaches the executor and
    is a no-op there."""
    import app.call_digest as cd

    monkeypatch.setattr(cd, "has_call_source", lambda eid: True)
    seen: dict = {}

    def _answer(**kw):
        seen["on_phase"] = kw.get("on_phase")
        return {"answer": "digest", "_skill_source": "call-digest"}

    monkeypatch.setattr(cd, "answer", _answer)

    out = qa._dispatch_planned_method(
        _plan("call-digest"), enterprise_id="ent", question="q", history=None,
        prd_id=None, dataset="acme", fresh=lambda: True, is_cancelled=None,
    )
    assert out["_skill_source"] == "call-digest"
    assert seen["on_phase"] is None


def test_a_planned_turn_threads_on_phase_from_the_top_level_answer(
    loud_ladder, monkeypatch
):
    """The call site inside `answer()` that invokes `_dispatch_planned_method`
    must actually have `on_phase` in scope and pass it — this exercises the
    real entry point a chat turn uses, not just the dispatcher directly."""
    import app.call_digest as cd

    monkeypatch.setattr(cd, "has_call_source", lambda eid: True)
    seen: dict = {}

    def _answer(**kw):
        seen["on_phase"] = kw.get("on_phase")
        return {"answer": "digest", "_skill_source": "call-digest"}

    monkeypatch.setattr(cd, "answer", _answer)

    phases: list[str] = []
    sink = phases.append  # bound once — `phases.append is phases.append` is False
    out = qa.answer(
        enterprise_id="ent", question="anything at all", dataset="acme",
        plan=_plan("call-digest"), on_phase=sink,
    )
    assert out["_skill_source"] == "call-digest"
    assert seen["on_phase"] is sink


def test_tracker_lookup_declines_when_no_tracker_is_connected(
    loud_ladder, monkeypatch
):
    """Answering "connect Jira" to someone who never asked about a tracker is
    the #1034 failure. A declined precondition falls through instead."""
    from app.connector_lookup import tracker

    monkeypatch.setattr(tracker, "any_connected", lambda eid: False)
    monkeypatch.setattr(
        tracker, "answer", lambda **k: pytest.fail("tracker ran unconnected")
    )

    assert qa._dispatch_planned_method(
        _plan("tracker-lookup"), enterprise_id="ent", question="q", history=None,
        prd_id=None, dataset="acme", fresh=lambda: True, is_cancelled=None,
    ) is None


def test_data_analysis_declines_with_no_tabular_data(loud_ladder, monkeypatch, tmp_path):
    """A cheap local check, never `_stage_workspace` — parsing every upload on
    every merely-matching question is what this precondition avoids."""
    from app import datasets

    empty = tmp_path / "raw"
    empty.mkdir()
    monkeypatch.setattr(datasets, "raw_path", lambda ds: empty)

    assert qa._dispatch_planned_method(
        _plan("data-analysis"), enterprise_id="ent", question="q", history=None,
        prd_id=None, dataset="acme", fresh=lambda: True, is_cancelled=None,
    ) is None


# ── degradation ──────────────────────────────────────────────────────────────


def test_an_engine_that_raises_falls_through_rather_than_breaking_chat(
    loud_ladder, monkeypatch
):
    import app.call_digest as cd

    monkeypatch.setattr(cd, "has_call_source", lambda eid: True)
    monkeypatch.setattr(
        cd, "answer",
        lambda **k: (_ for _ in ()).throw(RuntimeError("engine on fire")),
    )

    assert qa._dispatch_planned_method(
        _plan("call-digest"), enterprise_id="ent", question="q", history=None,
        prd_id=None, dataset="acme", fresh=lambda: True, is_cancelled=None,
    ) is None


def test_a_user_stop_is_not_an_engine_declining(loud_ladder, monkeypatch):
    """`AskCancelled` must escape the dispatcher's blanket except, or a Stop
    would silently become a full-price generic answer."""
    import app.call_digest as cd
    from app.qa_agent import AskCancelled

    monkeypatch.setattr(cd, "has_call_source", lambda eid: True)
    monkeypatch.setattr(
        cd, "answer", lambda **k: (_ for _ in ()).throw(AskCancelled())
    )

    with pytest.raises(AskCancelled):
        qa._dispatch_planned_method(
            _plan("call-digest"), enterprise_id="ent", question="q", history=None,
            prd_id=None, dataset="acme", fresh=lambda: True, is_cancelled=None,
        )


def test_a_plan_naming_no_machinery_carries_on(loud_ladder):
    """The normal outcome. Most questions are not machinery, and returning None
    means "continue to the routed/generic path", not "nothing happened"."""
    for method in (None, "", "voice-of-customer-report", "some-company-skill"):
        assert qa._dispatch_planned_method(
            _plan(method), enterprise_id="ent", question="q", history=None,
            prd_id=None, dataset="acme", fresh=lambda: True, is_cancelled=None,
        ) is None


# ── the ladder is genuinely off ──────────────────────────────────────────────


def test_the_ladder_still_runs_without_a_plan(monkeypatch):
    """The other half of the contract, and what makes this safe to land: every
    caller that has not been migrated keeps the ladder, byte for byte. This is
    what `test_connector_lookup_routing.py`'s ~20 precedence assertions exercise
    and why they all still pass."""
    import app.call_digest as cd

    monkeypatch.setattr(cd, "has_call_source", lambda eid: True)
    monkeypatch.setattr(
        cd, "answer",
        lambda **k: {"answer": "digest", "_skill_source": "call-digest"},
    )

    out = qa.answer(
        enterprise_id="ent",
        question="summarize the customer calls from last week",
        dataset="acme",
    )
    assert out["_skill_source"] == "call-digest"
