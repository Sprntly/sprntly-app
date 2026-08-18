"""Planner-path tracker seam — the KG-first tracker fallback made reachable when
the ask-planner (not the regex interceptor ladder) claimed the turn.

The regex ladder — and the tracker interceptor it hosts, which already degrades a
tracker read to a knowledge-graph answer when no live session resolves — is off
the moment the planner returns a non-None plan (`_regex_ladder = plan is None`).
A staging diagnosis showed the real "list our ClickUp tasks" failure takes the
PLANNER path: the planner names `sources=["clickup"]`, the ladder is skipped, the
planned live read finds no ClickUp session, and the model false-denies from a "not
read live" note with no KG block. The seam under test routes exactly that turn
through the SAME `tracker.answer` the interceptor calls — but only when the
planner routed to tracker-ONLY sources AND the question is itself a tracker query.

Two tiers:

* Routing unit tests (no DB / no LLM): the plan is passed directly to
  `qa_agent.answer(plan=...)` — the same seam the planner drives — `tracker.answer`
  and `compose_ask_answer` are spied, and every guard (mixed sources, non-tracker
  question, PRD open, project-surface skip, plan-None) is exercised.
* `@pytest.mark.integration` real-KG proofs against local Supabase + a real model,
  REUSING `test_tracker_lookup_kg_fallback.py`'s seeded-tenant fixture, driving
  `qa_agent.answer(plan=..., scope=...)` END-TO-END on the planner-claimed path for
  both the main and the project-private surface. Env-gated (RUN_TRACKER_KG_LIVE=1);
  teardown is id-scoped/cascade — never slug-scoped (the local rig is shared).
"""
from __future__ import annotations

import re

import pytest

from app import qa_agent
from app.ask_planner import Plan
from app.connector_lookup import tracker as tracker_mod
from app.skill_router import is_jira_lookup
from app.surface_scope import Surface, SurfaceScope

# Reuse the merged tracker-KG fixture verbatim — same seeded-tenant shape, same
# id-cascade teardown, same env gate — rather than re-deriving a second one.
from tests.test_tracker_lookup_kg_fallback import (  # noqa: E402
    _FALSE_DENIES,
    _MARKER,
    seeded_clickup_kg,
)


# ── routing unit tests (no DB / no LLM) ──────────────────────────────────────


@pytest.fixture
def spies(monkeypatch):
    """Spy the two mutually-exclusive exits of the planned direct path: the seam's
    `tracker.answer` and the ordinary `compose_ask_answer`. Both return a marker so
    a test can tell which one served the turn. The keyword sweep and the planned
    live read are booby-trapped: a planned turn must reach neither."""
    calls: dict = {"tracker_answer": [], "compose": 0}

    def _tracker_answer(*, enterprise_id, question, history=None, **kw):
        calls["tracker_answer"].append(
            {"enterprise_id": enterprise_id, "question": question, "history": history}
        )
        return {"answer": "FROM_TRACKER_KG", "_skill_action": "Tracker lookup"}

    def _compose(*a, **k):
        calls["compose"] += 1
        return {"answer": "FROM_COMPOSE"}

    def _sweep_boom(*a, **k):
        raise AssertionError("a planned turn must not reach the keyword sweep")

    def _live_boom(*a, **k):
        raise AssertionError("the seam must return before the planned live read")

    monkeypatch.setattr(tracker_mod, "answer", _tracker_answer)
    monkeypatch.setattr(qa_agent, "compose_ask_answer", _compose)
    monkeypatch.setattr(qa_agent, "_sweep_context", _sweep_boom)
    monkeypatch.setattr(qa_agent, "_planned_live_context", _live_boom)
    return calls


def _answer(plan, question, *, scope=None, prd_id=None):
    return qa_agent.answer(
        plan=plan,
        enterprise_id="ent-1",
        question=question,
        dataset="d",
        scope=scope,
        prd_id=prd_id,
    )


