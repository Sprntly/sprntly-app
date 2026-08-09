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
import re
from typing import Optional

from app.graph.gateway import llm_call
from app.prompt_history import render_history_block

logger = logging.getLogger(__name__)

_AGENT = "chat"
# Answer-tier model (see module docstring for why this is not the haiku
# routing tier; matches the model-tiering policy's sonnet default).
_MODEL = "claude-sonnet-4-6"

# Context budget for the envelope decision. There is no TURN cap: the reported
# failures are precisely the ones where the referent lives many turns back, and
# a 20-turn window silently deletes turn 2 of a 40-turn thread — the turn that
# named the feature "draft it up" refers to. All turns are considered; if they
# overflow the byte budget the middle is elided with a marker
# (`prompt_history.render_history_block`). The per-turn clamp still keeps one
# giant assistant answer (a VoC HTML report, a long analysis) from eating the
# whole budget on its own.
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
    "open_artifact",
)

# Artifact kinds an open request may NAME. Wider than what the chat panel can
# actually show (app.artifact_open.OPENABLE_TYPES = prd, evidence) on purpose:
# a user who says "open the dark mode prototype" named a prototype, and the
# honest answer is "prototypes open from the Artifacts tab", not a silently
# substituted PRD. The resolver reports `unsupported_type` for the extras;
# nothing here coerces one kind into another.
NAMEABLE_ARTIFACT_TYPES = ("prd", "evidence", "prototype", "report", "tickets")

# ── Deterministic OPEN-vs-GENERATE backstop ──────────────────────────────────
# The prompt below carries the real rule, and the labeled evals that prove it
# need a live model — which means they cannot gate CI. This regex pair is the
# part that CAN: a narrow, high-precision detector for the one direction that
# actually hurts (the user asked to SEE a document and got a new one written).
#
# Deliberately narrow, because its job is to be right rather than complete:
#   - the message must OPEN with a retrieval verb (after at most a short
#     conversational lead-in), so a verb buried in a subordinate clause
#     ("draft the email once you've opened the PRD") never triggers it;
#   - and it must contain NO authoring verb anywhere, so a compound request
#     ("pull up the billing PRD and then write one for checkout") is left
#     entirely to the model.
# Everything it declines falls through to the model's verdict unchanged, so a
# false negative costs nothing and a false positive is what we spent the
# narrowness on avoiding.
_OPEN_LEAD = (
    r"(?:(?:hey|hi|ok|okay|so|also|and|please|can|could|would|you|"
    r"quick(?:ly)?)\b[\s,:;–—-]*)*"
)
_OPEN_VERBS = (
    r"(?:open|re-?open|pull\s+up|bring\s+up|show(?:\s+me)?|find|locate|"
    r"take\s+me\s+to|go\s+to|jump\s+to|switch\s+to|load|display|view|"
    r"let\s+me\s+see|where\s+(?:is|are|'?s))"
)
_OPEN_REQUEST_RE = re.compile(rf"^\s*{_OPEN_LEAD}{_OPEN_VERBS}\b", re.I)
# Mirrors the authoring vocabulary in web/app/lib/prd-commands.ts (PRD_VERB_SRC)
# and skill_router's PRD rule, so a message cannot read as "authoring" on one
# side of the wire and "retrieval" on the other.
_AUTHORING_VERB_RE = re.compile(
    r"\b(?:generate|create|write|draft|make|build|prepare|produce|compose|"
    r"develop|author|spec\s+(?:this|that|it)\s+out|put\s+together|"
    r"come\s+up\s+with|spin\s+up|whip\s+up|redo|rewrite)\b",
    re.I,
)


