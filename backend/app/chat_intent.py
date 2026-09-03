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


# ── Deterministic open-artifact detection ───────────────────────────────────
# The model classifier is FLAKY on a bare open ("open the prd", "show me the
# PRD" — an opening verb + an artifact-type noun with NO title): it returns
# `answer`, the client sends the message to /v1/ask, and the answer engine
# refuses ("I can't open the PRD in your browser — that's a UI action"). This
# pair — an artifact-noun matcher and a subject extractor — turns any clear open
# request into an `open_artifact` verdict WITHOUT the model, so the flake can't
# happen. It fires only on the same narrow, authoring-free shape the veto uses,
# so it never swallows a "create/generate a PRD" request, and a null subject is
# legal (the route's project scope resolves it to the sole/best artifact).
_ARTIFACT_NOUN_RE = re.compile(
    r"\b(prds?|spec(?:s|ification)?|requirements?|evidence|"
    r"prototypes?|mock-?ups?|reports?|tickets?|stor(?:y|ies))\b",
    re.I,
)

# List/count asks ("which PRDs", "how many reports", "list my specs") are a
# DIFFERENT intent (`list_artifacts`); leave them to the model rather than
# force-opening one document.
_LIST_CUE_RE = re.compile(r"\b(?:which|what|list|how\s+many|every)\b", re.I)

# The opening verbs, determiners, prepositions and artifact nouns that are
# CHROME around the subject — everything that is not one of these is the title
# the user named. Nouns for EVERY kind are dropped (the kind is captured
# separately), so "open the checkout PRD" yields "checkout", not "checkout prd".
_OPEN_SUBJECT_DROP = frozenset(
    {
        "open", "reopen", "pull", "bring", "up", "show", "find", "locate",
        "take", "go", "jump", "switch", "load", "display", "view", "let", "see",
        "me", "to", "where", "is", "are", "hey", "hi", "ok", "okay", "so",
        "also", "and", "please", "can", "could", "would", "you", "quick",
        "quickly", "the", "a", "an", "my", "our", "this", "that", "for",
        "about", "of", "on", "in",
        "prd", "prds", "spec", "specs", "specification", "requirement",
        "requirements", "evidence", "prototype", "prototypes", "mockup",
        "mockups", "report", "reports", "ticket", "tickets", "story", "stories",
        "doc", "docs", "document", "documents",
    }
)

_SUBJECT_WORD_RE = re.compile(r"[a-z0-9][a-z0-9-]*", re.I)


def _artifact_type_for_noun(noun: str) -> Optional[str]:
    """Map a matched artifact noun to a NAMEABLE_ARTIFACT_TYPES value."""
    n = noun.lower().replace("-", "")
    if n in ("prd", "prds", "spec", "specs", "specification", "requirement",
             "requirements"):
        return "prd"
    if n == "evidence":
        return "evidence"
    if n in ("prototype", "prototypes", "mockup", "mockups"):
        return "prototype"
    if n in ("report", "reports"):
        return "report"
    if n in ("ticket", "tickets", "story", "stories"):
        return "tickets"
    return None


def _open_subject(text: str) -> Optional[str]:
    """The TITLE inside an open request, or None when it is a bare open.

    Everything that is not an opening verb, a determiner/preposition or an
    artifact noun is the subject — so "open the PRD for compliance reporting"
    yields "compliance reporting" and "open the PRD" yields None.
    """
    kept = [
        w for w in _SUBJECT_WORD_RE.findall(text.lower())
        if w not in _OPEN_SUBJECT_DROP
    ]
    subject = " ".join(kept).strip()
    return subject or None


def detect_open_intent(message: str) -> Optional[tuple[str, Optional[str]]]:
    """(`artifact_type`, `artifact_query`) for a clear open request, else None.

    Fires only when the message OPENS with an opening verb, names an
    artifact-type noun, carries NO authoring verb, and is not a list/count ask.
    `artifact_query` is the named title, or None for a bare open (a legal value
    the resolver turns into the sole/best artifact of that kind).
    """
    text = (message or "").strip()
    if not text or len(text) > _PRE_GATE_MAX_CHARS:
        return None
    if not _OPEN_REQUEST_RE.search(text):
        return None
    if _AUTHORING_VERB_RE.search(text):
        return None
    if _LIST_CUE_RE.search(text):
        return None
    nm = _ARTIFACT_NOUN_RE.search(text)
    if not nm:
        return None
    artifact_type = _artifact_type_for_noun(nm.group(1))
    if artifact_type is None:
        return None
    return artifact_type, _open_subject(text)


