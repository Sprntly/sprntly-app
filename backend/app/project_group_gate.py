"""Smart-interjection should-respond gate (AD-P10) — the bounded classifier
that decides whether the group agent replies to a group turn with NO
explicit `@Sprntly` mention. The mention path
(`routes/projects.py::post_group_turn_route`, `_MENTION_RE`) is unchanged
and never calls this module.

There is no user-facing toggle — the agent decides (v3.4 retired the
Auto/mention-only setting). This module is the decider.

Contract (AD-P7 best-effort, mirrors `app.project_memory.maybe_promote_turn`
in shape): `should_respond` never raises. Any pre-filter/classifier/logging
failure returns False (STAY OUT) — the conservative default AD-P10 and
competitive-rec-2 require. A gate failure must never spuriously interject
and must never block the post: the human turn that triggered a gate
consult has already persisted by the time this runs.

Cost posture (R8): a cheap pre-filter short-circuits obvious human-to-human
turns (empty of a question mark, very short, no agent-directed cue) to
STAY OUT with NO classifier call at all — this is what keeps a burst of
"ok"/"thanks" turns from each spending a call. Everything else gets ONE
bounded `call_json` classifier call over the recent, clamped, speaker-
tagged transcript — the SAME rendering `_respond_as_group_agent` builds for
its own reply, factored into `render_group_transcript` below so neither
call site duplicates the loop. One `interjection_gate` cost-summary log
line per classifier call; none on the pre-filter path, since no call was
made there.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.llm import DEFAULT_MODEL, call_json
from app.llm_telemetry import RunUsage, log_llm_run

logger = logging.getLogger(__name__)

# Same clamp posture as `routes/projects.py::_GROUP_CONTEXT_TURNS` — bounds
# the transcript fed to the classifier so a long-running group chat can't
# grow the gate's prompt unboundedly. Kept as a separate constant (not an
# import) so this module has no import-time dependency on `routes.projects`
# — callers pass an already-clamped `recent_turns` list; this is a second,
# defensive clamp in case a caller ever passes more.
_GROUP_CONTEXT_TURNS = 30

_GATE_SYSTEM = """You are the should-respond gate for Sprntly, a project \
teammate embedded in a team's group chat. You are NOT replying — you are \
deciding whether Sprntly should interject on the LATEST turn below, which \
was posted with no explicit @Sprntly mention. Each line of the transcript \
is "Name (job role): message", oldest first; the latest line is the turn \
to decide on.

Respond true when the latest turn is a question or request addressed to no \
specific human, or is clearly directed at Sprntly / the agent even without \
the @ handle (for example: "can someone automate this?", "what's the \
status on the integration?", "is anyone tracking this bug?").