# ── Plain-question pre-gate ──────────────────────────────────────────────────
# This module's call is not free and it is not off the answer path: the
# frontend AWAITS `POST /v1/chat/intent` before `POST /v1/ask` is even sent, so
# its sonnet call is serial dead time in front of every message (5.3s on the
# trace that motivated this, ~3s of it the model). On a message that is plainly
# a question the verdict is `answer` — the identical envelope the fail-open
# path below already produces — so the call bought nothing but the wait.
#
# Same design rule as `looks_like_open_request`: narrow, because its job is to
# be RIGHT rather than complete. Everything it declines still goes to the model
# with its verdict unchanged, so a false negative costs only the latency we pay
# today; a false positive would resurrect the exact failure this module was
# built to fix, where "break this into work items" gets answered as prose
# instead of dispatched. Hence conditions that a command cannot satisfy by
# accident, all required:
#
#   1. opens with an interrogative — the shape a question actually has;
#   2. ends with "?" — so the gate only touches what the user PUNCTUATED as a
#      question, never an imperative phrased politely ("could you draft this");
#   3. no authoring verb ANYWHERE — the same vocabulary the open-vs-generate
#      backstop vetoes on, so a compound "how should we price this, and write
#      it up?" is left entirely to the model;
#   4. not a retrieval request, which is its own intent (`open_artifact`);
#   5. short — a long message is where a buried instruction hides.
#
# The caller adds a sixth: the gate is not consulted at all when a PRD is open
# or a file is attached, because those are precisely the contexts where a
# question IS an action ("what should we change here?" on an open PRD).
_QUESTION_OPENERS_RE = re.compile(
    r"^\s*(?:what|why|how|when|who|whom|whose|where|which|"
    r"is|are|was|were|do|does|did|has|have|had|"
    r"can|could|should|would|will|any|tell\s+me)\b",
    re.I,
)
_PRE_GATE_MAX_CHARS = 200


def is_plain_question(message: str) -> bool:
    """True when `message` is UNAMBIGUOUSLY a question and nothing else.

    Pure and deterministic. Used only to skip the envelope's model call in
    favour of the `answer` verdict that call would have returned anyway; it
    never dispatches anything, so its failure mode is "we paid for the model
    call we already pay for today", not "the wrong thing happened".
    """
    text = (message or "").strip()
    if not text or len(text) > _PRE_GATE_MAX_CHARS:
        return False
    if not text.endswith("?"):
        return False
    if not _QUESTION_OPENERS_RE.search(text):
        return False
    if _AUTHORING_VERB_RE.search(text):
        return False
    if looks_like_open_request(text):
        return False
    return True


def _pre_gated() -> dict:
    """The `answer` envelope for a message the pre-gate resolved without the
    model. Shaped exactly like `_fallback`'s, but `source` distinguishes the
    two: this is a deliberate skip, not a failure, and the two must not be
    indistinguishable in telemetry."""
    return {
        "intent": "answer",
        "confidence": 1.0,
        "task": None,
        "instruction": None,
        "artifact_type": None,
        "artifact_query": None,
        "reason": "plain question — resolved without the model",
        "source": "pre_gate",
    }


def _subject_of(task: Optional[str]) -> Optional[str]:
    """A generation BRIEF reduced to something you can search titles with.

    Only used by the veto below, and only as a fallback: a `generate_prd`
    verdict fills `task` with a multi-sentence brief composed from the whole
    thread, which as a title query matches nothing and would put a paragraph
    inside the user's "I couldn't find a PRD for …" reply. First sentence,
    length-capped — enough to find the document, short enough to quote back.
    """
    if not task:
        return None
    first = re.split(r"(?<=[.!?])\s", task.strip(), maxsplit=1)[0].strip()
    # The sentence terminator rides along with the lookbehind split and is not
    # part of the subject — a trailing "." would be tokenized away by the
    # matcher anyway, but it reads wrong when quoted back to the user.
    first = first.rstrip(".!?").strip()
    if len(first) > 80:
        first = first[:80].rsplit(" ", 1)[0]
    return first or None