# Intents that act ON an existing PRD. edit_prd with no resolvable target is
# meaningless and downgrades to `answer`; tickets/prototype keep their intent
# even without a target — the client already has the "generate a PRD first"
# prerequisite flow for exactly that case. change_prd_template joins edit_prd:
# switching the format of no document is equally meaningless, and the answer it
# downgrades to carries the library (the planner forces include_library on the
# no-target plan), so it can truthfully say what formats exist and where the
# PRD panel's Format control lives.
# assign_tickets joins them: its ticket universe IS the thread's PRD (the
# tickets generated from it), so with no PRD in context there is nothing to
# assign and the downgrade-to-answer can say so honestly.
_NEEDS_PRD = frozenset({"edit_prd", "change_prd_template", "assign_tickets"})

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


def _fallback(reason: str, exc: BaseException | None = None) -> dict:
    """The fail-open `answer` envelope.

    When the failure was a PROVIDER REFUSAL, the envelope says so. This
    endpoint's fail-open contract is right — a dead model must never break a
    send — but it has a cost nobody could see: with the planner down, NO action
    can be chosen, so every command in the product silently becomes a chat
    reply. Observed 2026-08-16, on an exhausted Anthropic balance: commands
    stopped working, the chat answered in prose, and the only evidence was a
    line in the container log.

    `provider_error` rides the envelope so the client can say what happened.
    Absent on every ordinary fallback, so nothing changes for the failures that
    are genuinely ours.
    """
    notice = None
    if exc is not None:
        try:
            from app.llm_errors import limit_notice

            notice = limit_notice(exc)
        except Exception:  # noqa: BLE001 — the error path must not raise
            notice = None
    return {
        "intent": "answer",
        "confidence": 0.0,
        "provider_error": notice,
        "task": None,
        "instruction": None,
        "artifact_type": None,
        "artifact_query": None,
        # Present-and-null rather than absent, so every consumer sees ONE
        # envelope shape and a missing key can never be mistaken for a kind.
        "artifact_kind": None,
        "reason": reason,
        "source": "fallback",
    }


# ── planner-backed resolution ────────────────────────────────────────────────
#
# When the Ask Planner is deciding for this company, the action verdict comes
# from IT rather than from the call below, and this module becomes an adapter
# onto the envelope the client reducer already consumes.
#
# WHY FOLD THEM. Today a chat message costs two independent model calls that
# cannot see each other: this one picks the action, `qa_agent.route` picks the
# skill. Neither knows what the other decided, and neither knows what the
# company has connected — so "write a PRD about what competitors are doing" is
# judged twice, from different context, and the two verdicts can disagree with
# nothing to reconcile them. The planner makes it one decision, and one call:
# planner (sonnet) replaces this call (sonnet) PLUS the router (haiku), so the
# fold is strictly cheaper per message than what it replaces.
#
# The ENVELOPE SHAPE IS UNCHANGED, deliberately. `ChatIntentEnvelope` in
# web/app/lib/api.ts and the reducers in ChatScreen/BriefChat keep working
# untouched — this is a swap of what is behind the endpoint, not a new contract.


