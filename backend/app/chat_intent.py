"""Chat intent dispatch — one context-aware decision for every chat message.

Today the "is this message a COMMAND?" decision is spread across three
frontend dispatchers (BriefChat's exported regexes, ChatScreen's precedence
ladder + the classify-command haiku fallback, AIBar's private copies) and the
backend skill router — all keyword-anchored, all judging the newest message in
isolation. That is why "generate a PRD" behaves identically at turn 1 and turn
40, why "draft it up" / "break this into work items" / "make it shorter" fall
through to a text answer, and why the task handed to the PRD pipeline is often
a bare keyword instead of the feature the thread spent twenty turns
specifying.

This module is the replacement decision layer: ONE model call that sees the
conversation history, the active-tab context (open PRD, attachment), and the
newest message, and returns an ACTION ENVELOPE — a tool-style verdict

    {intent, confidence, task, instruction, reason}

where `intent` names the executor (the frontend reducer / future server
dispatch maps it onto the EXISTING endpoints: generate_prd →
POST /v1/prd/generate-from-task, edit_prd → POST /v1/prd/{id}/chat-edit,
generate_tickets → POST /v1/stories/generate, generate_prototype → the
design-agent flow, answer → POST /v1/ask) and the argument fields are
synthesized from the WHOLE conversation, not the surface words of one message.

The envelope is deliberately shaped like a one-iteration tool-use loop
(intents = tool names, fields = tool args) so the natural evolution — the
model answering directly on `answer`, or asking a clarifying question then
acting — is an extension, not a rewrite.

Model: answer-tier sonnet, not the haiku router tier. Selection alone could
run on haiku, but the argument synthesis is the part that fixes the reported
vagueness — compressing a long thread into a self-contained task brief is
exactly what the smallest model does worst, and this call replaces both the
classify-command call and (on command turns) the ask round-trip, so the cost
delta is small.

Fail-open contract: ANY failure (gateway down, bad JSON, unknown intent)
returns an `answer` envelope with confidence 0.0 — the worst outcome is
today's behavior (the message goes to the ask agent), never a broken send.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.graph.gateway import llm_call
from app.prompt_history import clamp_turn_text

logger = logging.getLogger(__name__)

_AGENT = "chat"
# Answer-tier model (see module docstring for why this is not the haiku
# routing tier; matches the model-tiering policy's sonnet default).
_MODEL = "claude-sonnet-4-6"

# Context budget for the envelope decision. Wider than the qa router's
# 6-turn window — the reported failures are precisely the ones where the
# referent lives many turns back. Per-turn clamp keeps one giant assistant
# answer (a VoC HTML report, a long analysis) from eating the whole budget.
_HISTORY_TURNS = 20
_HISTORY_TURN_CHARS = 1_500
_HISTORY_CHAR_BUDGET = 24_000

# A non-answer intent below this confidence is downgraded to `answer` — the
# recoverable default (a wrong answer costs a follow-up; a wrong generation
# is disruptive). Matches the qa router's LLM threshold.
_ACTION_CONFIDENCE_FLOOR = 0.6

INTENTS = (
    "answer",
    "generate_prd",
    "edit_prd",
    "generate_tickets",
    "generate_prototype",
)

# Intents that act ON an existing PRD. edit_prd with no resolvable target is
# meaningless and downgrades to `answer`; tickets/prototype keep their intent
# even without a target — the client already has the "generate a PRD first"
# prerequisite flow for exactly that case.
_NEEDS_PRD = frozenset({"edit_prd"})

_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "confidence": {"type": "number", "description": "0..1 for the chosen intent."},
        "task": {
            "type": ["string", "null"],
            "description": (
                "generate_prd only: self-contained brief for the document "
                "author, composed from the whole conversation. Otherwise null."
            ),
        },
        "instruction": {
            "type": ["string", "null"],
            "description": (
                "edit_prd only: the change to apply, self-contained. "
                "Otherwise null."
            ),
        },
        "reason": {"type": "string", "description": "One short clause."},
    },
    "required": ["intent", "confidence", "reason"],
    "additionalProperties": False,
}

_SYSTEM = """You are the intent dispatcher for a product-management \
assistant's chat. Every message the user sends arrives here first. Given the \
conversation so far, the active-tab context, and the newest message, decide \
the SINGLE action the user wants and extract its arguments. You do not answer \
the message yourself.

Actions:

- generate_prd — the user wants a PRD (product requirements document / \
product spec / product brief / requirements doc) produced. This includes \
keyword-free phrasings whose meaning lives in the thread — "draft it up", \
"spec this out", "write this up as a doc", "put that together" — when the \
conversation has been converging on a feature, idea, or problem. \
task: a SELF-CONTAINED brief for the document author, composed from the \
whole conversation: the topic plus EVERY requirement, constraint, and detail \
the user gave, kept verbatim where possible — never summarize away \
specifics, never invent new ones, never a bare pronoun. null only when the \
thread offers no topic at all.

- edit_prd — the user wants the EXISTING PRD changed: "make it shorter", \
"add a rollout section", "change the success metric to weekly retention", \
"tighten the scope". Choose this (not generate_prd) when a PRD is open on \
this tab or this thread produced one and the message asks to change it. \
instruction: the change to apply, self-contained — resolve "it" / "that \
section" / "the metric" from the conversation.

- generate_tickets — break a PRD or spec into tickets / stories / work \
items: "create tickets", "break this into work items", "turn that into \
stories", "split this up for the team".

- generate_prototype — an interactive prototype / mockup of the PRD: \
"prototype this", "mock it up", "can I see it working".

- answer — everything else: questions (including questions ABOUT PRDs or \
tickets — "what's in the PRD for onboarding?", "what makes a good PRD?"), \
discussion, analysis, feedback on a document, greetings. The default.