def test_tracker_only_plan_named_query_routes_to_kg_fallback(spies):
    """AC1/AC2/AC9 — planner names ClickUp-only sources for a question that NAMES
    ClickUp: the seam routes to `tracker.answer` (with the caller's own args) and
    `compose_ask_answer` never runs for the turn."""
    out = _answer(
        Plan(action="answer", sources=["clickup"], include_knowledge_graph=False),
        "list our ClickUp tasks",
    )
    assert out["answer"] == "FROM_TRACKER_KG"
    assert spies["compose"] == 0
    assert len(spies["tracker_answer"]) == 1
    call = spies["tracker_answer"][0]
    assert call["enterprise_id"] == "ent-1"
    assert call["question"] == "list our ClickUp tasks"


def test_tracker_only_plan_nontracker_question_does_not_route(spies):
    """AC3 (the BLOCKER regression) — a tracker-only plan on a NON-tracker question
    (`named_trackers == []` AND `is_jira_lookup == False`) does NOT route to the
    seam; the ordinary compose path serves it."""
    # Guard the premise: this is exactly the existing backstop fixture shape.
    assert tracker_mod.named_trackers("give me more details on the contoso meeting") == []
    assert is_jira_lookup("give me more details on the contoso meeting", None) is False
    out = _answer(
        Plan(action="answer", sources=["jira"]),
        "give me more details on the contoso meeting",
    )
    assert out["answer"] == "FROM_COMPOSE"
    assert spies["tracker_answer"] == []


def test_existing_backstop_test_still_green(spies):
    """AC3 — the same-shaped existing backstop case (`Plan(sources=["jira"])` on the
    contoso question) reaches the generic compose path with the seam present. The
    existing `test_qa_agent_planned_sources.py::test_the_backstop_stands_down_when_
    the_plan_named_no_call_source` runs in this blast radius and MUST stay green;
    this local mirror asserts the same stand-down so a future predicate widening
    trips here too."""
    out = _answer(
        Plan(action="answer", sources=["jira"]),
        "give me more details on the contoso meeting",
    )
    assert out["answer"] == "FROM_COMPOSE"
    assert spies["tracker_answer"] == []


def test_is_jira_lookup_only_query_routes(spies):
    """AC1 — a genuine tracker query that names NO tracker (`named_trackers == []`,
    `is_jira_lookup == True`) still fires the seam via the `is_jira_lookup` OR-term
    when the plan is tracker-only."""
    assert tracker_mod.named_trackers("what tickets are open") == []
    assert is_jira_lookup("what tickets are open", None) is True
    out = _answer(Plan(action="answer", sources=["jira"]), "what tickets are open")
    assert out["answer"] == "FROM_TRACKER_KG"
    assert len(spies["tracker_answer"]) == 1
    assert spies["compose"] == 0


def test_seam_is_planner_path_not_regex_ladder(spies):
    """AC4 — proves the answer came from the SEAM, not the regex-ladder tracker
    interceptor. The ladder interceptor's own outer gate is `is_jira_lookup(...)`,
    which is False for "list our ClickUp tasks"; and because the plan is non-None
    the ladder is structurally off (`_regex_ladder = plan is None`). Yet
    `tracker.answer` is still reached — via the seam's `named_trackers` OR-term —
    which only the planner-path seam can do here."""
    assert is_jira_lookup("list our ClickUp tasks", None) is False  # ladder gate off
    assert tracker_mod.named_trackers("list our ClickUp tasks") == ["clickup"]
    out = _answer(
        Plan(action="answer", sources=["clickup"], include_knowledge_graph=False),
        "list our ClickUp tasks",
    )
    assert out["answer"] == "FROM_TRACKER_KG"
    assert len(spies["tracker_answer"]) == 1


