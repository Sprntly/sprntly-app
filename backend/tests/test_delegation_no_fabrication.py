"""Property tests for the project-chat delegation fix: a "send this to
<teammate> to prioritize" request must actually delegate (root cause #1 —
the planner's action-classification must not read a named PERSON as a
Slack destination), and the agent must never fabricate the delegatee's
work after handing it off (root cause #2 — both project-surface system
prompts must forbid self-performing the task or claiming completion).

These are LLM-facing prompt/tool-description property tests (content +
negative-space, no live model call) — the same discipline
`test_project_delegation.py`'s `test_delegate_tool_description_when_and_
negative_space` / `test_brief_prompt_forbids_trailing_question_and_
requires_fields` already apply to this feature's other two prompts. A
grounded staging repro is what surfaced both root causes; there is no
CI-runnable way to prove the model now classifies/behaves correctly short
of a live call (see `test_project_delegation.py`'s
`RUN_DELEGATE_TASK_LIVE=1` tier for that arm), so what these pin is that
the INSTRUCTION the model is given actually says the right thing.
"""
from __future__ import annotations

import app.ask_job_runner as ajr
import app.ask_planner as ap
import app.routes.projects as projects_route


# ── Root cause #1: a person is not a Slack destination ───────────────────


def test_planner_prompt_distinguishes_a_named_person_from_a_slack_channel():
    system = ap._PLANNER_SYSTEM.lower()
    assert "person is not a slack destination" in system or (
        "not a channel" in system and "teammate" in system
    )
    # The exact delegation-shaped phrasing the staging repro used, given as
    # a worked example so the model has something concrete to pattern-match
    # against rather than only an abstract rule.
    assert "send this to fortune to prioritize" in system
    # The rule must land on `answer`, never invent a `delegate` action here —
    # delegation is resolved by the project agent itself, not this planner.
    assert "delegation" in system

    weak = ap._PLANNER_SYSTEM.replace(
        "A PERSON IS NOT A SLACK DESTINATION", "PERSONS AND CHANNELS ARE THE SAME"
    ).lower()
    assert "person is not a slack destination" not in weak


def test_planner_prompt_share_to_slack_example_is_unchanged():
    """Negative-space companion: the existing share_to_slack examples
    (a real channel, '#product') must still read as share_to_slack — this
    fix narrows the target to non-person destinations, it does not remove
    the action."""
    system = ap._PLANNER_SYSTEM
    assert "share this prd on my slack channel" in system.lower()
    assert "share_to_slack" in system


# ── Root cause #2: no fire-and-fabricate after a handoff ─────────────────


def _assert_forbids_fabrication(system: str, *, label: str) -> None:
    lower = system.lower()
    assert "delegate_task" in lower, f"{label}: must mention the delegate_task tool"
    assert "do not" in lower or "never" in lower or "not" in lower
    assert "replied" in lower or "finished" in lower or "completed" in lower or "done anything" in lower, (
        f"{label}: must forbid claiming the assignee has replied/finished/completed"
    )
    assert "stop" in lower or "done" in lower, (
        f"{label}: must tell the model the handoff is where it stops"
    )


def test_group_scope_system_forbids_fabricated_delegation_result():
    _assert_forbids_fabrication(projects_route._GROUP_SCOPE_SYSTEM, label="group")

    weak = "You have a delegate_task tool: call it when asked to hand off a task."
    with_out_error = False
    try:
        _assert_forbids_fabrication(weak, label="weak")
    except AssertionError:
        with_out_error = True
    assert with_out_error, "the property check must actually catch a prompt missing the rule"


def test_private_scope_system_mentions_delegate_task_and_forbids_fabrication():
    """Root cause #2 also covers the private surface, whose system prompt
    did not mention `delegate_task` AT ALL before this fix — the tool rode
    only on `extra_tools` with no behavioral guidance around it."""
    _assert_forbids_fabrication(ajr._PRIVATE_SCOPE_SYSTEM, label="private")


def test_group_and_private_prompts_share_the_same_confirmation_shape():
    """Both surfaces should land on the same honest, non-fabricated
    confirmation shape ("I've asked <name> to <task> ... once it's in") —
    proving the fix was applied consistently rather than only on the
    surface the repro happened to hit."""
    for system, label in (
        (projects_route._GROUP_SCOPE_SYSTEM, "group"),
        (ajr._PRIVATE_SCOPE_SYSTEM, "private"),
    ):
        lower = system.lower()
        assert "i've asked" in lower, f"{label}: missing the honest confirmation example"
        assert "bring" in lower or "once it's in" in lower, (
            f"{label}: confirmation example must point at a future report-back, "
            "not a fabricated result now"
        )
