"""A goal typed into chat reaches Goal Analysis, not an answer.

THE FAILURE THIS EXISTS TO STOP, observed live: a user asked to "increase
revenue by 5%" and got a list of revenue opportunities — no definition
confirmed, no plan shown, nothing approved. Every gate Goal Analysis has was
bypassed, because `ask_planner` had no idea the feature existed: its action set
was answer / generate_prd / edit_prd / generate_tickets / generate_prototype /
update_ticket, so a goal fell to `answer` and an insights path took it.

The gates were reachable only through a `+` menu item. A user typing a goal into
the box never met them.
"""
from __future__ import annotations

import pytest

from app.ask_planner import ACTION_ANALYSE_GOAL, ACTION_ANSWER, apply_gates

CID = "co-goal-routing"


def _gate(action: str, task: str = "increase revenue by 5%", **kw):
    return apply_gates(
        {"action": action, "task": task, "action_confidence": 0.9},
        enterprise_id=CID, connected=[], **kw,
    )


@pytest.fixture()
def crucible_on(monkeypatch):
    monkeypatch.setattr("app.entitlements.feature_flags_for_company",
                        lambda cid: {"crucible": True})
    monkeypatch.setattr("app.entitlements.crucible_enabled", lambda flags: True)


@pytest.fixture()
def crucible_off(monkeypatch):
    monkeypatch.setattr("app.entitlements.feature_flags_for_company",
                        lambda cid: {})
    monkeypatch.setattr("app.entitlements.crucible_enabled", lambda flags: False)


def test_a_goal_routes_to_goal_analysis(crucible_on):
    plan = _gate(ACTION_ANALYSE_GOAL)
    assert plan.action == ACTION_ANALYSE_GOAL
    assert plan.task == "increase revenue by 5%"


def test_the_entitlement_fails_closed(crucible_off):
    """Goal Analysis is an allowlist feature. A company without it must not
    have a chat message silently start a run it cannot see — it degrades to
    the behaviour that message had before this action existed."""
    plan = _gate(ACTION_ANALYSE_GOAL)
    assert plan.action == ACTION_ANSWER
    assert plan.task == ""


def test_an_unreadable_flag_is_not_a_licence(monkeypatch):
    def boom(cid):
        raise RuntimeError("flags unavailable")

    monkeypatch.setattr("app.entitlements.feature_flags_for_company", boom)
    plan = _gate(ACTION_ANALYSE_GOAL)
    assert plan.action == ACTION_ANSWER, "an unreadable flag opened the gate"


def test_a_goal_with_no_task_does_not_start_an_empty_run(crucible_on):
    """The same rule every other action follows: an action whose argument is
    missing is worse than no action."""
    plan = _gate(ACTION_ANALYSE_GOAL, task="")
    assert plan.action == ACTION_ANSWER


def test_the_action_is_in_the_schema_the_model_sees(crucible_on):
    """A gate for an action the model is never offered is dead code."""
    from app.ask_planner import _PLANNER_SCHEMA

    assert ACTION_ANALYSE_GOAL in _PLANNER_SCHEMA["properties"]["action"]["enum"]
    described = _PLANNER_SCHEMA["properties"]["action"]["description"]
    # The distinction that decides the routing: a GOAL is not a QUESTION about
    # a metric. Without this the planner sends "what is our churn?" to a run.
    assert "analyse_goal" in described
    assert "what is our churn" in described.lower()
