"""Best-effort on-join greeting for a newly-added project member.

Drops ONE `role='assistant'` turn into the new member's individual project
chat (get-or-create) the moment they're added — from BOTH mutation surfaces
that grow a project's roster: `routes/projects.py::add_member` (the
`POST /{project_id}/members` route, TIER_WORKSPACE/TIER_COMPANY new-
membership branch) and `tag_candidate_route` (the `POST /{project_id}/tag`
route's TIER_WORKSPACE branch) — so a member added via either the explicit
add-by-email flow or the @mention/tag flow lands with context instead of a
blank thread.

The greeting is a deterministic digest, composed entirely from already-
persisted reads (project memory summary, recent group-chat turns, the
artifact manifest, the roster, and the new member's own open delegations) —
NO fresh LLM call anywhere in this path (a Gate-1 DRY/cost check, and a
non-issue for the shared API-key budget,
`[[project_shared-api-key-credit-exhaustion-recurring]]`). Sections whose
underlying data is empty are simply omitted, except "For you", which always
renders (either the open items, or an honest "nothing assigned yet"). A
project with no summary, at most one artifact, and no group turns yet
degrades to a light, honest greeting that never fabricates a "why" or an
assignment.

Best-effort by contract (AD-P7): never raises into the invite/tag flow. A
greeting failure never breaks or delays the mutation it's attached to — the
member is added either way, and a re-add (`add_member`'s TIER_MEMBER branch,
`tag_candidate_route`'s TIER_MEMBER branch) never posts a duplicate, since
neither branch calls this at all.
"""
from __future__ import annotations

import logging
import re

from app.db import conversations as conversations_db
from app.db import delegation_events as delegation_events_db
from app.db import profiles as profiles_db
from app.db import project_memory_entries as memory_db
from app.db import projects as projects_db
from app.db.artifacts import list_artifacts_for_project
from app.db.projects import get_project

logger = logging.getLogger(__name__)

# An HTML comment — inert if it were ever rendered raw (it never is:
# `ProjectIndividualChat.tsx`'s `AgentTurnBody` looks for it and splits the
# turn into a visible lead + a Show more/less toggle over the rest).
MORE_MARKER = "<!--more-->"

# Soft cap on the visible lead, in characters — keeps the greeting skimmable
# without ever cutting a sentence in half.
_LEAD_TARGET_CHARS = 320

# How many recent group-chat turns feed the "what the team's been
# discussing" digest, and how long each excerpt is allowed to run — a
# skimmable few lines, not a transcript dump.
_GROUP_DIGEST_TURNS = 5
_GROUP_EXCERPT_CHARS = 120

# Flat artifact list cap — beyond this, the remainder is summarized as a
# single "…and N more" line rather than growing the greeting unboundedly.
_ARTIFACTS_CAP = 8

# How many of the new member's own open delegations to list under "For you"
# before summarizing the remainder is not needed here (the ledger already
# caps a single project's open assignments in practice), but this keeps the
# greeting bounded even if it doesn't.
_FOR_YOU_CAP = 5

_ARTIFACT_LABELS = {
    "prd": "PRD",
    "prototype": "Prototype",
    "evidence": "Evidence",
    "report": "Report",
    "ticket_set": "Ticket set",
    "custom_artifact": "Document",
}


def _artifact_label(artifact_type: str | None) -> str:
    return _ARTIFACT_LABELS.get(artifact_type, (artifact_type or "artifact").capitalize())


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


def _excerpt(text: str, cap: int) -> str:
    """Collapse whitespace/newlines and clip to `cap` chars — a one-line
    excerpt suitable for a digest bullet, never a multi-paragraph dump."""
    collapsed = " ".join((text or "").split())
    if not collapsed:
        return ""
    if len(collapsed) <= cap:
        return collapsed
    return collapsed[:cap].rstrip() + "…"


