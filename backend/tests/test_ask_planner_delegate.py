"""Gates + menu coverage for the planner's `delegate` action.

Root cause this closes: "assign David to review the evidence doc" and its
siblings ("ask/tell/get David to ...") were classified as `assign_tickets`
(the TICKET-ownership action, gated on a PRD already existing in the thread)
instead of a hand-off to a teammate — and dead-ended at `ticket_assign.py`'s
"This PRD has no tickets yet" note, with nothing ever delivered.

The action itself is chosen by a model, so what CAN be pinned in CI is the
deterministic half (gates, reachability, argument survival) plus the STATIC
menu text the model reads — the same split every other planner action's test
file uses (see `test_ask_planner_create_artifact.py`,
`test_ask_planner_goal_routing.py`'s `_menu()` pattern). Whether the model
actually PICKS `delegate` for a live phrasing needs a real classifier call —
that is the labelled-eval / live-verify lane, not this file.
"""
from __future__ import annotations

import inspect
import re

import app.ask_planner as ap
from app.ask_planner import _ACTIONS, _NEEDS_INSTRUCTION, apply_gates
from app.chat_intent import _CLIENT_INTENTS


def _gate(payload: dict):
    return apply_gates(payload, enterprise_id="co-test", connected=[])


# ── reachability + argument gates ───────────────────────────────────────────


def test_the_action_exists():
    """An action the planner cannot name is an action nothing can choose —
    the exact shape of the reported failure (assign_tickets could be named,
    delegate could not, so every delegation-shaped ask was forced onto the
    wrong action)."""
    assert "delegate" in _ACTIONS


def test_delegate_needs_an_instruction_like_assign_tickets_does():
    assert "delegate" in _NEEDS_INSTRUCTION


def test_a_well_formed_delegation_survives_with_its_instruction():
    plan = _gate({
        "action": "delegate",
        "instruction": "ask David to review the PRD and flag risks",
        "action_confidence": 0.92,
    })
    assert plan.action == "delegate"
    assert plan.instruction == "ask David to review the PRD and flag risks"


def test_a_delegation_with_no_instruction_degrades_to_answer():
    """Same rule as every other action whose argument is missing: a hand-off
    naming nobody and nothing has nothing to execute."""
    plan = _gate({"action": "delegate", "instruction": "", "action_confidence": 0.9})
    assert plan.action == "answer"


def test_delegate_is_deliberately_absent_from_the_client_vocabulary():
    """`delegate` executes entirely server-side (mirrors `update_ticket`) —
    `chat_intent._plan_to_envelope` rewrites it to `answer` before the client
    ever sees the string, so it must never be a member of `_CLIENT_INTENTS`
    (a client dispatch case for it would be dead code, since the server
    never emits it)."""
    assert "delegate" not in _CLIENT_INTENTS
    # And the superset relation the client-vocabulary test elsewhere pins
    # still holds with delegate excluded on this side of it.
    assert _CLIENT_INTENTS <= _ACTIONS


# ── the static menu text the model reads ────────────────────────────────────


def _delegate_menu() -> str:
    """The `delegate` action's own menu bullet, whitespace-normalised (the
    menu is wrapped prose in source) — mirrors
    `test_ask_planner_goal_routing.py`'s `_menu()` helper."""
    src = inspect.getsource(ap)
    i = src.index("- delegate — hand a NEW task")
    j = src.index("- multi_agent —", i)
    return re.sub(r"\s+", " ", src[i:j])


def _boundary_rule() -> str:
    """The close-call rule distinguishing `delegate` from `assign_tickets`."""
    src = inspect.getsource(ap)
    i = src.index("- EXISTING TICKET vs NEW TASK decides assign_tickets vs delegate")
    j = src.index("- ASKING ABOUT a document is not asking FOR one", i)
    return re.sub(r"\s+", " ", src[i:j])


def test_the_menu_carries_the_four_reported_failing_phrasings():
    """The exact phrasings observed live, staged as positive `delegate`
    examples so the model has them verbatim rather than having to
    generalise from a paraphrase."""
    menu = (_delegate_menu() + " " + _boundary_rule()).lower()
    for phrasing in (
        "tell david to figure out which requirements are important",
        "assign david to review the evidence doc",
        "ask david to take a look at the evidence doc",
    ):
        assert phrasing in menu, f"the menu no longer teaches {phrasing!r}"


def test_the_menu_still_teaches_the_ticket_ownership_examples():
    """Regression guard the other way: widening the menu for `delegate` must
    not cost `assign_tickets` its own worked examples — a menu that taught
    one at the expense of the other would just move the misclassification
    rather than fix it."""
    src = inspect.getsource(ap)
    i = src.index("- assign_tickets — change WHO OWNS tickets")
    j = src.index("- delegate — hand a NEW task", i)
    assign_menu = re.sub(r"\s+", " ", src[i:j]).lower()
    for phrasing in ("assign the auth ticket to dave", "reassign spr-3 to maya"):
        assert phrasing in assign_menu, f"assign_tickets lost its own example {phrasing!r}"


def test_the_boundary_rule_names_both_directions():
    """The rule must teach the split BOTH ways — an existing-ticket phrasing
    staying on assign_tickets, and a no-ticket hand-off moving to delegate —
    not just widen delegate's own bullet in isolation."""
    rule = _boundary_rule().lower()
    assert "assign_tickets" in rule and "delegate" in rule
    assert "assign the auth ticket to dave" in rule  # existing-ticket example
    assert "assign david to review the evidence doc" in rule  # no-ticket example


def test_the_slack_destination_rule_now_points_at_delegate():
    """The one other place in the prompt that already recognised
    "assign/hand/give ... to <teammate>" as a delegation
    (`share_to_slack`'s "A PERSON IS NOT A SLACK DESTINATION" carve-out) used
    to route that shape to the generic `answer` label — it must now name the
    real action, or the two rules would silently disagree about where these
    phrasings land."""
    src = inspect.getsource(ap)
    i = src.index("A PERSON IS NOT A SLACK DESTINATION")
    j = src.index("THEIR OWN CREATIONS vs THEIR CONNECTED SOURCES", i)
    rule = re.sub(r"\s+", " ", src[i:j])
    assert "`delegate`" in rule


def test_the_instruction_schema_names_delegate():
    """The JSON schema's own field description is what the model reads to
    decide WHAT to put in `instruction` for this action — an action present
    in the enum but undocumented in the schema description is only half
    taught."""
    assert "delegate" in ap._PLANNER_SCHEMA["properties"]["instruction"]["description"]


def test_the_prompt_version_reflects_the_widening():
    """`_PROMPT_VERSION` pools rows for routing-accuracy queries — a pre-
    `delegate` row answers a question ("is this a hand-off, not a ticket
    reassignment") no post-`delegate` row was ever asked, so the two must
    not be pooled. See the file's own versioned-comment ledger.

    `delegate` was v15. The pin moves with every later bump because the rule it
    encodes is about POOLING, not about `delegate`: v16 added `backlog_action`
    and `include_backlog`, which a v15 row could not name either."""
    assert ap._PROMPT_VERSION == "ask-planner-v20"
