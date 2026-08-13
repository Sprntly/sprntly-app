"""`delegate_task` tool + brief + gated cross-user delivery.

The defining Projects behavior: a member, in the group chat, asks Sprntly
to hand a task to a teammate ("send this to Fortune"), and Sprntly resolves
the assignee, writes a bounded best-effort brief, and delivers it into the
assignee's own private individual project chat — then confirms in the
group. This is the FIRST cross-user write in the product (a miss here is a
cross-user IDOR); the double-membership gate below is the load-bearing
invariant.

Structured like `project_memory.py::maybe_promote_turn`: `handle_delegate_task`
is the best-effort, never-raising entry point, called as the `dispatch`
target for the group agent's `run_tool_loop` (`routes/projects.py::
_respond_as_group_agent`). Zero new LLM calls for the resolve/gate/deliver
path — the ONE extra call is `_build_brief`'s bounded `call_md`, made only
once resolution + both membership gates have already passed.

Fail-closed throughout: nothing is ever half-delivered. `no_match`/
`ambiguous` resolution, a failed membership re-check, or a failed brief
all return a decline string with NO turn and NO `project_delegations` row
written. Delivery-then-record ordering (AD-P19): the turn is written
first, the fact second — if the turn write fails, the fact is never
reached, so a delegation fact can never exist without a real delivered
turn behind it.
"""
from __future__ import annotations

import logging
import time

from app.db.artifacts import list_artifacts_for_project
from app.db.conversations import (
    create_individual_project_chat,
    list_individual_turns,
    post_individual_turn,
)
from app.db.delegation_events import record_event, status_dto
from app.db.project_delegations import record_delegation
from app.db.projects import is_project_member, resolve_member
from app.llm import DEFAULT_MODEL, call_md
from app.llm_telemetry import RunUsage, log_llm_run
from app.project_context import assemble_project_context
from app.realtime import publish_broadcast

logger = logging.getLogger(__name__)

# The exact `list_individual_turns` read-DTO key set — a hard whitelist
# applied before every `brief.delivered` broadcast (AD-P21 no-schema-
# coupling), so an internal `conversation_turns` column can never ride
# along on the wire even if a future column is added to the table.
_BRIEF_TURN_DTO_KEYS = ("id", "role", "content", "created_at")


def _publish_brief_delivered(
    project_id: int, assignee_user_id: str, conversation_id: int, turn: dict
) -> None:
    """Best-effort publish-on-write for a delivered brief. The re-read
    (`list_individual_turns`) that shapes the DTO, the shaping itself, AND
    `publish_broadcast` are ALL swallowed here (AD-P22): by the time this
    is called the brief turn AND the `project_delegations` fact have
    already been written, so a transient re-read hiccup must never make
    `handle_delegate_task` report the decline string over a delivery that
    actually succeeded."""
    try:
        shaped = list_individual_turns(conversation_id, assignee_user_id, since=turn["id"] - 1)
        dto = next((t for t in shaped if t["id"] == turn["id"]), None)
        if dto is not None:
            publish_broadcast(
                f"project:{project_id}:user:{assignee_user_id}",
                "brief.delivered",
                {k: dto[k] for k in _BRIEF_TURN_DTO_KEYS},
            )
            logger.info(
                "brief_broadcast_published project_id=%s assignee=%s conversation_id=%s",
                project_id, assignee_user_id, conversation_id,
            )
    except Exception as exc:  # noqa: BLE001 — best-effort, AD-P22: realtime-prep never masks a successful delivery
        logger.warning(
            "realtime_publish_prep_failed topic=project:%s:user:%s event=brief.delivered "
            "error_class=%s",
            project_id, assignee_user_id, type(exc).__name__,
        )

# The exact status-DTO key set (`v_delegation_status` client shape) — a hard
# whitelist applied before every `delegation.event` broadcast (AD-P21 no-
# schema-coupling), the sibling of `_BRIEF_TURN_DTO_KEYS` above.
_DELEGATION_EVENT_DTO_KEYS = ("delegation_id", "status", "status_at", "task_summary")


def _publish_delegation_event(
    *, project_id: int, assigner_user_id: str, assignee_user_id: str, dto: dict
) -> None:
    """Best-effort: publish the SHAPED delegation-status DTO (never a raw row,
    AD-P21) to BOTH affected parties' per-user channels — the two people who
    care. NEVER the group channel `project:{id}` (a decline/cancel is private,
    AD-P30). A failed publish degrades to the client's next reconcile/refetch
    (AD-P22); `publish_broadcast` itself already never raises.

    Mirrors `_publish_brief_delivered`'s per-user-channel publish shape, but
    fans OUT to both parties where the brief helper is single-target/assignee-
    only — the ledger event concerns the assigner and the assignee alike."""
    shaped = {k: dto[k] for k in _DELEGATION_EVENT_DTO_KEYS}
    for uid in (assigner_user_id, assignee_user_id):
        publish_broadcast(f"project:{project_id}:user:{uid}", "delegation.event", shaped)