#: Intents a CLIENT can be handed. `INTENTS` is this module's own resolver
#: vocabulary (unchanged, so its fallback path behaves exactly as before);
#: this is the wider set the planner can produce and a surface may act on.
#: Kept as its own name rather than widening `INTENTS`, because the resolver
#: below cannot produce these and asserting that is worth a constant.
#: `change_prd_template` dispatches POST /v1/prd/{id}/change-template with the
#: envelope's `artifact_template_id`; a surface that predates it falls through
#: to its ask path like any unknown intent.
_CLIENT_INTENTS: frozenset[str] = frozenset(INTENTS) | {
    "multi_agent", "change_prd_template",
    # Write a document of any kind into the shared "Others" library.
    #
    # THIS SET IS THE WIRE, and leaving an action out of it is a silent
    # half-feature rather than an error. `ask_planner` could already decide
    # `create_artifact`, and `/v1/custom-artifacts/generate` could already
    # execute it — but an action missing here falls through
    # `_fallback("unknown action")` to a plain `answer`, so the chat REPLIED IN
    # PROSE and, knowing the product can write documents, told the user it had
    # made one. Nothing was created and the library stayed empty. Shipped that
    # way in #1154; found by Apurva asking for a leadership update.
    "create_artifact",
    # Change who OWNS tickets. The client resolves it against the thread's PRD
    # (POST /v1/tickets/assign-plan) and asks per-ticket through the question
    # popup when the mapping is ambiguous; the envelope's `instruction` is the
    # whole argument. Listed here for exactly the reason create_artifact's
    # comment records: this set is the wire, and an action missing from it is
    # a silent half-feature, not an error.
    "assign_tickets",
    # Change the report or document open beside the chat. Listed here for the
    # reason `create_artifact`'s comment above records at length: THIS SET IS
    # THE WIRE. An action the planner can decide and an endpoint can execute,
    # but which is missing here, falls through `_fallback("unknown action")` to
    # a plain answer — so the chat would reply in prose describing an edit it
    # never made, which is precisely the failure being fixed.
    "edit_artifact",
    # A business GOAL to move — hands off to Goal Analysis, which asks what the
    # metric means, states what it will read, and waits for approval.
    #
    # THE WIRE AGAIN, and this one was shipped cut. #1321 taught `ask_planner`
    # the `analyse_goal` action and added the client's dispatch case, and
    # omitted this line — so the planner decided correctly, `_plan_to_envelope`
    # hit `_fallback("unknown action")`, and the client received `answer`. A
    # user asking to increase revenue by 5% still got a list of opportunities:
    # the exact bug the PR was written to fix, shipped as a no-op.
    #
    # The subset assertion in `test_ask_planner.py` (`_CLIENT_INTENTS <=
    # _ACTIONS`) cannot catch this — it points the other way. The membership
    # test that can lives beside this file's other intents.
    "analyse_goal",
    # The tickets counterpart of change_prd_template — dispatches
    # POST /v1/stories/change-template with the envelope's
    # `artifact_template_id`. The TARGET is resolved client-side (the thread's
    # standalone ticket set, else the tab PRD's tickets), because a standalone
    # set has no prd_id for `_NEEDS_PRD` to check — which is why this intent is
    # deliberately NOT in that set: downgrading on "no PRD" would kill the
    # switch for exactly the sets that need it. A thread with neither falls
    # through to the grounded ask on the client, same as any unhandled intent.
    "change_tickets_template",
    # "What are my PRDs / tickets / reports?" — list what the user has CREATED,
    # as clickable items. Retrieval like open_artifact: the route attaches the
    # actual rows under `artifact_list` (the tenant-scoped lookup lives where
    # the tenant scope lives — routes/chat.py), and the client renders them and
    # opens a click into the artifact's own thread. Listed here for exactly the
    # reason create_artifact's comment records: this set is the wire, and an
    # action missing from it is a silent half-feature, not an error.
    "list_artifacts",
    # Post an artifact into the company's Slack. The client resolves the
    # TARGET from its own context (the tab's PRD, the thread's ticket set or
    # report) plus the envelope's `artifact_type`/`artifact_query`, previews
    # what will be posted via POST /v1/share/slack/preview, and only sends on
    # POST /v1/share/slack/send after the user confirms.
    #
    # Listed here for exactly the reason create_artifact's comment records —
    # this set is the wire — and the stakes are higher for this one than for
    # any other member. An action missing here falls through to `answer`, and
    # the answer path, knowing the product can post to Slack, would reply that
    # it had shared the document. Nothing would reach Slack, and unlike an
    # empty library nobody can check a channel they were never told about.
    "share_to_slack",
    # Create a PROJECT — the container, not a document. The client calls
    # POST /v1/projects with the envelope's `task` as the name and opens the
    # new project. Listed here for exactly the reason create_artifact's
    # comment records: this set is the wire, and an action missing from it
    # falls through to `answer` — where the chat, knowing the product has
    # projects, replies that it made one and nothing exists.
    "create_project",
}
#: `delegate` is DELIBERATELY ABSENT from the set above, unlike every other
#: action this module's comments warn about. It is not a missed wire — see
#: `_plan_to_envelope`'s `intent in ("update_ticket", "delegate")` rewrite:
#: both actions execute entirely server-side off the plain `answer` path, so
#: adding `delegate` here would only let a stale `intent == "delegate"` reach
#: a client that has never had — and does not need — a case for it.