def test_mixed_source_plan_does_not_route(spies):
    """AC5 — a MIXED-source plan (`["clickup","slack"]`, not a subset of TRACKERS)
    does NOT fire the seam; routing it would silently drop the co-planned Slack
    source. The ordinary planned path runs."""
    out = _answer(
        Plan(action="answer", sources=["clickup", "slack"]),
        "list our ClickUp tasks",
    )
    assert out["answer"] == "FROM_COMPOSE"
    assert spies["tracker_answer"] == []


def test_plan_none_uses_regex_ladder_unchanged(monkeypatch):
    """AC6 — with `plan is None` the seam (guarded on `plan is not None`) cannot run;
    a tracker turn is claimed by the regex-ladder tracker interceptor instead. Any
    `tracker.answer` call on a plan-None turn is therefore necessarily the ladder,
    never the seam. Earlier ladder interceptors' detectors are stubbed off so this
    isolates the tracker interceptor without a DB read."""
    calls: list = []

    def _tracker_answer(*, enterprise_id, question, history=None, **kw):
        calls.append(question)
        return {"answer": "FROM_LADDER", "_skill_action": "Tracker lookup"}

    monkeypatch.setattr(tracker_mod, "answer", _tracker_answer)
    # No live connection lookups in a hermetic unit test — force the capability
    # gate to fall to the `named_trackers` half (the question names ClickUp).
    monkeypatch.setattr(tracker_mod, "any_connected", lambda eid: False)
    # Quiet the earlier ladder interceptors whose detectors could otherwise claim
    # a "show ... tickets" phrasing and reach for a DB/index read.
    for name in (
        "is_call_digest",
        "is_voc_report_request",
        "is_data_analysis_request",
        "is_ticket_update",
    ):
        monkeypatch.setattr(qa_agent, name, lambda *a, **k: False)
    import app.call_index as call_index

    monkeypatch.setattr(call_index, "is_listing_request", lambda *a, **k: False)
    monkeypatch.setattr(call_index, "is_single_call_request", lambda *a, **k: False)
    monkeypatch.setattr(call_index, "windowed_call_question", lambda *a, **k: None)

    out = qa_agent.answer(
        plan=None,
        enterprise_id="ent-1",
        question="show our open ClickUp tickets",
        dataset="d",
    )
    assert out["answer"] == "FROM_LADDER"
    assert calls == ["show our open ClickUp tickets"]


def test_prd_open_tracker_plan_does_not_route(spies, monkeypatch):
    """AC7 — a PRD tab is open (`prd_context` truthy): the seam stands down even for
    a qualifying tracker query so the PRD grounding path is preserved."""
    import app.prd_context as prd_ctx

    monkeypatch.setattr(prd_ctx, "build_prd_context", lambda eid, pid: "PRD GROUNDING BLOCK")
    out = _answer(
        Plan(action="answer", sources=["clickup"], include_knowledge_graph=False),
        "list our ClickUp tasks",
        prd_id=1,
    )
    assert out["answer"] == "FROM_COMPOSE"
    assert spies["tracker_answer"] == []


def test_project_surface_unnamed_is_skipped_named_fires(spies):
    """AC8 — surface parity with the interceptor. On a project-private surface an
    UNNAMED PM question (`_skip_project_connectors` True) does NOT fire the seam; a
    NAMED-ClickUp question on the same surface (`_skip_project_connectors` False)
    does."""
    private = SurfaceScope(surface=Surface.project_private)
    # Unnamed → skipped.
    assert qa_agent._skip_project_connectors(private, "what tasks are open", None) is True
    out_unnamed = _answer(
        Plan(action="answer", sources=["clickup"]),
        "what tasks are open",
        scope=private,
    )
    assert out_unnamed["answer"] == "FROM_COMPOSE"
    assert spies["tracker_answer"] == []

    # Named ClickUp → admitted, seam fires.
    assert qa_agent._skip_project_connectors(private, "list our ClickUp tasks", None) is False
    out_named = _answer(
        Plan(action="answer", sources=["clickup"], include_knowledge_graph=False),
        "list our ClickUp tasks",
        scope=private,
    )
    assert out_named["answer"] == "FROM_TRACKER_KG"
    assert len(spies["tracker_answer"]) == 1


