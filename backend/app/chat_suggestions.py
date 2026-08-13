"""Next-prompt suggestions for chat — and, more importantly, the machinery that
makes SILENCE the default outcome.

After an answer lands, the chat can offer two or three prompts that continue the
conversation. The product requirement is not "always offer something": it is
**when Sprntly does not know what to suggest, it must suggest NOTHING**. An empty
strip is a correct, expected, frequent result; a plausible-but-untethered
suggestion ("Would you like to explore this further?") is a defect. Everything
below is built around that asymmetry.

Abstention is enforced in FOUR independent layers, so it never rests on the
model choosing to be modest:

 1. **Pre-call gate.** No assistant answer to continue from, an errored/empty
    turn, or the feature switched off → `[]` with no LLM call at all. Cheapest
    abstention is the one that never bills.
 2. **Schema.** `suggestions` is an array with no `minItems`, so `[]` is a
    first-class, one-token answer the model can emit — not something it has to
    talk its way out of. The prompt states plainly that returning nothing is the
    right answer when the thread offers no clear next step, and enumerates the
    filler shapes that are never acceptable.
 3. **The anchor gate — the load-bearing one.** Every suggestion must carry an
    `anchor`: a short phrase COPIED from the conversation that the suggestion
    follows from. We then check that anchor actually occurs in the conversation
    text (normalized). A suggestion invented out of nothing has nothing true to
    copy, so it is dropped by a deterministic string check rather than by
    trusting a self-reported confidence. This is what turns "don't hallucinate"
    from a prompt request into a verified property.
 4. **Deterministic filters.** Per-suggestion confidence floor, a filler
    denylist, a length band, de-duplication (including against questions the
    user already asked), and a hard cap of three.

Fail CLOSED, unlike `chat_intent`'s fail-open envelope: any exception, bad JSON,
or unknown shape returns `[]`. There, degrading meant "answer the message
normally"; here, degrading means showing the user invented text. Silence is
always the safe direction.

Model: haiku tier. The work this call does — read a thread, copy an anchor,
write a short question — is well within haiku, and the correctness guarantee
lives in the deterministic gates above rather than in model judgement, so
paying sonnet rates per chat turn buys nothing. Roughly $0.004 per turn at
haiku's $1/$5 per MTok (see MODEL_PRICING in app.llm_telemetry).
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

from app.graph.gateway import llm_call
from app.prompt_history import clamp_turn_text, render_history_block

logger = logging.getLogger(__name__)

_AGENT = "chat"
# Haiku tier — see module docstring. NOT the answer tier: this is a per-turn
# call on a good-to-have surface, and its correctness comes from the gates.
_MODEL = "claude-haiku-4-5"

# Smaller window than the intent envelope's: continuing a conversation depends
# on its RECENT shape, and this call is per-turn so its input is what costs.
_HISTORY_TURN_CHARS = 1_200
_HISTORY_CHAR_BUDGET = 10_000

# A suggestion below this self-reported confidence is dropped. Deliberately the
# same floor as chat_intent's action gate. It is the WEAKEST of the four layers
# (models are poorly calibrated, and a confident-sounding filler still scores
# high) — the anchor gate is what actually catches untethered text.
_CONFIDENCE_FLOOR = 0.6

# At most three, so the strip never competes with the answer for attention.
MAX_SUGGESTIONS = 3

# A suggestion has to be a real prompt: long enough to say something, short
# enough to read as a chip rather than a paragraph.
_MIN_PROMPT_CHARS = 12
_MAX_PROMPT_CHARS = 120

# An anchor shorter than this cannot distinguish "grounded in the thread" from
# "contains the word 'the'", so it fails the gate instead of passing it cheaply.
_MIN_ANCHOR_CHARS = 8

# Content-free continuations. Every one of these is a suggestion that would be
# equally "valid" after ANY conversation, which is exactly the failure mode the
# requirement names. Matched against the normalized prompt.
_FILLER_PATTERNS = (
    r"^tell me more",
    r"^can you (tell me more|elaborate|explain more|expand)",
    r"^(what|anything) else",
    r"^(please )?(elaborate|continue|go on|expand)",
    r"^(summarize|summarise) (this|that|it)$",
    r"^(any|some) (other|more) (thoughts|ideas|suggestions)",
    r"^what (do you think|should i do)( next)?\??$",
    r"^help me (with )?(this|that)$",
    r"^(show|tell) me more (about )?(this|that|it)$",
    r"^(what are the )?next steps\??$",
    r"^how can (you|i) help",
)
_FILLER_RE = tuple(re.compile(p) for p in _FILLER_PATTERNS)

_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            # NO minItems. An empty array is a valid, complete, expected answer.
            "maxItems": MAX_SUGGESTIONS,
            "description": (
                "0 to 3 next prompts. Return [] whenever the conversation does "
                "not point at a specific next step — an empty list is a correct "
                "answer, not a failure."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": (
                            "The prompt the user would send next, written in "
                            "their voice, first person, under 100 characters."
                        ),
                    },
                    "anchor": {
                        "type": "string",
                        "description": (
                            "2-8 words COPIED VERBATIM from the conversation "
                            "that this suggestion follows from. Must appear in "
                            "the text above exactly as written."
                        ),
                    },
                    "confidence": {
                        "type": "number",
                        "description": "0..1 that this is a next step the user actually wants.",
                    },
                },
                "required": ["prompt", "anchor", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["suggestions"],
    "additionalProperties": False,
}

_SYSTEM = """You propose the next prompt a user might send in a \
product-management assistant's chat, given the conversation so far.

