"""Best-effort on-join greeting for a newly-added project member.

Drops ONE `role='assistant'` turn into the new member's individual project
chat (get-or-create) the moment they're added — from BOTH mutation surfaces
that grow a project's roster: `routes/projects.py::add_member` (the
`POST /{project_id}/members` route, TIER_WORKSPACE/TIER_COMPANY new-
membership branch) and `tag_candidate_route` (the `POST /{project_id}/tag`
route's TIER_WORKSPACE branch) — so a member added via either the explicit
add-by-email flow or the @mention/tag flow lands with context instead of a
blank thread.

Private-first memory wave: with the shared team-thread surface removed, the
substrate a newcomer relies on to see "what the whole team did" is project
MEMORY, not a shared-turn digest. The greeting is now ONE narrative LLM pass
(David's item-#5 newcomer-brief shape — origin/why, problem statement, what's been
done, artifacts, members, open questions, the new member's own assigned
items, and a closing invitation) composed from already-persisted reads: the
project's cached memory summary (REUSE — `memory_db.get_summary`, no fresh
memory-SYNTHESIS call; that cache is written by `project_memory.py`'s own
regen loop, not here), the artifact manifest, the roster, and the new
member's own open delegations. This is the module's FIRST LLM call (a
deliberate, plan-sanctioned behaviour change — one call per greeting, on
member-add only, never per chat-open); before this wave the greeting was a
deterministic digest with no LLM call at all. On any LLM failure or an
empty response, `_fallback_greeting` — a deterministic, honest fallback —
is posted instead, so a member-add ALWAYS gets a greeting.

Best-effort by contract (AD-P7): never raises into the invite/tag flow. A
greeting failure never breaks or delays the mutation it's attached to — the
member is added either way, and a re-add (`add_member`'s TIER_MEMBER branch,
`tag_candidate_route`'s TIER_MEMBER branch) never posts a duplicate, since
neither branch calls this at all.
"""
from __future__ import annotations

import logging
import re
import time

from app.db import conversations as conversations_db
from app.db import delegation_events as delegation_events_db
from app.db import profiles as profiles_db
from app.db import project_memory_entries as memory_db
from app.db import projects as projects_db
from app.db.artifacts import list_artifacts_for_project
from app.db.projects import get_project
from app.llm import DEFAULT_MODEL, call_md
from app.llm_telemetry import RunUsage, log_llm_run

logger = logging.getLogger(__name__)

# An HTML comment — inert if it were ever rendered raw (it never is: the
# frontend's `GreetingTurnBody.tsx` (`web/…/projects/`) looks for it and
# splits the turn into a visible lead + a Show more/less toggle over the
# rest — `chat-shell/types.ts`'s `MORE_MARKER` constant matches this value
# byte-for-byte).
MORE_MARKER = "<!--more-->"

# Soft cap on the visible lead, in characters — used only by `_ensure_marker`
# below (the safety net for a model response that omits the marker) to keep
# the lead skimmable without ever cutting a sentence in half.
_LEAD_TARGET_CHARS = 320

# Flat artifact list cap fed to the prompt — beyond this, the remainder is
# summarized as a single "…and N more" line rather than growing the prompt
# (and the greeting) unboundedly.
_ARTIFACTS_CAP = 8

# How many of the new member's own open delegations are fed to the prompt.
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
    """Split text into `(lead, rest)` for the `<!--more-->` split.

    Prefers a natural paragraph break when the first paragraph is a
    reasonable lead length (`<= _LEAD_TARGET_CHARS * 1.5`); otherwise
    accumulates whole sentences up to the soft cap, never cutting
    mid-sentence. Text short enough to be entirely the lead returns an
    empty `rest`. Used by `_ensure_marker` below as the fallback split when
    the model's own response omits the literal marker."""
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


def _ensure_marker(body: str) -> str:
    """Guarantee the LITERAL `MORE_MARKER` appears in `body`, even when the
    model didn't follow the formatting rule exactly — `GreetingTurnBody.tsx`'s
    lead/show-more split must never break just because a response omitted
    it. Already-present is left untouched (whatever the model wrote,
    verbatim); missing is patched by splitting the body the same way the
    pre-LLM deterministic composer split a summary (`_split_lead`). A body
    too short to have a meaningful "rest" is returned as-is, unmarked — no
    show-more needed for a one-line greeting."""
    if MORE_MARKER in body:
        return body
    lead, rest = _split_lead(body)
    if not rest:
        return body
    return f"{lead}{MORE_MARKER}{rest}"


