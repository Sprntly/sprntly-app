"""The chat -> document wire.

Regression tests for a HALF-WIRED FEATURE, which is a worse failure than a
broken one: `ask_planner` could decide `create_artifact` and
`/v1/custom-artifacts/generate` could execute it, but nothing carried the
decision to the executor. An action missing from `_CLIENT_INTENTS` falls
through `_fallback("unknown action")` to a plain `answer`, so the chat replied
in prose and — knowing the product can write documents — told the user it had
made one. Nothing was created and the library stayed empty.

The tests below are written against the ENVELOPE, because the envelope is what
the client reduces over: an action the client cannot see is an action that does
not exist, whatever the planner decided.
"""
from __future__ import annotations

from app.ask_planner import Plan
from app.chat_intent import _CLIENT_INTENTS, _plan_to_envelope


def _plan(**kw) -> Plan:
    return Plan(**{"action": "create_artifact", "action_confidence": 0.9, **kw})


def test_create_artifact_is_a_client_intent():
    """THE BUG, in one line. Everything else here is downstream of it."""
    assert "create_artifact" in _CLIENT_INTENTS


def test_a_create_survives_into_the_envelope_with_its_kind():
    env = _plan_to_envelope(
        _plan(task="Summarize Q3 reliability for leadership",
              artifact_kind="leadership update"),
        prd_id=None,
    )
    assert env["intent"] == "create_artifact"
    assert env["artifact_kind"] == "leadership update"
    assert "Q3 reliability" in env["task"]


def test_a_create_with_no_task_is_downgraded_rather_than_dispatched():
    """A document with no brief is a blank page with a title on it."""
    env = _plan_to_envelope(_plan(task="", artifact_kind="memo"), prd_id=None)
    assert env["intent"] == "answer"


def test_a_low_confidence_create_is_downgraded():
    """Same floor every other action gets: a wrong build is disruptive, and
    this one lands in a library the whole team sees."""
    env = _plan_to_envelope(
        _plan(task="write something", artifact_kind="memo", action_confidence=0.2),
        prd_id=None,
    )
    assert env["intent"] == "answer"
    assert env["source"] == "low_confidence"


def test_a_create_does_not_need_a_prd():
    """Unlike edit_prd, a document stands alone — requiring a target PRD would
    make every leadership update impossible outside a PRD tab."""
    env = _plan_to_envelope(
        _plan(task="write the launch plan", artifact_kind="launch plan"), prd_id=None,
    )
    assert env["intent"] == "create_artifact"


def test_other_intents_carry_no_kind():
    for action, extra in (
        ("answer", {}),
        ("generate_prd", {"task": "a PRD about billing"}),
    ):
        env = _plan_to_envelope(
            Plan(action=action, action_confidence=0.9, **extra), prd_id=None,
        )
        assert env.get("artifact_kind") is None, action


def test_the_fallback_envelope_carries_the_key():
    """One envelope shape for every consumer: an absent key must never be
    mistaken for a kind."""
    from app.chat_intent import _fallback

    assert "artifact_kind" in _fallback("whatever")