YOUR DEFAULT ANSWER IS AN EMPTY LIST. Returning `{"suggestions": []}` is a \
correct, complete, and frequently right answer. You are not here to fill a \
slot. A user who sees nothing has lost nothing; a user who sees an invented \
suggestion has been misled about what this assistant knows.

Return a suggestion ONLY when the conversation itself points at a specific \
next step — a named thing the assistant offered, a concrete gap the answer \
left open, an obvious drill-down into data that was actually presented, or a \
document the user is plainly building toward.

OFFERING TO WRITE A DOCUMENT is one of the strongest suggestions you can make, \
and often the right one. When the thread has produced material that a person \
would normally write up for an audience — an update for leadership, a launch \
plan, a postmortem, a customer FAQ, release notes, a board memo — suggest \
writing it, naming the kind and the subject: "Draft a leadership update on the \
Q3 reliability work", "Write this up as a launch plan".

Phrase it as an instruction to WRITE the document, not a question about it: \
the user sends your prompt as their next message, and only a clear request \
creates anything. "What would leadership want to know?" produces an answer; \
"Draft a leadership update on X" produces the document. Never suggest writing \
a document the conversation has no material for — the anchor rule applies to \
these exactly as it does to every other suggestion.

Return [] when:
- the exchange is closed (a greeting, a thank-you, a one-fact answer);
- the answer failed, was refused, or said it lacked the data;
- the topic is too vague to continue specifically;
- the only things you can think of would fit ANY conversation;
- you are guessing.

Every suggestion MUST carry an `anchor`: 2-8 words copied verbatim from the \
conversation text above, which the suggestion follows from. Copy the words \
exactly — do not paraphrase, translate, or reword them. If you cannot point at \
words that were actually written, you do not have a suggestion; drop it.

Write each prompt in the user's own voice ("Break the top three into \
tickets"), first person where natural, under 100 characters, specific enough \
that it could not be pasted into a different conversation. Never repeat a \
question the user already asked. Never propose a suggestion whose answer the \
assistant just gave.