def _fallback_greeting(first_name: str, project_name: str, artifacts: list[dict]) -> str:
    """The deterministic, honest greeting used when the LLM pass fails or
    returns an empty response — renamed/repurposed from the pre-LLM
    module's `_brand_new_greeting` body, now the module's ONE fallback floor
    (previously it was reached only for a genuinely brand-new project; the
    narrative LLM pass now handles that case on its own, so this is reached
    only on an LLM failure/empty response, regardless of how populated the
    project actually is). Never fabricates a "why" or an assignment — states
    only what's actually captured. Private-safe: points the member at asking
    the agent directly, never at a group thread."""
    greet = f"Hey {first_name.strip()}" if first_name and first_name.strip() else "Hey"
    if artifacts:
        item = artifacts[0]
        title = (item.get("title") or "Untitled").strip()
        captured = f"one {_artifact_label(item.get('type'))} — {title}"
    else:
        captured = "nothing"
    return (
        f"{greet} — welcome to **{project_name}**. Here's what's captured so far: "
        f"{captured}. Ask me anything about the project any time."
    )


_GREETING_SYSTEM = f"""You write the on-join greeting for a teammate who was \
just added to a Sprntly project. Compose ONE narrative message, GROUNDED \
STRICTLY in the material given below — never invent a decision, an \
artifact, a teammate, or an assignment that isn't actually present in it. A \
project with little material yet gets an honest, short greeting — never a \
padded one.

Cover each of these points, in order, when the material actually supports \
it — skip a point silently when there's nothing for it, never fill it with \
an invented placeholder:

1. Why this project exists and, in plain terms a reader with ZERO context \
could follow, the problem it's solving — drawn from the project memory \
summary (which already carries the project's origin/goal when one was \
captured).
2. What's been done so far, from the project memory summary.
3. The artifacts already captured (name each one — title and type).
4. Who else is on the project.
5. Any open questions the memory summary flags as still unresolved.
6. The NEW member's OWN assigned/open items, called out as belonging to \
THEM specifically ("you're assigned to…") — never as a generic team list. \
If nothing is assigned to them yet, say that honestly ("nothing assigned \
to you yet") rather than inventing one.
7. Close with a short, honest invitation to ask for more detail on any of \
the above.

Formatting (hard rules):
- Open with "Hey <first name> —" (or just "Hey —" when no first name is \
given), welcoming them to the project by name.
- Points 1-2 are the LEAD: one to three short sentences, the zero-context \
problem statement. Immediately after the lead, insert the LITERAL marker \
`{MORE_MARKER}` on its own — exactly this text, exactly once, no \
surrounding decoration — then continue with points 3-7 after it.
- Plain prose; no markdown headings. A short bullet list is fine for the \
artifacts/members/assigned-items points if that reads more clearly.
- Never reference a shared team-thread, group conversation, or any surface \
other than this private chat — every reference to asking for more is "ask \
me" or "the project", said directly to this one reader.
"""


def _render_artifacts_for_prompt(artifacts: list[dict]) -> str:
    if not artifacts:
        return "(none captured yet)"
    shown = artifacts[:_ARTIFACTS_CAP]
    lines = [
        f"- {_artifact_label(item.get('type'))} — {(item.get('title') or 'Untitled').strip()}"
        f" ({item.get('status') or 'status unknown'})"
        for item in shown
    ]
    overflow = len(artifacts) - len(shown)
    if overflow > 0:
        lines.append(f"- …and {overflow} more")
    return "\n".join(lines)


def _render_members_for_prompt(members: list[dict], new_user_id: str) -> str:
    if not members:
        return "(no other members listed)"
    lines = []
    for member in members:
        name = (member.get("name") or "A teammate").strip()
        role = (member.get("job_role") or "").strip()
        tag = " — this IS the new member being greeted" if member.get("user_id") == new_user_id else ""
        line = f"- {name} ({role}){tag}" if role else f"- {name}{tag}"
        lines.append(line)
    return "\n".join(lines)