# The mention/add liveness-signal DTO whitelist — the sibling of
# `_DELEGATION_EVENT_DTO_KEYS`/`_BRIEF_TURN_DTO_KEYS` above, applied before
# every `mention.received`/`member.added` broadcast. `kind` ∈ {"mentioned",
# "added"}. Carries NO message text, NO brief, NO artifact, NO member list
# (AD-TNM2) — a private nudge is ids + names only, never project content.
_MENTION_SIGNAL_DTO_KEYS = ("project_id", "project_name", "actor_name", "kind")


def _publish_member_added(
    project_id: int, target_user_id: str, project_name: str | None
) -> None:
    """Best-effort publish-on-write of a `member.added` liveness signal to the
    added person's OWN per-user channel `project:{id}:user:{uid}` — NEVER the
    group channel `project:{id}` (a landing signal is private to the recipient,
    AD-TNM2/AD-P30). Entirely swallowed (AD-P22): by the time this is called
    the `project_members` write has already committed, so a realtime hiccup —
    or a DTO-shaping error — must never make the tag route / accept hook report
    failure or roll back a membership that succeeded. Mirrors
    `_publish_brief_delivered`'s swallow shape."""
    try:
        dto = {
            "project_id": project_id,
            "project_name": project_name,
            "actor_name": None,
            "kind": "added",
        }
        publish_broadcast(
            f"project:{project_id}:user:{target_user_id}",
            "member.added",
            {k: dto[k] for k in _MENTION_SIGNAL_DTO_KEYS},
        )
        logger.info(
            "member_added_signal_published project_id=%s target_user_id=%s",
            project_id, target_user_id,
        )  # ids only — never the added person's name or any project content
    except Exception as exc:  # noqa: BLE001 — best-effort, AD-P22: never fails/rolls back the write
        logger.warning(
            "realtime_publish_prep_failed topic=project:%s:user:%s event=member.added "
            "error_class=%s",
            project_id, target_user_id, type(exc).__name__,
        )


def _publish_mention_signal(
    project_id: int, target_user_id: str, actor_name: str | None, project_name: str | None
) -> None:
    """Best-effort publish-on-write of a `mention.received` signal to the
    MENTIONED member's OWN per-user channel — NEVER the group channel (a
    "you were mentioned" nudge is private to the recipient, AD-TNM2/AD-P30).
    Carries the actor's display name for the recipient-side affordance but NO
    message text and NO project content. Entirely swallowed (AD-P22): a
    notify-only tag response must never fail or change because a realtime
    publish hiccuped. Mirrors `_publish_member_added`."""
    try:
        dto = {
            "project_id": project_id,
            "project_name": project_name,
            "actor_name": actor_name,
            "kind": "mentioned",
        }
        publish_broadcast(
            f"project:{project_id}:user:{target_user_id}",
            "mention.received",
            {k: dto[k] for k in _MENTION_SIGNAL_DTO_KEYS},
        )
        logger.info(
            "mention_signal_published project_id=%s target_user_id=%s",
            project_id, target_user_id,
        )  # ids only — never actor/target names or any project content (AD-TNM2)
    except Exception as exc:  # noqa: BLE001 — best-effort, AD-P22: never fails/changes the tag response
        logger.warning(
            "realtime_publish_prep_failed topic=project:%s:user:%s event=mention.received "
            "error_class=%s",
            project_id, target_user_id, type(exc).__name__,
        )


# How many of the project's most-recently-touched artifacts to fold into
# the brief — bounded the same way the reply path bounds its own group
# transcript (`_GROUP_CONTEXT_TURNS`), so a heavily-artifacted project
# can't grow the brief prompt unboundedly.
_ARTIFACT_LIMIT = 5

DELEGATE_TASK_TOOL = {
    "name": "delegate_task",
    "description": (
        "Call this when a member asks you to hand a specific task off to a "
        "teammate — by name, @handle, or role (\"send this to Fortune\", "
        "\"give it to the designer\", \"loop Fortune in on the pricing work\"). "
        "Pick the assignee from the PROJECT ROSTER given below in this system "
        "prompt; use the person's name or role exactly as it appears there. "
        "Do NOT call this for a plain question to answer, an FYI to the room, "
        "brainstorming, or when two members are talking to each other and not "
        "asking you to route work. If you are unsure who is meant, do NOT guess "
        "— reply asking who they mean instead of calling this tool."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "assignee": {
                "type": "string",
                "description": (
                    "the teammate to hand the task to, as named by the asker (a "
                    "name, @handle, or role like 'the designer'), chosen from "
                    "the roster"
                ),
            },
            "task_summary": {
                "type": "string",
                "description": (
                    "a concise, self-contained statement of the specific task "
                    "being handed off, extracted from the conversation"
                ),
            },
        },
        "required": ["assignee", "task_summary"],
        "additionalProperties": False,
    },
}