NEVER acceptable, at any confidence: "Tell me more", "What else?", \
"Can you elaborate?", "What are the next steps?", "Summarize this", "Any \
other thoughts?", or any variation whose meaning does not depend on this \
particular conversation."""

_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def _norm(text: object) -> str:
    """Lowercase, punctuation-stripped, whitespace-collapsed — the comparison
    form for both the anchor gate and de-duplication. Deliberately lossy: it
    forgives smart quotes, casing and trailing punctuation (things a model
    changes while still quoting faithfully) without forgiving a different
    phrase."""
    if not isinstance(text, str):
        return ""
    return _NON_WORD_RE.sub(" ", text.lower()).strip()


def enabled() -> bool:
    """Kill switch, read at CALL time so it is flippable without a redeploy.

    Default ON: the whole feature is silent by construction, so the switch
    exists for cost control (it removes the per-turn LLM call) rather than for
    safety. `CHAT_SUGGESTIONS_ENABLED=0|false|no|off` turns it off.
    """
    raw = (os.environ.get("CHAT_SUGGESTIONS_ENABLED") or "").strip().lower()
    if not raw:
        return True
    return raw not in {"0", "false", "no", "off"}


def _conversation_text(history: list[dict]) -> str:
    """Every turn's text, normalized, as ONE haystack for the anchor gate.

    Clamped the same way the prompt is (`clamp_turn_text` strips data: URIs and
    reduces an HTML report to its narrative) so the haystack contains exactly
    the text the model was shown — otherwise an anchor copied faithfully out of
    a long turn could fail the gate because the model saw a truncation the
    haystack didn't, or vice versa.
    """
    return _norm(
        "\n".join(
            clamp_turn_text(row.get("content"), max_chars=_HISTORY_TURN_CHARS)
            for row in history
            if isinstance(row, dict)
        )
    )


def _user_questions(history: list[dict]) -> set[str]:
    """Normalized user turns — nothing already asked gets suggested back."""
    return {
        _norm(row.get("content"))
        for row in history
        if isinstance(row, dict) and row.get("role") == "user"
    }


def _last_assistant_text(history: list[dict]) -> str:
    for row in reversed(history):
        if isinstance(row, dict) and row.get("role") == "assistant":
            content = row.get("content")
            return content if isinstance(content, str) else ""
    return ""


def filter_suggestions(
    raw: object, conversation_text: str, already_asked: Optional[set[str]] = None
) -> list[str]:
    """The deterministic half of abstention. Model output in, prompts out.

    Pure and exported so the gates are testable without a model: every drop
    below is a rule that a live suggestion has to survive, and the whole set
    resolving to `[]` is a normal outcome, not an error path.
    """
    if not isinstance(raw, dict):
        return []
    items = raw.get("suggestions")
    if not isinstance(items, list):
        return []

    asked = already_asked or set()
    kept: list[str] = []
    seen: set[str] = set()

    for item in items:
        if not isinstance(item, dict):
            continue
        prompt = item.get("prompt")
        anchor = item.get("anchor")
        if not isinstance(prompt, str) or not isinstance(anchor, str):
            continue
        prompt = prompt.strip()
        if not (_MIN_PROMPT_CHARS <= len(prompt) <= _MAX_PROMPT_CHARS):
            continue

        try:
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError):
            # An unparseable confidence is not a zero-cost benefit of the
            # doubt — the item is malformed, so it goes.
            continue
        if confidence < _CONFIDENCE_FLOOR:
            continue

        normalized = _norm(prompt)
        if any(pattern.match(normalized) for pattern in _FILLER_RE):
            continue
        if normalized in seen or normalized in asked:
            continue

        # THE ANCHOR GATE. The claimed grounding must really be in the thread.
        normalized_anchor = _norm(anchor)
        if len(normalized_anchor) < _MIN_ANCHOR_CHARS:
            continue
        if normalized_anchor not in conversation_text:
            continue

        seen.add(normalized)
        kept.append(prompt)
        if len(kept) >= MAX_SUGGESTIONS:
            break

    return kept


def suggest_next_prompts(
    enterprise_id: str,
    history: Optional[list[dict]] = None,
    *,
    prd_id: Optional[int] = None,
    prd_title: Optional[str] = None,
) -> list[str]:
    """0-3 prompts continuing this conversation. Never raises; `[]` is normal.

    `history` is the thread oldest-first, `[{role, content}]`, as
    `routes/ask.py:_load_history` returns it — including the turn that just
    completed, since the newest answer is precisely what a next prompt
    continues.
    """
    if not enabled():
        return []
    rows = [row for row in (history or []) if isinstance(row, dict)]
    if len(rows) < 2:
        # Nothing has been answered yet: there is no exchange to continue, only
        # a question in flight. Abstain before spending anything.
        return []
    if len(_last_assistant_text(rows).strip()) < 40:
        # An empty, errored or one-word final turn. Whatever the model would
        # invent from it would be invented from nothing.
        return []

    conversation_text = _conversation_text(rows)
    if not conversation_text:
        return []

    try:
        result = llm_call(
            enterprise_id=enterprise_id,
            agent=_AGENT,
            purpose="chat_suggestions",
            model=_MODEL,
            system=_SYSTEM,
            input=_render_context(prd_id, prd_title)
            + render_history_block(
                rows,
                turn_chars=_HISTORY_TURN_CHARS,
                char_budget=_HISTORY_CHAR_BUDGET,
            )
            + "Suggest the next prompts, or return an empty list.",
            prompt_version="chat-suggestions-v1",
            json_schema=_SCHEMA,
            # Three short questions plus anchors. A small ceiling also bounds
            # the output half of the per-turn cost.
            max_tokens=600,
        )
    except Exception:  # noqa: BLE001 — a suggestion strip must never surface an error
        logger.exception("chat suggestions failed; abstaining")
        return []

    return filter_suggestions(
        result.output, conversation_text, _user_questions(rows)
    )


def _render_context(prd_id: Optional[int], prd_title: Optional[str]) -> str:
    if prd_id:
        title = f' — "{prd_title}"' if prd_title else ""
        return f"Active tab: PRD #{prd_id}{title} is open beside this chat.\n\n"
    return "No PRD is open on this tab.\n\n"