Respond false — stay out — when NOT to respond: ordinary human-to-human \
back-and-forth, a message @-addressed to another named human, \
acknowledgements ("thanks", "sounds good", "got it"), or ambiguous chatter \
with nothing concrete for Sprntly to act on. When genuinely unsure, the \
conservative default is false: stay out rather than interject uninvited.
"""

_GATE_SCHEMA = {
    "type": "object",
    "properties": {"respond": {"type": "boolean"}},
    "required": ["respond"],
    "additionalProperties": False,
}

# Pre-filter bound (R8): a turn with no question mark, no agent-directed
# cue, and at or under this many words is treated as an obvious trivial
# acknowledgement/chatter ("ok", "thanks", "np, will do") — skipped WITHOUT
# a classifier call. Deliberately narrow: the classifier handles everything
# longer or more ambiguous, this only bounds spend on the trivial case.
_PREFILTER_MAX_WORDS = 4
_AGENT_CUE_RE = re.compile(r"\bsprntly\b|\bagent\b", re.IGNORECASE)


def render_group_transcript(recent_turns: list[dict[str, Any]]) -> str:
    """Speaker-tagged transcript, one line per turn, oldest first — the
    SAME rendering `_respond_as_group_agent` builds for its reply prompt,
    factored out here so the gate and the reply path consult (and produce)
    the identical shape rather than duplicating the loop. Each line is
    "Name (job role): message" (or "Sprntly: message", no job role, for the
    agent's own prior turns)."""
    lines = []
    for turn in recent_turns:
        label = turn["author_name"] or "Someone"
        job_role = turn.get("author_job_role")
        if job_role:
            label = f"{label} ({job_role})"
        lines.append(f"{label}: {turn['content']}")
    return "\n".join(lines)


def _obviously_human_chatter(latest_content: str) -> bool:
    """Cheap pre-filter (R8): true for a short, question-free, agent-cue-
    free turn — the trivial-acknowledgement case ("ok"/"thanks"/"np").
    Deliberately narrow so the classifier still sees everything else,
    including longer human-to-human exchanges the prompt itself must
    learn to stay out of."""
    text = (latest_content or "").strip()
    if not text:
        return True
    if "?" in text:
        return False
    if _AGENT_CUE_RE.search(text):
        return False
    return len(text.split()) <= _PREFILTER_MAX_WORDS


def _log_gate_run(
    *,
    project_id: int,
    conversation_id: int,
    meta: dict,
    start: float,
    status: str,
    error_class: str | None = None,
) -> None:
    """The one structured cost-summary line per classifier call (AC6).
    Never raises — a logging hiccup must never be the reason the gate
    breaks."""
    try:
        log_llm_run(
            operation="projects.group_chat.interjection_gate",
            identifier={"project_id": project_id, "conversation_id": conversation_id},
            usage=RunUsage(
                cache_creation_input_tokens=meta.get("cache_creation_input_tokens", 0),
                cache_read_input_tokens=meta.get("cache_read_input_tokens", 0),
                input_tokens=meta.get("input_tokens", 0),
                output_tokens=meta.get("output_tokens", 0),
            ),
            duration_ms=int((time.monotonic() - start) * 1000),
            status=status,
            model=meta.get("model") or DEFAULT_MODEL,
            error_class=error_class,
        )
    except Exception:  # noqa: BLE001 — observability must never break the gate
        logger.warning(
            "interjection_gate_cost_log_failed project_id=%s conversation_id=%s",
            project_id, conversation_id,
        )


def should_respond(
    project_id: int,
    conversation_id: int,
    recent_turns: list[dict[str, Any]],
    latest_content: str,
) -> bool:
    """Whether the group agent should interject on a non-mention group
    turn. Never raises (AD-P7/AD-P10) — any pre-filter, classifier, or
    logging failure returns False, the conservative stay-out default. Only
    ever consulted by `post_group_turn_route` for a turn with NO explicit
    `@Sprntly` mention; the mention path is deterministic and never calls
    this.

    `recent_turns` is the caller's already-clamped, speaker-tagged turn
    list (the trailing `_GROUP_CONTEXT_TURNS`, as read via
    `conversations_db.list_group_turns` — which itself refuses a
    non-`kind='group'` conversation id, R4/AD-P2 isolation backstop). This
    function does not requery the DB.
    """
    if _obviously_human_chatter(latest_content):
        logger.info(
            "group_gate_decision project_id=%s conversation_id=%s "
            "respond=False reason=prefilter",
            project_id, conversation_id,
        )
        return False

    start = time.monotonic()
    meta: dict = {}
    try:
        transcript = render_group_transcript(recent_turns[-_GROUP_CONTEXT_TURNS:])
        out = call_json(
            system=_GATE_SYSTEM,
            user=transcript,
            model=DEFAULT_MODEL,
            schema=_GATE_SCHEMA,
            meta_out=meta,
        )
        respond = bool(out.get("respond"))
    except Exception as exc:  # noqa: BLE001 — best-effort, AD-P7/AD-P10: default stay-out
        logger.warning(
            "group_gate_classify_failed project_id=%s conversation_id=%s error=%s",
            project_id, conversation_id, type(exc).__name__,
        )
        _log_gate_run(
            project_id=project_id, conversation_id=conversation_id, meta=meta,
            start=start, status="error", error_class=type(exc).__name__,
        )
        return False

    _log_gate_run(
        project_id=project_id, conversation_id=conversation_id, meta=meta,
        start=start, status="complete",
    )
    logger.info(
        "group_gate_decision project_id=%s conversation_id=%s respond=%s reason=classifier",
        project_id, conversation_id, respond,
    )
    return respond