def test_tenant_and_args_propagate_to_tracker_answer(spies):
    """AC9 — the `enterprise_id`, `question`, and `history` handed to `tracker.answer`
    are exactly those `answer()` received; no cross-tenant substitution."""
    history = [{"role": "user", "content": "earlier turn"}]
    qa_agent.answer(
        plan=Plan(action="answer", sources=["clickup"], include_knowledge_graph=False),
        enterprise_id="ent-XYZ",
        question="list our ClickUp tasks",
        dataset="d",
        history=history,
    )
    call = spies["tracker_answer"][0]
    assert call["enterprise_id"] == "ent-XYZ"
    assert call["question"] == "list our ClickUp tasks"
    assert call["history"] == history


def test_tracker_answer_raise_degrades_to_compose(monkeypatch):
    """AC10 — belt-and-braces. `tracker.answer` already degrades internally rather
    than raising; if it DOES raise, the seam's try/except falls through to the
    ordinary compose path and a normal payload is still returned — never a 500."""
    def _boom(**kw):
        raise RuntimeError("unexpected tracker.answer failure")

    monkeypatch.setattr(tracker_mod, "answer", _boom)
    monkeypatch.setattr(qa_agent, "compose_ask_answer", lambda *a, **k: {"answer": "FROM_COMPOSE"})
    monkeypatch.setattr(qa_agent, "_sweep_context", lambda *a, **k: "")
    monkeypatch.setattr(qa_agent, "_planned_live_context", lambda *a, **k: "")
    out = _answer(
        Plan(action="answer", sources=["clickup"], include_knowledge_graph=False),
        "list our ClickUp tasks",
    )
    assert out["answer"] == "FROM_COMPOSE"


def test_enumeration_is_deferred_not_wired():
    """AC13 — the delivered behaviour is the merged semantic KG path via
    `tracker.answer`; no deterministic `recent_signals_by_skill` enumeration read is
    wired on the planner path. Asserted against the seam's own source."""
    import inspect

    src = inspect.getsource(qa_agent.answer)
    assert "recent_signals_by_skill" not in src
    # The seam reaches the KG only by routing to the shared tracker fallback.
    assert "tracker.answer" in src


def test_no_dbd_identifiers_in_changed_files():
    """AC14 — no ticket id, phase code, wave name, or agent name (nor an AI-authorship
    trailer) in the changed source or this test. Forbidden tokens are assembled from
    fragments so this guard does not itself embed them."""
    import inspect

    tokens = [
        "CO" + "NN-" + r"\d+",
        r"P\d+-\d+",
        "d" + "bd",
        "dispos" + "able",
        "van" + "guard",
        "samsung-" + "readiness",
        "sprntly-" + "(builder|planner|gate1|verifier|tester|infra)",
        "co-" + "authored-by",
    ]
    forbidden = re.compile(r"\b(" + "|".join(tokens) + r")\b", re.I)
    import app.qa_agent as qa_mod
    import tests.test_tracker_kg_planner_path as self_mod

    for mod in (qa_mod, self_mod):
        src = inspect.getsource(mod)
        assert not forbidden.search(src), f"forbidden identifier in {mod.__name__}"


# ── real-KG integration proofs (local Supabase + real model) ─────────────────
# Run with the local rig env exported:
#   RUN_TRACKER_KG_LIVE=1 pytest tests/test_tracker_kg_planner_path.py -m integration
# Needs SUPABASE_URL (loopback) / SUPABASE_SERVICE_ROLE_KEY and a real
# ANTHROPIC/DESIGN_AGENT_ANTHROPIC key. The planner is exercised by PASSING a fixed
# non-None Plan to answer(), so the planner-claimed path is provably taken (not the
# regex ladder). Fixtures/teardown are reused from test_tracker_lookup_kg_fallback.