Rules:
- FIRST resolve pronouns and ellipsis against the conversation; judge the \
resolved meaning, not the surface words. Where a message sits in the thread \
changes what it means: "generate a PRD" opening a thread is a bare command \
(task = whatever topic it names); the same words after twenty turns \
discussing a feature mean "generate a PRD for THAT feature" (task = the \
discussed feature, fully specified from the thread).
- Mentioning an artifact is not requesting it. Asking about, criticizing, \
or referencing a PRD or ticket is answer.
- generate_prd vs edit_prd: no PRD exists yet in this tab/thread → \
generate_prd; one exists and the message asks to change it → edit_prd. \
"Redo it with X" aimed at an existing PRD is still edit_prd.
- Direction decides edit_prd, and the same words run both ways. "update the \
PRD with the ticket details" changes the DOCUMENT → edit_prd. "update the \
ticket details with the PRD" changes the TICKET, which is not an action here → \
answer (the ask path owns it). Whichever the verb reaches first is the thing \
being changed; when the target is a ticket, issue or story, choose answer.
- A bare affirmative ("yes", "go ahead", "do it") adopts whatever action \
the assistant just offered in its most recent turn; if it offered nothing, \
answer.
- When genuinely torn between an action and answer, prefer answer.
- confidence: your 0..1 belief in the chosen intent given the full context."""


def _clamp(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + " …"


def _render_history(history: Optional[list[dict]]) -> str:
    """Last-N turns, rendered oldest→newest, per-turn and total clamped.

    The total budget is spent NEWEST-FIRST: when a long thread overflows it,
    the oldest turns fall off — a deictic message ("draft it up") resolves
    against what was just discussed, so recency must win the budget."""
    if not history:
        return ""
    rows: list[str] = []
    total = 0
    for turn in reversed(history[-_HISTORY_TURNS:]):
        role = (turn.get("role") or "user").capitalize()
        # clamp_turn_text first: the per-turn char cap alone keeps the BYTES safe
        # but happily spends them on raw base64 when the turn is an HTML report
        # with embedded charts — the router then classifies intent against ~1.5k
        # of image payload instead of the narrative. Strip the data URIs and
        # reduce the document to its text, THEN apply the existing cap.
        content = _clamp(
            clamp_turn_text(turn.get("content"), max_chars=_HISTORY_TURN_CHARS),
            _HISTORY_TURN_CHARS,
        )
        if not content:
            continue
        row = f"{role}: {content}"
        total += len(row)
        if total > _HISTORY_CHAR_BUDGET:
            break
        rows.append(row)
    if not rows:
        return ""
    rows.reverse()
    return "Conversation so far:\n" + "\n".join(rows) + "\n\n"


def _render_context(
    prd_id: Optional[int], prd_title: Optional[str], has_attachments: bool
) -> str:
    if prd_id:
        title = f' — "{prd_title}"' if prd_title else ""
        prd_line = f"Active tab: PRD #{prd_id}{title} is open beside this chat."
    else:
        prd_line = "No PRD is open on this tab."
    lines = [prd_line]
    if has_attachments:
        lines.append("The newest message has one or more documents attached.")
    return "\n".join(lines) + "\n\n"


def _fallback(reason: str) -> dict:
    return {
        "intent": "answer",
        "confidence": 0.0,
        "task": None,
        "instruction": None,
        "reason": reason,
        "source": "fallback",
    }


def resolve_chat_intent(
    enterprise_id: str,
    message: str,
    history: Optional[list[dict]] = None,
    *,
    prd_id: Optional[int] = None,
    prd_title: Optional[str] = None,
    has_attachments: bool = False,
) -> dict:
    """Decide the action envelope for one chat message, in context.

    Returns {intent, confidence, task, instruction, reason, source} where
    source is "llm" for a model verdict, "low_confidence" for a verdict
    downgraded to answer, or "fallback" on any failure. Never raises.
    """
    try:
        result = llm_call(
            enterprise_id=enterprise_id,
            agent=_AGENT,
            purpose="chat_intent",
            model=_MODEL,
            system=_SYSTEM,
            input=(
                _render_context(prd_id, prd_title, has_attachments)
                + _render_history(history)
                + f"Newest message: {message}"
            ),
            prompt_version="chat-intent-v1",
            json_schema=_SCHEMA,
            # The task field echoes requirement details verbatim from a long
            # thread — give it room.
            max_tokens=4000,
        )
        out = result.output if isinstance(result.output, dict) else {}
        intent = (out.get("intent") or "").strip()
        if intent not in INTENTS:
            return _fallback("unknown intent")
        confidence = float(out.get("confidence") or 0.0)

        def _clean(value: object) -> Optional[str]:
            return value.strip() if isinstance(value, str) and value.strip() else None

        envelope = {
            "intent": intent,
            "confidence": confidence,
            "task": _clean(out.get("task")),
            "instruction": _clean(out.get("instruction")),
            "reason": _clean(out.get("reason")) or "",
            "source": "llm",
        }
        if intent != "answer" and confidence < _ACTION_CONFIDENCE_FLOOR:
            envelope.update(intent="answer", source="low_confidence")
        if envelope["intent"] in _NEEDS_PRD and not prd_id:
            envelope.update(intent="answer", source="no_target_prd")
        if envelope["intent"] == "edit_prd" and not envelope["instruction"]:
            # An edit with no instruction can't be applied; the ask path at
            # least answers the message.
            envelope.update(intent="answer", source="no_instruction")
        return envelope
    except Exception:  # noqa: BLE001 — dispatch must never break the send
        logger.exception("chat intent resolve failed; falling back to answer")
        return _fallback("resolver error")
