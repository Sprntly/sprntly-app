"""Best-effort on-join greeting for a newly-added project member.

Drops ONE `role='assistant'` turn into the new member's individual project
chat (get-or-create) the moment they're added
(`routes/projects.py::add_member`'s new-membership branch, TIER_WORKSPACE/
TIER_COMPANY) — so they land with context instead of a blank thread. Framed
by the project's already-synthesized memory summary
(`project_memory_entries.get_summary`, REUSE — NO fresh LLM call in this
path; a Gate-1 DRY check, and a non-issue for the shared API-key budget,
`[[project_shared-api-key-credit-exhaustion-recurring]]`). Works with or
without a seeded summary: `get_summary` returning nothing (or no
`summary_md`) yields the no-summary fallback greeting below.

Best-effort by contract (AD-P7): never raises into the invite flow. A
greeting failure never breaks or delays `add_member` — the member is added
either way, and a re-add (the `TIER_MEMBER` branch, which returns before
this is ever called) never posts a duplicate.
"""
from __future__ import annotations

import logging
import re

from app.db import conversations as conversations_db
from app.db import project_memory_entries as memory_db
from app.db.projects import get_project

logger = logging.getLogger(__name__)

# An HTML comment — inert if it were ever rendered raw (it never is:
# `ProjectIndividualChat.tsx`'s `AgentTurnBody` looks for it and splits the
# turn into a visible lead + a Show more/less toggle over the rest).
MORE_MARKER = "<!--more-->"

# Soft cap on the visible lead, in characters — keeps the greeting skimmable
# without ever cutting a sentence in half.
_LEAD_TARGET_CHARS = 320


def _split_lead(summary_md: str) -> tuple[str, str]:
    """Split a synthesized summary into `(lead, rest)` for the
    `<!--more-->` split.

    Prefers a natural paragraph break when the first paragraph is a
    reasonable lead length (`<= _LEAD_TARGET_CHARS * 1.5`); otherwise
    accumulates whole sentences up to the soft cap, never cutting
    mid-sentence. A summary short enough to be entirely the lead returns an
    empty `rest` — the caller then omits the expand marker."""
    text = summary_md.strip()
    if not text:
        return "", ""

    paragraphs = re.split(r"\n\s*\n", text, maxsplit=1)
    first_para = paragraphs[0].strip()
    if first_para and len(first_para) <= _LEAD_TARGET_CHARS * 1.5:
        rest = text[len(paragraphs[0]):].strip()
        return first_para, rest

    sentences = re.split(r"(?<=[.!?])\s+", text)
    lead_parts: list[str] = []
    consumed = 0
    for sentence in sentences:
        if lead_parts and consumed + len(sentence) > _LEAD_TARGET_CHARS:
            break
        lead_parts.append(sentence)
        consumed += len(sentence) + 1

    lead = " ".join(lead_parts).strip()
    if not lead:
        # A single sentence longer than the cap — keep it whole rather than
        # cut it mid-sentence; nothing is left over to expand.
        return text, ""
    rest = text[len(lead):].strip()
    return lead, rest


def _compose_greeting(project_name: str, summary_md: str | None) -> str:
    """Deterministic greeting body.

    With a summary: a short framing line + the summary's lead gist visible
    inline, the rest behind `MORE_MARKER` (omitted when the whole summary
    already fits in the lead). Without one: a light, honest greeting that
    points at the group chat — it never fabricates a "why" or an
    assignment."""
    name = project_name.strip() or "this project"
    if not summary_md or not summary_md.strip():
        return (
            f"Hey — you're on {name} now. Nothing's been captured here yet, "
            "so ask me anything about the project, or check the group chat "
            "to catch up on what the team's been discussing."
        )

    lead, rest = _split_lead(summary_md)
    intro = f"Hey — you're on {name} now. Here's what I know so far:"
    if not rest:
        return f"{intro}\n\n{lead}"
    return f"{intro}\n\n{lead}{MORE_MARKER}{rest}"


def post_join_greeting(project_id: int, user_id: str) -> None:
    """Best-effort/never-raises (AD-P7): compose and post ONE grounding
    assistant turn into `user_id`'s individual chat for `project_id`.

    Called from `add_member`'s new-membership branch ONLY (`TIER_WORKSPACE`/
    `TIER_COMPANY`) — the `TIER_MEMBER` re-add branch returns before it, so a
    re-add never posts a duplicate greeting."""
    try:
        project = get_project(project_id)
        if not project:
            logger.warning("join_greeting_no_project project_id=%s", project_id)
            return
        summary = memory_db.get_summary(project_id) or {}  # REUSE — no fresh LLM
        content = _compose_greeting(project.get("name") or "", summary.get("summary_md"))
        conversation = conversations_db.create_individual_project_chat(project_id, user_id)
        turn = conversations_db.post_individual_turn(conversation["id"], "assistant", content)
        logger.info(
            "join_greeting_posted project_id=%s conversation_id=%s turn_id=%s had_summary=%s",
            project_id, conversation["id"], turn.get("id"), bool(summary.get("summary_md")),
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, AD-P7
        logger.warning(
            "join_greeting_failed project_id=%s error=%s", project_id, type(exc).__name__,
        )