def _is_report_pipeline(pipeline_id: Optional[str], question: str = "") -> bool:
    """Does this plan's pipeline WRITE A REPORT DOCUMENT?

    `qa_agent._REPORT_PIPELINE_IDS` is the set the answer path itself dispatches
    on, so it is the one read here — a second list would drift and the drift
    would be invisible until a report printed itself into a chat thread.

    ONE carve-out, over `call_digest.VOC_PIPELINE_IDS`: the voice-of-customer
    machinery is reached by TWO pipeline ids — "call-digest" (the calls-shaped
    pick) and "voice-of-customer-report" (the skill id the planner picks for a
    VoC question that never says "calls") — and both run the same `answer`,
    which serves the full report, the pointed query and the map-reduce count
    from one entry point. The planner classifies by question SHAPE before that
    function has decided which of the three it will run, so the ids have to
    cover all of them.

    KEYED ON THE SET, NEVER THE STRING. Testing `pipeline_id == "call-digest"`
    is what let this fire for half the traffic it was written for: a table ask
    ("a list of the features clients asked for … in a form of a table") plans
    as "voice-of-customer-report", so the carve-out was skipped, the client
    opened a Reports panel, and the answer path returned a query answer to the
    thread — the reported bug, one pipeline id over from the one it was
    supposed to be fixed in.

    `call_digest.is_voc_query` IS that decision — the same function
    `call_digest.answer` forks on (`query_mode`), so this cannot drift from what
    actually happens — and everything it claims answers INLINE with
    `_report: False`. Such a turn must not open the Reports drawer or show
    report-generation copy just because it shares a pipeline id with the report
    it is not writing.

    THE COMMONEST CASE IS A SUMMARY (owner's rule, 2026-09-03). "Give me
    summary on last week's customer conversations" now answers in the thread —
    see `call_digest.is_voc_query` — so the panel must not open for it. Before
    this, the endpoint promised a report, the answer path wrote none, and the
    reader watched a Reports panel that never filled.

    This supersedes the narrower count-shaped carve-out that stood here:
    `is_mapreducible_count` requires `is_voc_query`, so the wider check
    subsumes it, needs no feature-flag read, and closes the same mismatch for
    every other query shape ("which accounts complained about latency") that
    was already answering inline under a `report: true` envelope.

    Every other `_REPORT_PIPELINE_IDS` member names exactly one shape and needs
    no such carve-out.

    Imported lazily: `qa_agent` is a heavy module and this endpoint is on the
    send path, which imports `ask_planner` the same way one function below.
    """
    if not pipeline_id:
        return False
    try:
        from app.qa_agent import _REPORT_PIPELINE_IDS

        if pipeline_id not in _REPORT_PIPELINE_IDS:
            return False
        from app.call_digest import VOC_PIPELINE_IDS, is_voc_query

        if pipeline_id in VOC_PIPELINE_IDS and is_voc_query(question):
            return False
        return True
    except Exception:  # noqa: BLE001 — never break the verdict over a hint
        logger.exception("report-pipeline check failed for %s", pipeline_id)
        return False


