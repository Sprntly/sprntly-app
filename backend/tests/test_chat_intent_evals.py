"""app.chat_intent — resolver behavior + the intent regression gate.

Offline (runs everywhere, fake gateway): the prompt actually carries the
context the decision depends on (open-PRD line, attachment note, history
window with clamps, the newest message), and the guardrails hold (fail-open,
unknown intent, low-confidence downgrade, edit without target/instruction).

Live (`-m integration` with an API key): the labeled EVALS below run against
the real model. This set exists because the old keyword regression lists
cannot represent the actual reported failures — KEYWORD-FREE commands whose
meaning lives in the conversation ("draft it up", "break this into work
items", "make it shorter" beside an open PRD). Keyword phrasing tests all
pass while those keep breaking; these are the acceptance criteria for the
envelope dispatcher.
"""
from __future__ import annotations

import os

import pytest

import app.chat_intent as ci

# ── Labeled evals ────────────────────────────────────────────────────────────
# Each case: (name, message, history, ctx, expected intent). ctx keys:
# prd_id/prd_title/has_attachments. History oldest-first, [{role, content}].

_FEATURE_THREAD = [
    {"role": "user", "content": "Users keep asking for CSV export of the weekly report."},
    {"role": "assistant", "content": "Signal backs that: 14 requests this quarter, mostly enterprise."},
    {"role": "user", "content": "It should respect the report filters, and cap at 50k rows. Finance wants scheduled exports too."},
    {"role": "assistant", "content": "Makes sense — filtered, capped exports plus a schedule option."},
]

_PRD_OPEN_CTX = {"prd_id": 42, "prd_title": "CSV export"}

EVALS: list[tuple[str, str, list[dict], dict, str]] = [
    # The three reported acceptance phrases — keyword-free, context-only.
    ("draft-it-up", "okay, draft it up", _FEATURE_THREAD, {}, "generate_prd"),
    ("work-items", "break this into work items", _FEATURE_THREAD, _PRD_OPEN_CTX,
     "generate_tickets"),
    ("make-it-shorter", "make it shorter", [], _PRD_OPEN_CTX, "edit_prd"),
    # Position-in-thread: bare command at turn 1 is still a command.
    ("bare-command-turn1", "generate a PRD for usage-based pricing", [], {},
     "generate_prd"),
    # Same words mid-thread still a command (task from the thread — see
    # test_live_task_synthesis).
    ("bare-command-mid-thread", "generate a PRD", _FEATURE_THREAD, {},
     "generate_prd"),
    # Mention traps: ABOUT a PRD is a question, not a command.
    ("mention-question", "what's the acceptance criteria in the PRD for onboarding?",
     [], {}, "answer"),
    ("meta-question", "what makes a good PRD?", [], {}, "answer"),
    ("critique", "the PRD for dark mode is missing metrics", [], _PRD_OPEN_CTX,
     "answer"),
    # Edit vs generate beside an open PRD.
    ("edit-add-section", "add a rollout section", [], _PRD_OPEN_CTX, "edit_prd"),
    ("edit-metric", "change the success metric to weekly retention", [],
     _PRD_OPEN_CTX, "edit_prd"),
    ("new-prd-beside-open", "now write a prd for a totally different thing: audit logs",
     [], _PRD_OPEN_CTX, "generate_prd"),
    # Affirmative adoption of the assistant's own offer.
    ("yes-adopts-offer", "yes please",
     _FEATURE_THREAD + [{"role": "assistant",
                         "content": "Want me to draft a PRD for the CSV export?"}],
     {}, "generate_prd"),
    # Plain conversation stays answer.
    ("plain-question", "why are enterprise users asking for this?",
     _FEATURE_THREAD, {}, "answer"),
    ("greeting", "hey, what can you do?", [], {}, "answer"),
    # Report asks are ANSWERS, not envelope actions. A competitive review is
    # produced by the skill router on the answer path (app/competitive_intel.py);
    # there is no report intent, and classifying one of these as generate_prd
    # would open a PRD tab instead of running the review.
    ("cir-report-ask", "run a competitive intelligence report", [], {}, "answer"),
    ("cir-scan-ask", "monthly competitor scan please", [], {}, "answer"),
    ("cir-standing-ask", "where do we stand vs our competitors right now?",
     [], _PRD_OPEN_CTX, "answer"),
    ("cir-shipping-ask", "what have our competitors shipped this month?",
     _FEATURE_THREAD, {}, "answer"),
]


