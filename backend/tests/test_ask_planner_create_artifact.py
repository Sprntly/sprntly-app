"""Gates for the planner's `create_artifact` action.

The action itself is chosen by a model, so what CAN be pinned in CI is the
deterministic half: that the action is reachable, that its argument survives,
that a missing argument degrades safely, and that no OTHER action can carry a
stray artifact kind. The prompt rules that decide "asked for a document" vs
"asked about one" need a live model and are exercised by the labelled evals,
not here — the same split every other planner action uses.
"""
from __future__ import annotations

from app.ask_planner import ACTION_ANSWER, _ACTIONS, apply_gates


def _gate(payload: dict):
    return apply_gates(payload, enterprise_id="co-test", connected=[])


def test_the_action_exists():
    """An action the planner cannot name is an action nothing can choose."""
    assert "create_artifact" in _ACTIONS


def test_a_well_formed_create_survives_with_its_kind():
    plan = _gate({
        "action": "create_artifact",
        "task": "Summarize the Q3 reliability work for leadership",
        "artifact_kind": "leadership update",
        "action_confidence": 0.9,
    })
    assert plan.action == "create_artifact"
    assert plan.artifact_kind == "leadership update"
    assert "Q3 reliability" in plan.task


def test_a_create_with_no_task_degrades_to_answer():
    """An action whose ARGUMENT is missing is worse than no action: a document
    generated from an empty brief is a blank page with a title on it.

    Unlike `generate_prd` — which has a chat surface that asks "what should it
    cover?" and waits — there is no such prompt for an arbitrary document kind,
    so `answer` is the recoverable landing.
    """
    plan = _gate({"action": "create_artifact", "artifact_kind": "memo"})
    assert plan.action == ACTION_ANSWER
    assert plan.artifact_kind is None


def test_a_create_with_no_kind_still_builds():
    """The mirror case, and it goes the OTHER way on purpose. The user asked
    for a document and the task says what it is about; the generator titles it
    from its own <h1>. Refusing here would answer with prose someone who asked
    for a document."""
    plan = _gate({"action": "create_artifact", "task": "write up the outage"})
    assert plan.action == "create_artifact"
    assert plan.artifact_kind is None


def test_a_kind_on_any_other_action_is_dropped():
    """Arguments belong to their action. A stray kind on an `answer` is noise a
    downstream reader would otherwise try to honour."""
    for action, extra in (
        (ACTION_ANSWER, {}),
        ("generate_prd", {"task": "a PRD about billing"}),
        ("generate_tickets", {"task": "break this down"}),
    ):
        plan = _gate({"action": action, "artifact_kind": "leadership update", **extra})
        assert plan.artifact_kind is None, action


def test_a_create_gathers_nothing():
    """ACTION EXCLUSIVITY, which `apply_gates` already enforces for every
    builder: a plan that BUILDS does not also gather for an answer nobody
    composes. Asserted for this action too, because a create that carried
    sources would make the log claim reads that never happened."""
    plan = _gate({
        "action": "create_artifact",
        "task": "write the launch plan",
        "artifact_kind": "launch plan",
        "sources": ["slack", "jira"],
        "web_search": True,
        "pipeline_id": "call-digest",
    })
    assert plan.action == "create_artifact"
    assert plan.sources == []
    assert plan.web_search is False
    assert plan.pipeline_id is None


def test_an_overlong_kind_is_clamped():
    """`kind` is free text from a model and is stored in a bounded column."""
    plan = _gate({
        "action": "create_artifact", "task": "t", "artifact_kind": "x" * 500,
    })
    assert len(plan.artifact_kind) <= 120