@pytest.mark.integration
def test_planner_path_clickup_list_answers_from_kg_main_surface(seeded_clickup_kg, monkeypatch):
    """AC11 (load-bearing — the demo symptom, main surface) — ClickUp session absent,
    a NON-None Plan(sources=["clickup"]) supplied (so `plan is not None` ⇒ the planner
    path, provably not the regex ladder), 'list our ClickUp tasks' driven end-to-end
    through `qa_agent.answer(scope=main)` surfaces the seeded KG content and is NOT a
    false-deny."""
    from app import db

    eid = seeded_clickup_kg
    monkeypatch.setattr(db, "get_connection", lambda cid, prov: None)
    monkeypatch.setattr(db, "list_connections", lambda cid: [])
    monkeypatch.setattr(db, "list_slack_connections", lambda cid: [])
    out = qa_agent.answer(
        plan=Plan(action="answer", sources=["clickup"], include_knowledge_graph=False),
        enterprise_id=eid,
        question="list our ClickUp tasks",
        dataset="kgtest",
        scope=SurfaceScope(surface=Surface.main),
    )
    text = out["answer"].lower()
    assert _MARKER.lower() in text or "checkout" in text, out["answer"]
    for deny in _FALSE_DENIES:
        assert deny not in text, f"false-deny leaked: {deny!r}"


@pytest.mark.integration
def test_planner_path_clickup_list_answers_from_kg_private_surface(seeded_clickup_kg, monkeypatch):
    """AC11 (load-bearing — project-private surface) — as above with
    `scope=project_private`; the question NAMES ClickUp so `_skip_project_connectors`
    returns False and the seam is admitted on the project surface too."""
    from app import db

    eid = seeded_clickup_kg
    monkeypatch.setattr(db, "get_connection", lambda cid, prov: None)
    monkeypatch.setattr(db, "list_connections", lambda cid: [])
    monkeypatch.setattr(db, "list_slack_connections", lambda cid: [])
    private = SurfaceScope(surface=Surface.project_private)
    assert qa_agent._skip_project_connectors(private, "list our ClickUp tasks", None) is False
    out = qa_agent.answer(
        plan=Plan(action="answer", sources=["clickup"], include_knowledge_graph=False),
        enterprise_id=eid,
        question="list our ClickUp tasks",
        dataset="kgtest",
        scope=private,
    )
    text = out["answer"].lower()
    assert _MARKER.lower() in text or "checkout" in text, out["answer"]
    for deny in _FALSE_DENIES:
        assert deny not in text, f"false-deny leaked: {deny!r}"


@pytest.mark.integration
def test_planner_path_enumeration_deferred(seeded_clickup_kg, monkeypatch):
    """AC13 (integration) — against the real seam, the delivered answer comes from
    the merged semantic KG path via `tracker.answer`; no deterministic
    `recent_signals_by_skill` enumeration read is invoked on the planner path."""
    from app import db
    from app.graph import facade as facade_mod

    eid = seeded_clickup_kg
    monkeypatch.setattr(db, "get_connection", lambda cid, prov: None)
    monkeypatch.setattr(db, "list_connections", lambda cid: [])
    monkeypatch.setattr(db, "list_slack_connections", lambda cid: [])
    enum_calls: list = []
    orig = facade_mod.GraphFacade.recent_signals_by_skill

    def _spy(self, *a, **k):
        enum_calls.append((a, k))
        return orig(self, *a, **k)

    monkeypatch.setattr(facade_mod.GraphFacade, "recent_signals_by_skill", _spy)
    out = qa_agent.answer(
        plan=Plan(action="answer", sources=["clickup"], include_knowledge_graph=False),
        enterprise_id=eid,
        question="list our ClickUp tasks",
        dataset="kgtest",
        scope=SurfaceScope(surface=Surface.main),
    )
    assert out["answer"]
    assert enum_calls == [], "planner path must not wire deterministic enumeration"