def _group_discussion_digest(project_id: int) -> str:
    """"<Name>: <excerpt>" bullets for the last few group-chat turns, or ""
    when the project has no group chat yet or it has zero turns. Best-
    effort: any read failure degrades to no digest rather than raising."""
    try:
        group_chat = conversations_db.get_group_chat(project_id)
        if not group_chat:
            return ""
        turns = conversations_db.list_group_turns(group_chat["id"])
    except Exception:  # noqa: BLE001 — best-effort
        return ""
    if not turns:
        return ""
    lines = []
    for turn in turns[-_GROUP_DIGEST_TURNS:]:
        author = (turn.get("author_name") or "Someone").strip() or "Someone"
        excerpt = _excerpt(turn.get("content") or "", _GROUP_EXCERPT_CHARS)
        if excerpt:
            lines.append(f"- {author}: {excerpt}")
    return "\n".join(lines)


def _artifacts_section(artifacts: list[dict]) -> str:
    if not artifacts:
        return ""
    shown = artifacts[:_ARTIFACTS_CAP]
    lines = [
        f"- {_artifact_label(item.get('type'))} — {(item.get('title') or 'Untitled').strip()}"
        for item in shown
    ]
    overflow = len(artifacts) - len(shown)
    if overflow > 0:
        lines.append(f"- …and {overflow} more")
    return f"**Artifacts to review ({len(artifacts)})**\n" + "\n".join(lines)


def _who_section(members: list[dict], new_user_id: str) -> str:
    if not members:
        return ""
    lines = []
    for member in members:
        name = (member.get("name") or "A teammate").strip()
        role = (member.get("job_role") or "").strip()
        tag = " (you)" if member.get("user_id") == new_user_id else ""
        line = f"- {name}{tag} — {role}" if role else f"- {name}{tag}"
        lines.append(line)
    return "**Who's on it**\n" + "\n".join(lines)


def _for_you_section(open_assigned: list[dict]) -> str:
    """Always renders — the caller never omits this section, unlike the
    others: "nothing assigned" is itself the useful answer to "is there
    anything for me?", not an empty section to hide."""
    if not open_assigned:
        return "**For you**\nNothing assigned yet."
    lines = [
        f"- {(row.get('task_summary') or '(no summary)').strip()} ({row.get('status')})"
        for row in open_assigned[:_FOR_YOU_CAP]
    ]
    return "**For you**\n" + "\n".join(lines)


_CTA = "Ask me anything about the project, or open the group chat to catch up."


def _intro_line(first_name: str, project_name: str) -> str:
    greet = f"Hey {first_name.strip()}" if first_name and first_name.strip() else "Hey"
    return f"{greet} — welcome to **{project_name}**. Here's what you'd want to know to jump in:"


def _brand_new_greeting(first_name: str, project_name: str, artifacts: list[dict]) -> str:
    """The honest light greeting for a project with no summary, at most one
    artifact, and no group-chat turns yet — never fabricates a "why" or an
    assignment."""
    greet = f"Hey {first_name.strip()}" if first_name and first_name.strip() else "Hey"
    if artifacts:
        item = artifacts[0]
        title = (item.get("title") or "Untitled").strip()
        captured = f"one {_artifact_label(item.get('type'))} — {title}"
    else:
        captured = "nothing"
    return (
        f"{greet} — welcome to **{project_name}**. It's brand new: {captured} captured so far. "
        "Ask me anything about the project, or check the group chat as the team fills it in."
    )