def _render_delegations_for_prompt(open_assigned: list[dict]) -> str:
    if not open_assigned:
        return "(nothing currently assigned to the new member)"
    lines = [
        f"- {(row.get('task_summary') or '(no summary)').strip()} ({row.get('status')})"
        for row in open_assigned[:_FOR_YOU_CAP]
    ]
    return "\n".join(lines)


def _render_greeting_inputs(
    *,
    project_name: str,
    first_name: str,
    summary_md: str,
    artifacts: list[dict],
    members: list[dict],
    new_user_id: str,
    open_assigned: list[dict],
) -> str:
    return (
        f"Project name: {project_name}\n"
        f"New member's first name: {first_name or '(unknown)'}\n\n"
        f"Project memory summary (what the project knows so far — may be "
        f"empty for a brand-new project):\n"
        f"{summary_md.strip() or '(no memory recorded yet)'}\n\n"
        f"Artifacts captured:\n{_render_artifacts_for_prompt(artifacts)}\n\n"
        f"Project members:\n{_render_members_for_prompt(members, new_user_id)}\n\n"
        f"The new member's own open/assigned items:\n"
        f"{_render_delegations_for_prompt(open_assigned)}"
    )


def _log_greeting_run(
    *, project_id: int, user_id: str, meta: dict, start: float,
    status: str, error_class: str | None = None,
) -> None:
    """The one structured cost-summary line per greeting (identifiers only —
    `project_id`/`user_id`/`model`/tokens/`duration_ms`/`status`/
    `error_class`; NEVER the greeting body, memory content, or a member's
    name/email — observability minimum, `TICKET_STANDARD.md` §7). Never
    raises — a logging hiccup must never be the reason a greeting fails."""
    try:
        log_llm_run(
            operation="projects.greeting.compose",
            identifier={"project_id": project_id, "user_id": user_id},
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
    except Exception:  # noqa: BLE001 — observability must never break the greeting
        logger.warning(
            "join_greeting_cost_log_failed project_id=%s user_id=%s", project_id, user_id,
        )


def _compose_greeting_llm(
    *,
    project_id: int,
    user_id: str,
    project_name: str,
    first_name: str,
    summary_md: str,
    artifacts: list[dict],
    members: list[dict],
    open_assigned: list[dict],
) -> str:
    """The single narrative LLM pass (item-#5 brief). Never raises — always
    returns a string, "" on any failure or an empty model response, so the
    caller (`post_join_greeting`) can fall through to `_fallback_greeting`.
    One call per greeting, on member-add only (never per chat-open)."""
    start = time.monotonic()
    meta: dict = {}
    status = "complete"
    error_class: str | None = None
    body = ""
    try:
        body = call_md(
            system=_GREETING_SYSTEM,
            user=_render_greeting_inputs(
                project_name=project_name,
                first_name=first_name,
                summary_md=summary_md,
                artifacts=artifacts,
                members=members,
                new_user_id=user_id,
                open_assigned=open_assigned,
            ),
            model=DEFAULT_MODEL,
            meta_out=meta,
        )
        body = (body or "").strip()
        if not body:
            status = "fallback"
    except Exception as exc:  # noqa: BLE001 — caller falls back to _fallback_greeting
        status = "fallback"
        error_class = type(exc).__name__
        logger.warning(
            "join_greeting_compose_failed project_id=%s user_id=%s error=%s",
            project_id, user_id, error_class,
        )
    _log_greeting_run(
        project_id=project_id, user_id=user_id, meta=meta,
        start=start, status=status, error_class=error_class,
    )
    if not body:
        return ""
    return _ensure_marker(body)


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
        project_name = project.get("name") or "this project"

        try:
            first_name = profiles_db.first_name_for_user(user_id) or ""
        except Exception:  # noqa: BLE001 — personalisation is best-effort
            first_name = ""

        summary = memory_db.get_summary(project_id) or {}  # REUSE — no fresh synthesis call
        summary_md = (summary.get("summary_md") or "").strip()

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

        content = _compose_greeting_llm(
            project_id=project_id,
            user_id=user_id,
            project_name=project_name,
            first_name=first_name,
            summary_md=summary_md,
            artifacts=artifacts,
            members=members,
            open_assigned=open_assigned,
        )
        if not content:
            content = _fallback_greeting(first_name, project_name, artifacts)

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
