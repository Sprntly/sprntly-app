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


# ── The non-entitled tenant must be affected NOT AT ALL ──────────────────────

def test_a_workspace_without_the_module_is_told_before_the_model_chooses():
    """Finding 4: the gate alone made non-entitled tenants WORSE off.

    `_PLANNER_SYSTEM` tells the model that for any action other than `answer` it
    should not also pick a pipeline or sources. `apply_gates` runs after that,
    so dropping `analyse_goal` back to `answer` left `pipeline_id` and `sources`
    already emptied — a thinner answer than the same message got before the
    action existed. Telling the model up front is what keeps it a true no-op.
    """
    import app.ask_planner as ap

    text = ap._goal_analysis_block(False)
    assert "Never choose" in text and "analyse_goal" in text
    # It must also repair the instruction it is overriding, not just forbid the
    # action — otherwise the model still skips the pipeline it would have picked.
    assert "pipeline" in text and "sources" in text


def test_a_workspace_with_the_module_gets_no_block_at_all():
    """Empty, so the entitled path's prompt is byte-identical to before."""
    import app.ask_planner as ap

    assert ap._goal_analysis_block(True) == ""


def test_the_capability_line_rides_the_uncached_input_not_the_system_block():
    """PER-COMPANY DATA NEVER GOES IN `_PLANNER_SYSTEM`.

    The system block is tenant-invariant so one Anthropic cache entry serves
    every company; this module states that rule for three other blocks already.
    A boolean leaks no customer text, but it would still fork the shared entry.
    """
    import app.ask_planner as ap

    assert "Goal Analysis is not available" not in ap._PLANNER_SYSTEM
    built = ap._build_input(
        "increase revenue by 5%", connected=[], custom_block="",
        keyword_prior="", history=None, goal_analysis_available=False,
    )
    assert "Goal Analysis is not available" in built


# ─── A goal asked as a question is still a goal ─────────────────────────────


def _menu() -> str:
    """The action menu the planner shows the model."""
    import inspect

    from app import ask_planner

    import re

    src = inspect.getsource(ask_planner)
    i = src.index("- analyse_goal —")
    # WHITESPACE-NORMALISED: the menu is wrapped prose, so a phrasing the model
    # reads as one clause ("how can we improve activation") is split across two
    # lines with indentation in the source. Asserting on the raw text checks
    # where the line breaks fall, which is not the property.
    return re.sub(r"\s+", " ", src[i:src.index("- generate_tickets", i)])


def test_the_menu_teaches_that_an_interrogative_goal_is_a_goal():
    """OBSERVED LIVE, and the distinction was one no user could be expected to
    make: "How to grow revenue by 5%" routed to `answer` and reached the DS
    agent, while "How can we grow revenue by 5%" routed correctly.

    v13 taught the action with imperative examples and then drew the exclusion
    at "A GOAL IS NOT A QUESTION ABOUT A METRIC" — a line between statements and
    questions rather than between REPORTING a number and CHANGING one. The
    question form is the most common way a PM phrases a goal.
    """
    menu = _menu().lower()
    for phrasing in ("how to grow revenue", "how do we reduce churn",
                     "how can we improve activation", "what can we do about"):
        assert phrasing in menu, f"the menu no longer teaches {phrasing!r}"


def test_the_menu_still_sends_a_REPORTING_question_to_answer():
    """The exclusion has to survive the widening. "what is our churn?" asks
    what a number IS; it is not a goal, and routing it to Goal Analysis would
    put a definition gate in front of a lookup."""
    menu = _menu().lower()
    assert "what is our churn" in menu
    assert "how did revenue move last quarter" in menu
    # And the line is drawn on report-versus-change, not on punctuation.
    assert "report" in menu and "change" in menu


def test_the_prompt_version_moved_with_the_menu():
    """`_PROMPT_VERSION` pools rows for routing-accuracy queries. A v13 row and
    a v14 row answer differently for every interrogative goal, so pooling them
    would measure nothing — the file's own rule is that anything altering what
    the prompt ASKS bumps. Bumped to v15 by the `delegate` action, then to v16
    by `backlog_action` + `include_backlog`; each answers a question the
    version before it was never asked."""
    from app.ask_planner import _PROMPT_VERSION

    assert _PROMPT_VERSION == "ask-planner-v18"
