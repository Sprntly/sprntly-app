"""The wire from planner to client for `analyse_goal`.

#1321 added the action to `ask_planner._ACTIONS` and the dispatch case to the
client, and left `chat_intent._CLIENT_INTENTS` alone — so `_plan_to_envelope`
returned `_fallback("unknown action")`, the client received `answer`, and the
feature was a no-op in production. The existing `_CLIENT_INTENTS <= _ACTIONS`
assertion is a SUBSET check pointing the other way and passed happily.
"""
from app.ask_planner import ACTION_ANALYSE_GOAL
from app.chat_intent import _CLIENT_INTENTS, _plan_to_envelope
from app.ask_planner import Plan


def _plan(**kw) -> Plan:
    return Plan(**{"action": ACTION_ANALYSE_GOAL, "action_confidence": 0.9, **kw})


def test_analyse_goal_is_a_client_intent():
    """THE BUG, in one line. Everything else here is downstream of it."""
    assert ACTION_ANALYSE_GOAL in _CLIENT_INTENTS


def test_a_goal_survives_into_the_envelope_with_its_words():
    env = _plan_to_envelope(_plan(task="increase revenue by 5%"), prd_id=None)
    assert env["intent"] == "analyse_goal"
    # The NUMBER is part of the goal — Goal Analysis asks about it.
    assert env["task"] == "increase revenue by 5%"


def test_the_envelope_is_not_the_unknown_action_fallback():
    """The precise shape of the shipped bug: a real plan arriving as `answer`.

    Asserted on the fallback's own signature rather than on `intent` alone, so
    this fails for the right reason if the wire is cut again.
    """
    env = _plan_to_envelope(_plan(task="reduce churn"), prd_id=None)
    assert env["intent"] != "answer"
    assert env.get("source") != "fallback"