def _plan_to_envelope(
    plan, *, prd_id: Optional[int], open_artifact: Optional[dict] = None,
    question: str = "",
) -> dict:
    """A gated `ask_planner.Plan` in this module's envelope vocabulary.

    `question` is the original message, threaded through ONLY so the
    `"report"` key can tell a count-shaped `call-digest` question apart from
    a report-shaped one — see `_is_report_pipeline`. Defaulted so every
    existing direct caller (tests included) that has no message in hand
    keeps working unchanged; a missing question just means the `call-digest`
    carve-out never fires, which is the pre-existing (report=True) behaviour.

    Three of this module's own downgrade rules are re-applied HERE rather than
    trusted to the planner, because each needs something the planner does not
    have:

      * the ACTION CONFIDENCE FLOOR. `_gate_action` validates that an action is
        known and carries its argument; it does not judge conviction. Acting on a
        0.2-confidence `generate_prd` is disruptive in a way that a 0.2-confidence
        answer is not, so the floor stays exactly where it was and at the same
        value.
      * `_NEEDS_PRD`. Whether a target PRD exists is a tenant-scoped DB fact; the
        planner runs with no `prd_id` and could not check it if it wanted to.
      * the empty-instruction guard, for the same reason it exists here: an edit
        with nothing to apply at least gets answered.

    `update_ticket` maps to `answer`: it is not a client dispatch — the
    ticket-update executor runs server-side off the answer path — so the client
    has nothing to do with it beyond showing the reply.

    `delegate` maps to `answer` for the same reason, and deliberately, not as
    an oversight: the actual hand-off is server-side too — the project chat's
    scoped tool loop (`skill_router.is_project_tool_request` → `delegate_task`
    → `project_delegation.handle_delegate_task`), reached off the SAME
    grounded-ask path every plain answer already takes once the client falls
    through to it. Rewriting `delegate` to `answer` here is what MAKES that
    reuse work: the client resends the user's ORIGINAL message unparaphrased,
    which is exactly what the tool loop's own regex gate and the delegating
    model read — a client-side executor synthesizing a second call from
    `instruction` would risk drifting from the sentence the tool loop actually
    sees, and would duplicate `handle_delegate_task`'s resolve/gate/deliver
    path for no reason. See `ask_planner`'s `delegate` entry in `_ACTIONS` for
    why the action exists as its own explicit, gated planner decision even
    though it is invisible to the client.

    Every OTHER action passes straight through, including ones only some
    surfaces can act on. `multi_agent` is the case that makes this the right
    shape: the AI bar runs it, ChatScreen does not, and a surface that cannot
    handle an intent simply falls through to its ask path. Collapsing it here
    instead would take the capability away from the surface that HAS it, to
    protect one that never asked.
    """
    intent = plan.action
    if intent in ("update_ticket", "delegate"):
        intent = "answer"
    # A document with no brief is a blank page with a title on it. The planner
    # already degrades this (`_NEEDS_TASK`), and it is re-applied here for the
    # same reason the confidence floor is: this envelope is what the CLIENT
    # acts on, so every condition that must hold before a build starts is
    # enforced where the build is dispatched from.
    if intent == "create_artifact" and not (plan.task or "").strip():
        intent = "answer"
    # A project with no subject is an untitled container. Same re-application,
    # same reason: this envelope is what the CLIENT acts on, so the condition
    # is enforced where the create is dispatched from.
    if intent == "create_project" and not (plan.task or "").strip():
        intent = "answer"
    if intent not in _CLIENT_INTENTS:
        return _fallback("unknown action")

    envelope = {
        "intent": intent,
        # THE ACTION'S confidence, not the pipeline's. `plan.confidence` sits
        # under `pipeline_id` in the planner's schema and answers "how sure are
        # you about this PIPELINE" — for which the normal answer is "there isn't
        # one". Reading it here vetoed real commands with a number that was
        # never about them: "generate prd for me and please use the template 1
        # template" arrived as generate_prd at pipeline-confidence 0.5 and was
        # downgraded to a plain answer, repeatedly, including one plan at 0.0.
        "confidence": plan.action_confidence,
        "task": plan.task or None,
        "instruction": plan.instruction or None,
        "artifact_type": (
            plan.artifact_type
            if plan.artifact_type in NAMEABLE_ARTIFACT_TYPES else None
        ),
        # `create_artifact` only: WHAT KIND of document, in the user's own
        # words ("leadership update"). Free text — the executor stores it as a
        # label and nothing branches on it. None on every other intent, because
        # the planner's gate clears it there.
        "artifact_kind": plan.artifact_kind or None,
        "artifact_query": plan.artifact_query,
        # `share_to_slack` only: WHERE it goes and WHAT is said with it. Both
        # may be null — no channel means the client asks which one (never a
        # guessed destination), no note means the document goes out on its
        # own. The planner's gate clears the pair on every other intent.
        "share_channel": plan.share_channel,
        "share_note": plan.share_note,
        # The uploaded format this build must be written into, when the user
        # named one. The client forwards the id to the executor; the NAME is for
        # the client to say which format it is using, so an honoured request is
        # visible to the person who made it rather than something they have to
        # take on trust.
        "artifact_template_id": plan.artifact_template_id,
        "artifact_template_name": plan.artifact_template_name,
        # `list_artifacts` only: which kind of the user's own creations to
        # list ("all" | "prd" | "evidence" | "prototype" | "report" |
        # "ticket_set" | "custom_artifact"). None on every other intent; the
        # rows themselves are attached by the route, where tenancy lives.
        "list_kind": plan.list_kind,
        # And whether the ask was HOW MANY rather than WHICH ONES — "count"
        # makes the route attach per-day tallies (`artifact_counts`) so the
        # client can answer with the numbers instead of a wall of cards.
        "list_mode": plan.list_mode,
        # And how many they asked for ("my last 5 PRDs" → 5, "the latest PRD"
        # → 1), from the planner's gated constraints. None — the common case —
        # means the route's own cap. Scoped to the intent like list_kind, so a
        # top_n extracted for an ordinary answer never leaks in here.
        "list_limit": (
            plan.constraints.get("top_n")
            if intent == "list_artifacts" and isinstance(plan.constraints, dict)
            else None
        ),
        "reason": plan.reason or "",
        "source": "planner",
        # The answer this turn produces is a REPORT DOCUMENT, not a chat reply:
        # the planner resolved one of the report pipelines and the gate accepted
        # it. The client opens the panel's Reports tab in its generating state
        # and streams the document THERE — the posture a PRD build already takes
        # — instead of printing a report into the thread it is about to appear
        # beside. False is the ordinary case and reads as "an answer".
        #
        # Read from the SAME set `qa_agent` dispatches on, never a second list:
        # a name that fell out of one and not the other would open a panel for
        # an answer, or print a report into the chat.
        "report": _is_report_pipeline(plan.pipeline_id, question),
        # `edit_artifact` only: WHICH document the edit targets — the report or
        # team document the tab has open, re-read server-side. The client
        # already knows what its own panel is showing; this is echoed so the
        # reducer acts on the SAME artifact the decision was grounded on, the
        # way `prd_id`/`prd_title` are echoed for `edit_prd`.
        "open_artifact": open_artifact,
    }
    # A FORMAT WE COULD NOT FIND STOPS THE BUILD (owner's decision, 2026-08-10).
    # `template_query` is only ever set when the user named a format and nothing
    # in their library matched it, and the alternative — building in the ACTIVE
    # format instead — is the silent substitution this whole feature exists to
    # end: they asked for one format, got another, and nothing on screen says so.
    #
    # Downgrading to `answer` is what turns it into a question, and it costs
    # nothing else: the planner has already forced `include_library` on this
    # plan, so the answer that runs has the company's real format list in front
    # of it and can ask WHICH ONE by name instead of apologising in the abstract.
    #
    # ORDERED AFTER the confidence floor on purpose. Both read the ORIGINAL
    # `intent` rather than the envelope (the floor check always has), so the
    # later of the two wins the `source` when both apply — and when a build both
    # named an unknown format and scored low, the format is the reason the user
    # can act on. Everything below reads `envelope["intent"]` and so cannot
    # overwrite it once this has landed on `answer`.
    if intent != "answer" and plan.action_confidence < _ACTION_CONFIDENCE_FLOOR:
        envelope.update(intent="answer", source="low_confidence")
    if intent != "answer" and plan.template_query:
        envelope.update(intent="answer", source="template_not_found")
    if envelope["intent"] in _NEEDS_PRD and not prd_id:
        envelope.update(intent="answer", source="no_target_prd")
    if envelope["intent"] == "edit_prd" and not envelope["instruction"]:
        envelope.update(intent="answer", source="no_instruction")
    if envelope["intent"] == "edit_artifact" and (
        not envelope["instruction"] or not open_artifact
    ):
        # The same two re-applications `edit_prd` gets, for the same reasons:
        # an edit with nothing to apply has nothing to do, and a TARGET is a
        # tenant-scoped fact the planner cannot check (it is told what is open,
        # but the id it would act on is resolved here). `answer` is the
        # recoverable landing — it can ask which document, or what to change.
        envelope.update(
            intent="answer",
            source="no_instruction" if not envelope["instruction"] else "no_target_artifact",
        )
    if envelope["intent"] == "assign_tickets" and not envelope["instruction"]:
        # Same rule as edit_prd: an assignment with nobody named and nothing
        # targeted is a dispatch with nothing to execute. The planner gates
        # this too (_NEEDS_INSTRUCTION); re-applied where the client is told
        # what to do.
        envelope.update(intent="answer", source="no_instruction")
    if envelope["intent"] == "open_artifact" and not envelope["artifact_query"]:
        # The planner already gates this, but the rule is re-applied here for
        # the same reason the other three are: this function owns what the
        # CLIENT is told to do, and an open request with nothing to look up
        # must reach it as `answer`, never as an open of nothing.
        envelope.update(intent="answer", source="no_artifact_query")
    if (
        envelope["intent"] in ("change_prd_template", "change_tickets_template")
        and not envelope["artifact_template_id"]
    ):
        # Re-applied like open_artifact above: the planner downgrades a
        # switch with no target itself, but this function owns what the client
        # is told to do, and a change-template dispatch with no format id would
        # be an executor call with nothing to execute. Covers both switches —
        # the tickets one included, even though its ticket-set target is
        # resolved client-side, because the FORMAT argument is the backend's to
        # gate either way.
        envelope.update(intent="answer", source="no_target_format")
    return envelope