def looks_like_open_request(message: str) -> bool:
    """True when `message` is UNAMBIGUOUSLY a request to see an existing thing.

    Pure and deterministic — the CI-runnable half of the open-vs-generate
    guarantee. Used only to veto a `generate_prd` verdict (see
    `resolve_chat_intent`); it never promotes anything on its own, so its
    failure mode is "the model decides", not "the wrong thing happens".
    """
    text = (message or "").strip()
    if not text:
        return False
    return bool(_OPEN_REQUEST_RE.search(text)) and not _AUTHORING_VERB_RE.search(text)

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
        "artifact_type": {
            "type": ["string", "null"],
            "enum": [*NAMEABLE_ARTIFACT_TYPES, None],
            "description": (
                "open_artifact only: which existing artifact the user named. "
                "Report what they asked for even if it is not a prd or "
                "evidence — naming it is how they get told where it lives. "
                "Otherwise null."
            ),
        },
        "artifact_query": {
            "type": ["string", "null"],
            "description": (
                "open_artifact only: the SUBJECT the user named the document "
                "by, with the document noun stripped. Otherwise null."
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
product spec / product brief / requirements doc) produced FOR A CHANGE TO THE \
PRODUCT: a new capability, an improvement to an existing one, or a fix. What \
the document would be ABOUT is what selects this action — not the noun the \
user gives the document, and not the verb they ask with. This includes \
keyword-free phrasings whose meaning lives in the thread — "draft it up", \
"spec this out", "write this up as a doc", "put that together" — when the \
conversation has been converging on a feature, idea, or problem to solve. \
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
"prototype this", "mock it up", "can I see it working". The same subject test \
applies: a prototype shows a PRODUCT CHANGE working, so a request to lay out \
or visualize existing information — a report, a summary, a deck, a one-pager \
— is answer, not a prototype.

- open_artifact — the user wants to SEE a document that ALREADY EXISTS, \
brought up beside the chat: "open the PRD for compliance reporting", "pull up \
the checkout abandonment PRD", "show me the PRD about onboarding", "bring up \
the evidence for the export request", "can you open that spec again". Nothing \
is written, generated or changed — an existing document is put on screen. \
artifact_type: what the user NAMED — "prd" for a PRD / spec / requirements \
doc, "evidence" for the research write-up behind a finding, and equally \
"prototype", "report" or "tickets" when that is what they asked for. Name it \
honestly even though only PRDs and evidence open in this panel: the others \
get told where they live, and substituting a PRD for the prototype someone \
asked for is worse than saying where to find it. artifact_query: the SUBJECT the \
user named it by, with the document noun and the opening verb stripped — \
"open the PRD for compliance reporting" → "compliance reporting", "pull up \
the checkout abandonment PRD" → "checkout abandonment". Resolve a deictic \
reference from the thread ("open that one" after discussing dark mode → "dark \
mode"); leave artifact_query null only when the thread names no subject at \
all.

- answer — everything else: questions (including questions ABOUT PRDs or \
tickets — "what's in the PRD for onboarding?", "what makes a good PRD?"), \
discussion, analysis, feedback on a document, greetings. The default.

Rules:
- OPEN IS NOT GENERATE. This is the one distinction you must never get wrong, \
because the two produce opposite outcomes: open_artifact shows a document the \
user already has, generate_prd spends minutes writing a new one they did not \
ask for. The VERB decides, and only the verb: open / pull up / bring up / show \
me / find / take me to / go to / where is / let me see → open_artifact; write \
/ draft / create / generate / make / spec out / put together → generate_prd. \
Both take the same object ("a PRD for X"), so the object tells you nothing. \
When the verb is an opening verb, choose open_artifact even if no such \
document exists — whether one exists is not yours to decide, and answering \
"there isn't one" is recoverable while writing an unwanted PRD is not. When \
the verb is genuinely absent or ambiguous ("the compliance reporting PRD?"), \
prefer open_artifact over generate_prd for the same reason.
- FIRST resolve pronouns and ellipsis against the conversation; judge the \
resolved meaning, not the surface words. Where a message sits in the thread \
changes what it means: "generate a PRD" opening a thread is a bare command \
(task = whatever topic it names); the same words after twenty turns \
discussing a feature mean "generate a PRD for THAT feature" (task = the \
discussed feature, fully specified from the thread).
- Mentioning an artifact is not requesting it. Asking about, criticizing, \
or referencing a PRD or ticket is answer.
- SUBJECT MATTER decides generate_prd, never document shape. A PRD specifies \
a change to the product — something to build, improve, or fix. When the user \
instead wants information they already have gathered, explained, compared, \
summarized or reformatted — a report, a summary, a one-pager, an exec update, \
a briefing, a recap, a status write-up — that is answer, however \
document-shaped the request sounds ("put together a one-pager on our \
pricing", "write up the top issues in a formatted doc", "draft an exec update \
on this quarter", "summarize last week's calls into a document"). There is no \
report or summary action here; the answer path writes those documents itself. \
The two cases part cleanly on what the document is about, not on how it is \
phrased: after a thread about a checkout bug, "put that together" is \
generate_prd — the thing being written up is a product change; "put together \
a one-pager on our pricing" is answer — the thing being written up is \
information that already exists.
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


def _render_history(history: Optional[list[dict]]) -> str:
    """EVERY turn, oldest→newest, per-turn clamped and middle-elided if long.

    The whole thread is considered. When it exceeds the byte budget the head and
    the tail are kept and the middle is replaced by an explicit marker naming
    how many turns went — the head carries the topic a deictic message points
    at, the tail carries what it points *with*, and the marker tells the model
    the thread is partial instead of letting it read a gap as continuity.

    Per-turn clamping runs through `clamp_turn_text`, which strips `data:` URIs
    and reduces an HTML document to its narrative BEFORE the char cap: the cap
    alone keeps the bytes safe but happily spends them on raw base64 when the
    turn is a report with embedded charts, leaving the router to classify intent
    against ~1.5k of image payload instead of the prose."""
    return render_history_block(
        history,
        turn_chars=_HISTORY_TURN_CHARS,
        char_budget=_HISTORY_CHAR_BUDGET,
    )


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
        "artifact_type": None,
        "artifact_query": None,
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

    Returns {intent, confidence, task, instruction, artifact_type,
    artifact_query, reason, source} where source is "llm" for a model verdict,
    "low_confidence" for a verdict downgraded to answer, or "fallback" on any
    failure. Never raises.

    This function does NOT resolve an `open_artifact` request to a document —
    it only names the subject. The lookup (and its 0/1/many verdict) belongs to
    app.artifact_open, called by the route where the tenant scope lives.
    """
    # Skip the model entirely on a message that is plainly a question and
    # nothing else — the verdict would be `answer`, which is what this returns.
    # Deliberately NOT applied when a PRD is open or a file is attached: those
    # are the contexts where a question can carry an action ("what should we
    # change here?" against an open PRD is an edit), and the model is the only
    # thing that can tell. See `is_plain_question` for why the gate is narrow.
    if prd_id is None and not has_attachments and is_plain_question(message):
        return _pre_gated()
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
            prompt_version="chat-intent-v3",
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

        artifact_type = _clean(out.get("artifact_type"))
        envelope = {
            "intent": intent,
            "confidence": confidence,
            "task": _clean(out.get("task")),
            "instruction": _clean(out.get("instruction")),
            "artifact_type": (
                artifact_type if artifact_type in NAMEABLE_ARTIFACT_TYPES else None
            ),
            "artifact_query": _clean(out.get("artifact_query")),
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
        # ── The deterministic backstop ────────────────────────────────────────
        # A message that is unambiguously "show me an existing thing" must never
        # come back as a generation, whatever the model said. This is the only
        # place the guarantee holds without a live model, so it is the only part
        # of it that CI can gate (the labeled evals below need an API key).
        #
        # It vetoes in ONE direction and lands on two safe outcomes: an open
        # (worst case: "I couldn't find that", which opens nothing) or an answer.
        # It never turns an `answer` into an action, and it never manufactures a
        # generation.
        if envelope["intent"] == "generate_prd" and looks_like_open_request(message):
            logger.info(
                "chat intent: vetoing generate_prd for an open-shaped message "
                "(%r) — routing to open_artifact",
                message[:120],
            )
            envelope.update(
                intent="open_artifact",
                source="open_verb_veto",
                # The model's synthesized `task` is the closest thing to the
                # subject it identified; keep whatever it already gave for the
                # open, then fall back to it. `task` is dropped either way —
                # nothing downstream may read it as a generation brief.
                artifact_query=(
                    envelope["artifact_query"] or _subject_of(envelope["task"])
                ),
                artifact_type=envelope["artifact_type"] or "prd",
                task=None,
            )
        if envelope["intent"] == "open_artifact":
            # An open with no subject names nothing to look for. It degrades to
            # `answer` — NEVER to generate_prd: "open a PRD" answered with a
            # freshly written PRD is the single failure this action exists to
            # prevent, so the downgrade path is deliberately the harmless one.
            if not envelope["artifact_query"]:
                envelope.update(intent="answer", source="no_artifact_query")
            elif not envelope["artifact_type"]:
                # Nothing was NAMED (a bare "open that doc") — PRDs are what
                # people ask for, so resolve there rather than abandon a valid
                # request. A kind the user DID name is left exactly as they said
                # it, even when this panel can't show it: app.artifact_open
                # answers with `unsupported_type` and the client says where it
                # actually lives.
                envelope["artifact_type"] = "prd"
        return envelope
    except Exception:  # noqa: BLE001 — dispatch must never break the send
        logger.exception("chat intent resolve failed; falling back to answer")
        return _fallback("resolver error")