_BRIEF_SYSTEM = """\
You write a short brief handing a task from one project teammate to
another. It is delivered straight into the assignee's private chat with
Sprntly — a one-way notification, not a live conversation turn a reply
could land on.

Write in plain prose (short paragraphs or a few brief bullets — no
heading dump). State, in this order:
  - the task itself, clearly and concretely
  - who assigned it — name AND role — so the assignee knows who to ask
    for more context
  - the relevant project context and any linked artifacts given below,
    ONLY what is actually provided — never invent a fact, a name, a
    deadline, or an artifact that is not in the material given to you

End the brief on a plain statement. Never end on a question, and never
end with an offer ("let me know if you need anything", "want me to dig
in further?", or similar) — there is no live turn here for a reply to
land on.
"""


def _log_brief_run(
    *,
    project_id: int,
    assignee_user_id: str | None,
    meta: dict,
    start: float,
    status: str,
    error_class: str | None = None,
) -> None:
    """The one structured cost-summary line per brief call (R8/AD-P15).
    Never raises — a logging hiccup must never be the reason a delegation
    fails."""
    try:
        log_llm_run(
            operation="projects.delegation.brief",
            identifier={"project_id": project_id, "assignee_user_id": assignee_user_id},
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
    except Exception:  # noqa: BLE001 — observability must never break delivery
        logger.warning("delegation_brief_cost_log_failed project_id=%s", project_id)


def _render_brief_user(
    task_summary: str,
    assigner: dict | None,
    assignee: dict,
    context_block: str,
    artifacts: list[dict],
) -> str:
    assigner_name = (assigner or {}).get("name") or "a teammate"
    assigner_role = (assigner or {}).get("job_role") or "no role set"
    assignee_name = assignee.get("name") or "there"

    lines = [
        f"Task: {task_summary}",
        f"Assigned by: {assigner_name} ({assigner_role})",
        f"Assigned to: {assignee_name}",
    ]
    if context_block:
        lines.append(f"\nProject context:\n{context_block}")
    if artifacts:
        art_lines = "\n".join(
            f"- {a.get('title') or a.get('type')} ({a.get('type')})"
            for a in artifacts
        )
        lines.append(f"\nLinked artifacts:\n{art_lines}")
    return "\n".join(lines)


def _build_brief(
    project_id: int,
    assigner_user_id: str | None,
    assignee: dict,
    task_summary: str,
    roster: list[dict],
    dataset: str,
    company_id: str,
) -> str | None:
    """ONE bounded, best-effort `call_md` (AD-P19). Reuses
    `assemble_project_context` with the **assigner's** user_id (R-D7 —
    the folded role line reads as context about who is asking) and the
    project's own artifact fan-out. Context/artifact folding degrades
    silently to "none" on a read failure — only the LLM call itself can
    fail the brief outright (returns None, never raises)."""
    start = time.monotonic()
    meta: dict = {}
    assignee_user_id = assignee.get("user_id")

    try:
        context_block = assemble_project_context(project_id, assigner_user_id)
    except Exception:  # noqa: BLE001 — best-effort context fold
        context_block = ""

    assigner = next((m for m in roster if m.get("user_id") == assigner_user_id), None)

    try:
        artifacts = list_artifacts_for_project(
            project_id=project_id, dataset=dataset, company_id=company_id
        )[:_ARTIFACT_LIMIT]
    except Exception:  # noqa: BLE001 — best-effort artifact fold
        artifacts = []

    user = _render_brief_user(task_summary, assigner, assignee, context_block, artifacts)

    try:
        brief = call_md(system=_BRIEF_SYSTEM, user=user, model=DEFAULT_MODEL, meta_out=meta)
    except Exception as exc:  # noqa: BLE001 — fail-closed, AD-P19
        _log_brief_run(
            project_id=project_id, assignee_user_id=assignee_user_id, meta=meta,
            start=start, status="error", error_class=type(exc).__name__,
        )
        return None

    _log_brief_run(
        project_id=project_id, assignee_user_id=assignee_user_id, meta=meta,
        start=start, status="complete",
    )
    return brief.strip() or None


def handle_delegate_task(
    *,
    project_id: int,
    assigner_user_id: str | None,
    source_conversation_id: int,
    source_turn_id: int | None,
    roster: list[dict],
    dataset: str,
    company_id: str,
    tool_input: dict,
) -> str:
    """Best-effort dispatch handler for the `delegate_task` tool — never
    raises (AD-P7); on ANY failure it returns a safe decline string and
    delivers nothing. Delivery-then-record ordering (AD-P19): the
    individual-chat turn is written FIRST, the `project_delegations` fact
    SECOND — a failure writing the turn means the fact is never reached."""
    try:
        needle = (tool_input.get("assignee") or "").strip()
        task = (tool_input.get("task_summary") or "").strip()
        if not needle or not task:
            return "I couldn't tell who to send it to or what the task is — ask again with both."

        if not assigner_user_id:
            return "I couldn't tell who's asking, so I can't hand this off — try again."

        res = resolve_member(project_id, needle)
        if res["status"] == "no_match":
            names = ", ".join(m.get("name") or "(unnamed)" for m in res["roster"])
            return f"I don't see '{needle}' on this project. Members: {names}. Who did you mean?"
        if res["status"] == "ambiguous":
            names = ", ".join(m.get("name") or "(unnamed)" for m in res["candidates"])
            return f"'{needle}' could be {names}. Which one?"

        assignee = res["member"]

        # DOUBLE GATE (AD-P16/AD-P18) — the load-bearing IDOR check. Never
        # trust a resolved id as a substitute for a live re-check: verify
        # BOTH the assigner and the resolved assignee are actual members of
        # THIS project, right before the cross-user write.
        if not (
            is_project_member(project_id, assigner_user_id)
            and is_project_member(project_id, assignee["user_id"])
        ):
            return "I can only hand tasks between members of this project."

        brief = _build_brief(
            project_id, assigner_user_id, assignee, task, roster, dataset, company_id
        )
        if not brief:
            return f"I couldn't build the brief for {assignee.get('name') or 'them'} — nothing was sent."

        conv = create_individual_project_chat(project_id, assignee["user_id"])
        # Invariant: get-or-create returns the assignee's OWN
        # (project_id, kind='individual') thread — never a group chat,
        # never another user's thread.
        turn = post_individual_turn(conv["id"], "assistant", brief)  # deliver FIRST
        deleg = record_delegation(  # THEN the fact (AD-P19 order)
            project_id=project_id,
            assigner_user_id=assigner_user_id,
            assignee_user_id=assignee["user_id"],
            task_summary=task,
            source_conversation_id=source_conversation_id,
            source_turn_id=source_turn_id,
            delivered_conversation_id=conv["id"],
            delivered_turn_id=turn["id"],
        )
        logger.info(
            "delegation_delivered project_id=%s assignee=%s delivered_turn_id=%s",
            project_id, assignee["user_id"], turn["id"],
        )
        # Own try/except (AD-P27, server-deterministic genesis fact): a lost
        # genesis event must never roll back a delivery+fact write that has
        # already committed. The zero-events fallback in `v_delegation_status`
        # covers the case where this write is lost (belt-and-braces).
        try:
            record_event(
                delegation_id=deleg["id"], event="assigned", actor_user_id=assigner_user_id
            )
        except Exception as exc:  # noqa: BLE001 — best-effort, never blocks a successful delivery
            logger.warning(
                "delegation_genesis_event_failed delegation_id=%s error=%s",
                deleg["id"], type(exc).__name__,
            )
        # Publish on the ASSIGNEE'S per-user channel, never the group
        # channel — a private brief on `project:{id}` would leak it to
        # every other member. Entirely best-effort (AD-P22): see
        # `_publish_brief_delivered`.
        _publish_brief_delivered(project_id, assignee["user_id"], conv["id"], turn)
        # Ledger-create liveness: mirror the emit route's publish so the Task
        # ledger updates LIVE on CREATION too (not only on later status
        # changes). Publish the shaped `assigned` status DTO to BOTH parties'
        # per-user channels (the self-assign case → the one channel). Entirely
        # best-effort (AD-P22): the delivery + fact are already committed, so a
        # publish hiccup degrades to the client's next reconcile and NEVER
        # changes this handler's return.
        try:
            dto = status_dto(deleg["id"])
            if dto is not None:
                _publish_delegation_event(
                    project_id=project_id,
                    assigner_user_id=assigner_user_id,
                    assignee_user_id=assignee["user_id"],
                    dto=dto,
                )
        except Exception as exc:  # noqa: BLE001 — best-effort, AD-P22: never blocks a delivered handoff
            logger.warning(
                "delegation_create_event_publish_prep_failed delegation_id=%s error_class=%s",
                deleg["id"], type(exc).__name__,
            )
        first = (assignee.get("name") or "").split()[0] if assignee.get("name") else "their"
        return f"Sent the brief to {first}'s chat."
    except Exception as exc:  # noqa: BLE001 — best-effort, AD-P7: never block the group reply
        logger.warning(
            "delegation_failed project_id=%s error=%s", project_id, type(exc).__name__
        )
        return "I hit a problem handing that off — nothing was sent."