def _resolve_via_planner(
    enterprise_id: str,
    message: str,
    history: Optional[list[dict]],
    *,
    prd_id: Optional[int],
    prd_title: Optional[str] = None,
    open_artifact: Optional[dict] = None,
    thread_artifact: Optional[dict] = None,
) -> Optional[dict]:
    """The planner's verdict as an envelope, or None to use the call below.

    None on every reason not to plan — decide mode off, no tenant, a planner
    failure — so a planner outage degrades this endpoint to exactly the
    behaviour it had before, which is a working product. `plan_for_answer`
    already swallows and logs; this only has to handle "it declined"."""
    from app import ask_planner

    plan = ask_planner.plan_for_answer(
        enterprise_id=enterprise_id,
        question=message,
        history=history,
        # The thread's open PRD is an INPUT to the plan, not just an after-the-
        # fact gate on its verdict: without it the planner answered "build a
        # report from this prd" with "no PRD is open or identified in the thread".
        prd_id=prd_id,
        prd_title=prd_title,
        # THE LINE THE EDIT PRECONDITION READS. `plan_for_answer` has always
        # taken this and this call has never passed it, so the planner's prompt
        # was rendered without any "Active tab: report #45 … is open beside
        # this chat" line — and `edit_artifact`'s own rule says "Choose this
        # only when that line names a report or a document". With the line
        # never present the action was unreachable: every "add a risks section
        # to that report" planned as `answer` and came back as the rewritten
        # section printed into the chat, with the report untouched. Reported as
        # "it cannot edit a report based on a prompt".
        #
        # `thread_artifact` is the same referent when the panel is showing
        # nothing — a report this conversation produced is still what "that
        # report" means to the person who asked for it here.
        open_artifact=open_artifact or thread_artifact,
    )
    if plan is None:
        return None
    envelope = _plan_to_envelope(
        plan, prd_id=prd_id, open_artifact=open_artifact or thread_artifact,
        question=message,
    )
    # The full gated plan rides along under `plan`, for the browser console.
    # Everything on it was already decided server-side and is already visible in
    # the backend log; this only saves someone testing from having to watch
    # `docker logs` in another window to see WHY a message went where it did.
    #
    # Diagnostic only — no client branches on it, and it carries no secret: the
    # sources are provider KEYS the caller's own company connected, and the
    # reason is one clause the model wrote about the user's own message.
    envelope["plan"] = plan.as_log_dict()
    logger.info(
        "[planner] intent company=%s intent=%s source=%s",
        enterprise_id, envelope["intent"], envelope["source"],
    )
    return envelope