# ── Offline: prompt assembly + guardrails (fake gateway) ─────────────────────

class _FakeResult:
    def __init__(self, output):
        self.output = output


def _patch_llm(monkeypatch, output, calls=None):
    def _fake(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        if isinstance(output, Exception):
            raise output
        return _FakeResult(output)

    monkeypatch.setattr(ci, "llm_call", _fake)


def test_prompt_carries_context_history_and_message(monkeypatch):
    calls: list[dict] = []
    _patch_llm(monkeypatch, {"intent": "answer", "confidence": 0.9, "reason": "q"},
               calls)
    ci.resolve_chat_intent(
        "ent-1", "make it shorter",
        [{"role": "user", "content": "hello"},
         {"role": "assistant", "content": "hi there"}],
        prd_id=42, prd_title="CSV export", has_attachments=True,
    )
    (call,) = calls
    prompt = call["input"]
    assert 'PRD #42 — "CSV export" is open' in prompt
    assert "documents attached" in prompt
    assert "User: hello" in prompt and "Assistant: hi there" in prompt
    assert prompt.rstrip().endswith("Newest message: make it shorter")
    # Decision-log identity for the audit spine.
    assert call["enterprise_id"] == "ent-1"
    assert call["purpose"] == "chat_intent"


def test_prompt_without_prd_says_so(monkeypatch):
    calls: list[dict] = []
    _patch_llm(monkeypatch, {"intent": "answer", "confidence": 0.9, "reason": "q"},
               calls)
    ci.resolve_chat_intent("ent-1", "hello", [])
    assert "No PRD is open" in calls[0]["input"]


def test_history_is_clamped(monkeypatch):
    calls: list[dict] = []
    _patch_llm(monkeypatch, {"intent": "answer", "confidence": 0.9, "reason": "q"},
               calls)
    long_turn = "x" * 10_000
    ci.resolve_chat_intent(
        "ent-1", "q", [{"role": "assistant", "content": long_turn}]
    )
    prompt = calls[0]["input"]
    # Per-turn clamp applied — the giant answer arrives truncated.
    assert long_turn not in prompt
    assert "x" * ci._HISTORY_TURN_CHARS in prompt


def test_history_budget_keeps_the_newest_turns(monkeypatch):
    """When the total budget overflows, the OLDEST turns fall off — never the
    recent ones a deictic message resolves against."""
    calls: list[dict] = []
    _patch_llm(monkeypatch, {"intent": "answer", "confidence": 0.9, "reason": "q"},
               calls)
    filler = [
        {"role": "user", "content": f"old-{i} " + "y" * ci._HISTORY_TURN_CHARS}
        for i in range(ci._HISTORY_TURNS - 1)
    ]
    history = filler + [{"role": "user", "content": "NEWEST: ship the CSV export"}]
    ci.resolve_chat_intent("ent-1", "draft it up", history)
    prompt = calls[0]["input"]
    assert "NEWEST: ship the CSV export" in prompt
    assert "old-0 " not in prompt  # the oldest filler fell off, not the newest


def test_valid_action_envelope_passes_through(monkeypatch):
    _patch_llm(monkeypatch, {
        "intent": "generate_prd", "confidence": 0.9,
        "task": "CSV export: filtered, 50k-row cap, scheduled exports",
        "reason": "thread converged on the feature",
    })
    env = ci.resolve_chat_intent("ent-1", "draft it up", _FEATURE_THREAD)
    assert env["intent"] == "generate_prd"
    assert env["source"] == "llm"
    assert "50k-row cap" in env["task"]


def test_gateway_error_fails_open_to_answer(monkeypatch):
    _patch_llm(monkeypatch, RuntimeError("gateway down"))
    env = ci.resolve_chat_intent("ent-1", "draft it up", _FEATURE_THREAD)
    assert env["intent"] == "answer"
    assert env["confidence"] == 0.0
    assert env["source"] == "fallback"


def test_unknown_intent_fails_open(monkeypatch):
    _patch_llm(monkeypatch, {"intent": "launch_rocket", "confidence": 0.99,
                             "reason": "?"})
    env = ci.resolve_chat_intent("ent-1", "hi", [])
    assert env["intent"] == "answer"
    assert env["source"] == "fallback"


def test_low_confidence_action_downgrades_to_answer(monkeypatch):
    _patch_llm(monkeypatch, {"intent": "generate_prd", "confidence": 0.4,
                             "task": "something", "reason": "weak"})
    env = ci.resolve_chat_intent("ent-1", "maybe a doc?", [])
    assert env["intent"] == "answer"
    assert env["source"] == "low_confidence"


def test_low_confidence_answer_is_not_downgraded(monkeypatch):
    _patch_llm(monkeypatch, {"intent": "answer", "confidence": 0.3, "reason": "meh"})
    env = ci.resolve_chat_intent("ent-1", "hmm", [])
    assert env["intent"] == "answer"
    assert env["source"] == "llm"


def test_generate_without_a_topic_downgrades(monkeypatch):
    """An action needs its object resolved before it fires.

    `edit_prd` has always required a target PRD and an instruction; the two
    tests below pin that. `generate_prd` required NOTHING — the schema even
    authorised `task: null` — and the frontend dispatches on the intent alone,
    so a confident-but-topicless read authored a document about nothing.
    """
    _patch_llm(monkeypatch, {"intent": "generate_prd", "confidence": 0.95,
                             "task": None, "reason": "seems like a doc"})
    env = ci.resolve_chat_intent("ent-1", "write this up", [])
    assert env["intent"] == "answer"
    assert env["source"] == "no_task"


def test_generate_with_a_topic_still_fires(monkeypatch):
    """The slot check must not disarm the feature: a resolved topic goes
    through untouched."""
    _patch_llm(monkeypatch, {"intent": "generate_prd", "confidence": 0.95,
                             "task": "offline exports for the mobile app",
                             "reason": "explicit request"})
    env = ci.resolve_chat_intent("ent-1", "write this up", [])
    assert env["intent"] == "generate_prd"
    assert env["source"] == "llm"
    assert env["task"] == "offline exports for the mobile app"


def test_blank_task_counts_as_no_topic(monkeypatch):
    """`_clean` normalises whitespace-only to None; the gate reads the cleaned
    value, so '   ' is as topicless as null."""
    _patch_llm(monkeypatch, {"intent": "generate_prd", "confidence": 0.95,
                             "task": "   ", "reason": "blank"})
    env = ci.resolve_chat_intent("ent-1", "do the thing", [])
    assert env["intent"] == "answer"
    assert env["source"] == "no_task"


def test_edit_without_target_prd_downgrades(monkeypatch):
    _patch_llm(monkeypatch, {"intent": "edit_prd", "confidence": 0.9,
                             "instruction": "shorten it", "reason": "edit"})
    env = ci.resolve_chat_intent("ent-1", "make it shorter", [])
    assert env["intent"] == "answer"
    assert env["source"] == "no_target_prd"


def test_edit_without_instruction_downgrades(monkeypatch):
    _patch_llm(monkeypatch, {"intent": "edit_prd", "confidence": 0.9,
                             "instruction": "  ", "reason": "edit"})
    env = ci.resolve_chat_intent("ent-1", "tweak it", [], prd_id=42)
    assert env["intent"] == "answer"
    assert env["source"] == "no_instruction"


def test_tickets_without_target_keeps_intent(monkeypatch):
    """Tickets with no PRD keeps the intent — the client owns the
    'generate a PRD first' prerequisite flow."""
    _patch_llm(monkeypatch, {"intent": "generate_tickets", "confidence": 0.9,
                             "reason": "tickets"})
    env = ci.resolve_chat_intent("ent-1", "break this into work items", [])
    assert env["intent"] == "generate_tickets"


# ── Live regression gate (real model; opt-in) ────────────────────────────────

@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="needs live model")
@pytest.mark.parametrize(
    "name,message,history,ctx,expected", EVALS, ids=[e[0] for e in EVALS]
)
def test_live_intent_accuracy(name, message, history, ctx, expected):
    env = ci.resolve_chat_intent(
        "eval", message, history,
        prd_id=ctx.get("prd_id"), prd_title=ctx.get("prd_title"),
        has_attachments=ctx.get("has_attachments", False),
    )
    assert env["intent"] == expected, (
        f"{name}: expected {expected}, got {env['intent']} "
        f"(source={env['source']}, reason={env['reason']!r})"
    )


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="needs live model")
def test_live_task_synthesis_is_self_contained():
    """The vagueness fix itself: a deictic command must yield a task composed
    from the thread — the topic and the concrete requirements, not a pronoun."""
    env = ci.resolve_chat_intent("eval", "okay, draft it up", _FEATURE_THREAD)
    assert env["intent"] == "generate_prd"
    task = (env["task"] or "").lower()
    assert "csv" in task or "export" in task
    assert "50k" in task or "50,000" in task
    assert "it" != task.strip()