def _compose_greeting(
    *,
    project_name: str,
    first_name: str,
    summary_md: str,
    group_digest: str,
    artifacts: list[dict],
    members: list[dict],
    new_user_id: str,
    open_assigned: list[dict],
) -> str:
    """Deterministic, section-shaped greeting body.

    Visible lead: the intro line plus (when there's a summary) its gist via
    `_split_lead`. Everything else — the rest of the summary, the recent
    group-chat digest, the artifact list, the roster, and "For you" — sits
    behind `MORE_MARKER`, mirroring the existing show-more/less split.
    Sections whose data is empty are omitted entirely, except "For you"
    (see `_for_you_section`). Degrades to the honest brand-new greeting
    (no marker, no fabricated content) when there's no summary, at most one
    artifact, and no group-chat activity yet."""
    name = project_name.strip() or "this project"

    if not summary_md.strip() and len(artifacts) <= 1 and not group_digest:
        return _brand_new_greeting(first_name, name, artifacts)

    intro = _intro_line(first_name, name)
    solving_lead, solving_rest = _split_lead(summary_md) if summary_md.strip() else ("", "")

    lead_parts = [intro]
    if solving_lead:
        lead_parts.append(f"**What we're solving**\n\n{solving_lead}")
    lead_block = "\n\n".join(lead_parts)

    rest_parts: list[str] = []
    if solving_rest:
        rest_parts.append(solving_rest)
    team_section = _group_discussion_section(group_digest)
    if team_section:
        rest_parts.append(team_section)
    artifacts_section = _artifacts_section(artifacts)
    if artifacts_section:
        rest_parts.append(artifacts_section)
    who_section = _who_section(members, new_user_id)
    if who_section:
        rest_parts.append(who_section)
    rest_parts.append(_for_you_section(open_assigned))  # always present
    rest_parts.append(_CTA)

    rest = "\n\n".join(rest_parts)
    if not rest:
        return lead_block
    return f"{lead_block}{MORE_MARKER}{rest}"


def _group_discussion_section(digest: str) -> str:
    if not digest:
        return ""
    return "**What the team's been discussing**\n" + digest


def post_join_greeting(project_id: int, user_id: str, dataset: str, company_id: str) -> None:
    """Best-effort/never-raises (AD-P7): compose and post ONE grounding
    assistant turn into `user_id`'s individual chat for `project_id`.

    Called from BOTH `add_member`'s new-membership branch (`TIER_WORKSPACE`/
    `TIER_COMPANY`) and `tag_candidate_route`'s `TIER_WORKSPACE` branch — the
    re-add/notify-only branches on each route return before either call, so
    a re-add never posts a duplicate greeting. `dataset`/`company_id` scope
    the artifact-manifest read the same way every other project route scopes
    it (`_dataset_for(ctx)`, `ctx.company_id`)."""
    try:
        project = get_project(project_id)
        if not project:
            logger.warning("join_greeting_no_project project_id=%s", project_id)
            return
        project_name = project.get("name") or ""

        try:
            first_name = profiles_db.first_name_for_user(user_id) or ""
        except Exception:  # noqa: BLE001 — personalisation is best-effort
            first_name = ""

        summary = memory_db.get_summary(project_id) or {}  # REUSE — no fresh LLM
        summary_md = (summary.get("summary_md") or "").strip()

        group_digest = _group_discussion_digest(project_id)

        try:
            artifacts = list_artifacts_for_project(
                project_id=project_id, dataset=dataset, company_id=company_id
            )
        except Exception:  # noqa: BLE001 — best-effort
            artifacts = []

        try:
            members = projects_db.list_members(project_id)
        except Exception:  # noqa: BLE001 — best-effort
            members = []

        try:
            assigned_rows = delegation_events_db.list_status_for_assignee(project_id, user_id)
        except Exception:  # noqa: BLE001 — best-effort
            assigned_rows = []
        open_assigned = [
            row for row in assigned_rows if row.get("status") in delegation_events_db.OPEN_STATES
        ]

        content = _compose_greeting(
            project_name=project_name,
            first_name=first_name,
            summary_md=summary_md,
            group_digest=group_digest,
            artifacts=artifacts,
            members=members,
            new_user_id=user_id,
            open_assigned=open_assigned,
        )
        conversation = conversations_db.create_individual_project_chat(project_id, user_id)
        turn = conversations_db.post_individual_turn(conversation["id"], "assistant", content)
        logger.info(
            "join_greeting_posted project_id=%s conversation_id=%s turn_id=%s had_summary=%s",
            project_id, conversation["id"], turn.get("id"), bool(summary_md),
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, AD-P7
        logger.warning(
            "join_greeting_failed project_id=%s error=%s", project_id, type(exc).__name__,
        )