def resolve_chat_intent(
    enterprise_id: str,
    message: str,
    history: Optional[list[dict]] = None,
    *,
    prd_id: Optional[int] = None,
    prd_title: Optional[str] = None,
    has_attachments: bool = False,
    open_artifact: Optional[dict] = None,
    #: The report/document THIS THREAD produced, when the panel is showing
    #: none. Resolved by the route (`_thread_edit_target`), and used for the
    #: same two jobs `open_artifact` is: the referent in the planner's prompt,
    #: and the target an `edit_artifact` verdict acts on.
    thread_artifact: Optional[dict] = None,
) -> dict:
    """Decide the action envelope for one chat message, in context.

    Returns {intent, confidence, task, instruction, artifact_type,
    artifact_query, reason, source} where source is "planner" when the Ask
    Planner decided, "pre_gate" when a plainly-question-shaped message resolved
    without any model, "llm" for this module's own model verdict,
    "low_confidence" / "no_target_prd" / "no_instruction" for a verdict
    downgraded to answer, or "fallback" on any failure. Never raises.

    This function does NOT resolve an `open_artifact` request to a document —
    it only names the subject. The lookup (and its 0/1/many verdict) belongs to
    app.artifact_open, called by the route where the tenant scope lives.
    """
    # ── Deterministic open-artifact force (before ANY model) ──
    # A clear open request — an opening verb + an artifact-type noun, with or
    # without a title — is classified WITHOUT the model, in front of both the
    # planner and this module's own model call. The classifier is flaky on a
    # BARE open ("open the prd"), returning `answer`; the client then sends it to
    # /v1/ask and the answer engine refuses with "that's a UI action". This makes
    # a clear open deterministic, so that path can't be reached. A null subject
    # is intentional and legal — the route's project scope resolves it to the
    # project's sole/best artifact (app.artifact_open), and on main chat to the
    # sole one or an ambiguous chip list; it NEVER becomes a generation (the
    # authoring-verb guard in detect_open_intent leaves "create/generate" alone).
    # Skipped when a file is attached (an "open this" leans toward import — left
    # to the model, matching the pre-gate's own attachment carve-out).
    if not has_attachments:
        detected = detect_open_intent(message)
        if detected is not None:
            _open_type, _open_query = detected
            return {
                "intent": "open_artifact",
                "confidence": 1.0,
                "task": None,
                "instruction": None,
                "artifact_type": _open_type,
                "artifact_query": _open_query,
                "reason": "deterministic open request",
                "source": "open_intent",
            }

    # The planner decides for enrolled companies; everyone else takes the path
    # below, unchanged. Wrapped rather than trusted: this endpoint is on the
    # send path, and a planner import or flag read must never break a send.
    try:
        planned = _resolve_via_planner(
            enterprise_id, message, history, prd_id=prd_id, prd_title=prd_title,
            # Both referents an edit can act on: what the panel is showing, and
            # failing that what this thread produced. Neither reached the
            # planner before, which is why `edit_artifact` never fired.
            open_artifact=open_artifact, thread_artifact=thread_artifact,
        )
        if planned is not None:
            return planned
    except Exception:  # noqa: BLE001 — fall through to the resolver below
        logger.exception("planner-backed intent failed; using the intent resolver")

    # Not planner-backed. Skip the model entirely on a message that is plainly a
    # question and nothing else — the verdict would be `answer`, which is what
    # this returns.
    #
    # Ordered AFTER the planner deliberately, and it must stay there: for an
    # enrolled company the planner decides every message, and a gate that
    # resolved first would be precisely the second guesser this branch exists to
    # remove. It only ever runs for companies the planner has not been given.
    #
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
    except Exception as exc:  # noqa: BLE001 — dispatch must never break the send
        logger.exception("chat intent resolve failed; falling back to answer")
        return _fallback("resolver error", exc)
