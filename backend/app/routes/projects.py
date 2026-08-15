"""Projects — container + membership CRUD and the layered project-memory
API (folds the build spec's `db/projects.py` CRUD + memory read/manual-add
into one ticket/one file pair).

Tenant gate: every route resolves tenancy from `WorkspaceContext`
(`require_workspace`, the `ask.py` pattern — `backend/app/routes/ask.py`).
Projects scope by `company_id`/`workspace_id` UUID, NOT by a dataset
slug — this router deliberately does not take a `?dataset=` query param
and does not call `require_owned_dataset`.

Scope boundary: memory synthesis + promotion are Phase 2. Artifact fan-out
(GET/POST `.../artifacts`) reuses `db/artifacts.py`'s existing five-table
fan-out (AD-P1/AD-P12, build spec §5.2) — see the handlers below.

Group-chat turn endpoints (build spec §5.3, AD-P2/AD-P4/AD-P10): a
human-to-human group turn is a cheap DB write — no LLM call, UNLESS it
clears the cheap pre-filter in `project_group_gate` and the should-respond
classifier decides the turn is clearly for the agent. Either an explicit
`@Sprntly` mention (deterministic, no classifier call) OR a smart-
interjection `respond=true` decision triggers ONE best-effort LLM call
(AD-P7) producing an assistant turn. There is no user-facing toggle for
this — the agent decides (v3.4 retired the Auto/mention-only setting).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from typing import Literal, NamedTuple

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app import qa_agent
from app.auth import WorkspaceContext, require_workspace
from app.chat_intent import resolve_chat_intent
from app.db import conversation_read_cursors as read_cursors_db
from app.db import conversations as conversations_db
from app.db import delegation_events as delegation_events_db
from app.db import project_memory_entries as memory_db
from app.db import projects as projects_db
from app.db import team as team_db
from app.db import workspaces as workspaces_db
from app.db.artifacts import list_artifacts_for_company, list_artifacts_for_project
from app.db.companies import get_seat_limit
from app.db.custom_artifacts import BodyTooLarge, create_artifact
from app.db.prds import save_prd_version, update_prd_content
from app.ingest import convert
from app.team_email import send_invite_email
from app.deps.ownership import require_owned_evidence, require_owned_prd
from app import project_delegation
from app import project_group_context
from app import project_join_greeting
from app import project_task_execution
from app.project_chat_edit import apply_chat_edit_scoped
from app.project_prd_gate import ProjectPrdWriteDenied, assert_prd_on_project
from app.project_prd_patch_tool import (
    _project_prd_ids,
    _resolve_prd_id,
    project_prd_edit_enabled,
)
from app.realtime import publish_broadcast
from app.project_artifact_capture import save_chat_output_as_report
from app.project_from_prd import find_existing_prd_auto_project
from app.project_group_gate import render_group_transcript, should_respond
from app.project_memory import maybe_promote_turn, schedule_regen
from app.routes.ask import _load_history
from app.routes.chat import _dataset_for
from app.surface_scope import PROJECT_TOOL_NUDGE, Surface, SurfaceScope

logger = logging.getLogger(__name__)

# Deterministic trigger — checked FIRST, unconditionally, no classifier
# call (AD-P10). Word-boundary so "@Sprntly" and "@sprntly" both match but
# a longer handle sharing the prefix would not. A turn that does NOT match
# this falls through to the smart-interjection gate (`project_group_gate.
# should_respond`) below.
_MENTION_RE = re.compile(r"@sprntly\b", re.IGNORECASE)

# How many of the most recent group turns are folded into the agent's
# context on a mention reply — bounded so a long-running group chat can't
# grow the prompt unboundedly (mirrors the per-turn history clamp posture
# `_load_history`/`app.prompt_history` already apply to individual chats).
_GROUP_CONTEXT_TURNS = 30

# The exact `list_group_turns` read-DTO key set — a hard whitelist applied
# before every `turn.created` broadcast (AD-P21 no-schema-coupling), so an
# internal `conversation_turns` column (e.g. `attachments`) can never ride
# along on the wire even if a future column is added to the table.
_GROUP_TURN_DTO_KEYS = (
    "id", "role", "content", "author_user_id", "author_name",
    "author_job_role", "created_at",
)

# Strong refs to in-flight background group-reply tasks — mirrors
# `routes.ask._inflight_tasks` (`ask.py:43`). asyncio holds only a WEAK
# reference to a bare `create_task` result, so without this the task can be
# garbage-collected mid-run. The done-callback discards each task on
# completion.
_group_reply_tasks: set[asyncio.Task] = set()

# LT-8 (genuinely open, spec §10.2/build-spec §Group): which shape the
# group agent's reply call sees as `question` — the attributed transcript
# itself, or just the latest triggering message (with the full transcript
# still riding on `SurfaceScope.prerendered_transcript` either way, so the
# model always sees the whole thread; see `_respond_as_group_agent`).
# Defaulted to the conservative option so a missing live test never
# silently changes router/interceptor behaviour (build-spec §Group);
# pinned by the ship-gate LT-8 live test before merge.
_GROUP_TRANSCRIPT_AS_QUESTION = False


def _schedule_group_reply(
    project_id: int, conversation_id: int, ctx: WorkspaceContext, trigger_kind: str,
) -> None:
    """Fire-and-forget the group agent's reply, backgrounded off the
    request path (spec §5.5; Gate-1 BLOCKER-1 fix). `post_group_turn_route`
    is `async def` so a loop is running here, letting `asyncio.create_task`
    schedule `asyncio.to_thread(_respond_as_group_agent, ...)` — `to_thread`
    because `_respond_as_group_agent` stays a SYNC function (its LLM/DB
    calls are blocking and belong on the threadpool, not the event loop). A
    bare `create_task(_respond_as_group_agent(...))` would raise twice over
    (no running loop outside a request, and the target isn't a coroutine).

    Under `"pytest" in sys.modules` runs the reply INLINE instead (mirrors
    `routes.ask.py`'s own `:457` guard) — the TestClient does not keep the
    app's event loop alive between requests, so a fire-and-forget task would
    never run and a test asserting on the posted reply would hang/miss it."""
    if "pytest" in sys.modules:
        _respond_as_group_agent(project_id, conversation_id, ctx, trigger_kind=trigger_kind)
        return
    task = asyncio.create_task(
        asyncio.to_thread(
            _respond_as_group_agent, project_id, conversation_id, ctx, trigger_kind,
        )
    )
    _group_reply_tasks.add(task)
    task.add_done_callback(_group_reply_tasks.discard)


def _publish_group_turn_created(project_id: int, conversation_id: int, turn: dict | None) -> None:
    """Best-effort publish-on-write for one group turn (human or assistant).
    The re-read (`list_group_turns`) that shapes the DTO, the shaping
    itself, AND `publish_broadcast` are ALL swallowed here (AD-P22):
    `turn` has already persisted by the time this is called, so a
    transient re-read hiccup must never 500 the request or otherwise
    mask the already-successful write. `publish_broadcast` itself never
    raises either, but the re-read that feeds it is a separate DB call
    with no such guarantee — hence this wrapper, not just a bare call."""
    if not turn:
        return
    try:
        shaped = conversations_db.list_group_turns(conversation_id, since=turn["id"] - 1)
        dto = next((t for t in shaped if t["id"] == turn["id"]), None)
        if dto is not None:
            publish_broadcast(
                f"project:{project_id}", "turn.created",
                {k: dto[k] for k in _GROUP_TURN_DTO_KEYS},
            )
    except Exception as exc:  # noqa: BLE001 — best-effort, AD-P22: realtime-prep never breaks the write
        logger.warning(
            "realtime_publish_prep_failed topic=project:%s event=turn.created error_class=%s",
            project_id, type(exc).__name__,
        )


_GROUP_AGENT_SYSTEM_PROMPT = """\
You are Sprntly, a project teammate embedded in this team's group chat.
You were tagged with @Sprntly in the transcript below. Read the recent
conversation (each line is "Name (job role): message" or "Sprntly: message"
for your own prior turns) and reply helpfully and concisely to whoever
tagged you, as one more voice in the thread — not a formal report.

Rules:
- Address the request that mentioned you; use the surrounding turns only
  as context for who is asking and why.
- Keep it conversational and short — a few sentences, not a document.
- If the ask is unclear or out of scope, say so plainly rather than
  guessing.
- You have a delegate_task tool: when someone asks you to hand a specific
  task to a teammate, call it (pick the assignee from the roster below).
  Do not call it for a plain question, an FYI, or human-to-human chatter.
- You have NO PRD-editing tool in THIS reply. A PRD edit, when it can be
  made, is applied by a separate step BEFORE you are asked to reply — so
  if you are being asked to reply here, no edit was applied on this turn.
  Therefore you must NEVER claim you edited the document: do not say you
  "added", "updated", "changed", "removed", or "appended" anything to the
  PRD, and never report a change as "done". If the latest turn is asking
  for a PRD change, either discuss it or say what you need to make it (for
  example, which PRD to edit) — but do NOT state the change as already
  made. Reporting an edit that did not happen misleads the team.

You KNOW this project. The PROJECT CONTEXT block below gives you the
project's shared memory, its members (the roster), its open tasks (the
delegation ledger), and its artifacts (PRDs, prototypes, evidence,
reports, and uploaded documents). Answer questions about any of these
directly — never say you "can't see" the team's files, tasks, or members.
For the FULL detail behind the summary, use your read tools:
get_project_memory, list_project_artifacts, get_artifact_content (to read
a specific PRD/report/evidence body OR an uploaded document's full text),
and get_task_ledger. Every one of these is scoped to THIS project only.
When someone asks what a document says — including an uploaded file, not
just a PRD — call get_artifact_content NOW, in this same reply, and
answer from the real content. Never tell the team you will check or read
it later; you have the tool, use it immediately.

{nudge}
""".format(nudge=PROJECT_TOOL_NUDGE)


def _group_system_with_roster(roster: list[dict]) -> str:
    """`_GROUP_AGENT_SYSTEM_PROMPT` + a live `PROJECT ROSTER:` block (AD-P18
    model-arbitration seam) — first-name + job_role per member, no PII
    beyond that. Lets the model resolve a free-text assignee ("the
    designer") to a specific teammate before calling `delegate_task`, at
    zero extra LLM-call cost (the roster rides on this same reply call)."""
    lines = []
    for m in roster:
        name = m.get("name") or "(unnamed)"
        first = name.split()[0] if name != "(unnamed)" else name
        role = m.get("job_role") or "no role set"
        lines.append(f"- {first} — {role}")
    roster_block = "PROJECT ROSTER:\n" + ("\n".join(lines) if lines else "(no other members yet)")
    return f"{_GROUP_AGENT_SYSTEM_PROMPT}\n{roster_block}"

def _projects_enabled() -> bool:
    """Read PROJECTS_ENABLED at REQUEST TIME (never import time). Default-off;
    never default-on in any commit. Request-time read means flipping the env
    var takes effect without a code deploy and keeps the gate honest under
    module reload in tests. The frontend uses a SEPARATE var,
    NEXT_PUBLIC_PROJECTS_ENABLED; the two gate independently — THIS one is the
    security boundary (the frontend build-time flag is not)."""
    val = (os.environ.get("PROJECTS_ENABLED") or "").strip().lower()
    return val in {"1", "true", "yes"}


def _require_projects_enabled() -> None:
    if not _projects_enabled():
        raise HTTPException(status_code=404, detail="Not found")  # invisible, not 401/403


router = APIRouter(
    prefix="/v1/projects",
    tags=["projects"],
    dependencies=[Depends(_require_projects_enabled)],
)


# The agent is a virtual member — rendered from a constant, never a stored
# `project_members`/`auth.users` row (AD-P6). A fresh dict is prepended per
# response so no caller can mutate the shared constant.
_AGENT_MEMBER = {
    "user_id": None,
    "kind": "agent",
    "name": "Sprntly",
    "role_label": "Agent coworker · dispatches tasks",
    "status": "working",
}


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1)
    origin: str = "manual"
    # Only meaningful when origin="prd_auto" (the create-modal's "Auto ·
    # from PRD" tab) — the PRD being forked, used for the first-write-wins
    # dedup check below. Ignored for every other origin.
    prd_id: int | None = Field(default=None, ge=1)


class AddMemberRequest(BaseModel):
    email: str = Field(min_length=1)


class SetInstructionsRequest(BaseModel):
    # Empty string is allowed — it clears the saved value (db.projects.
    # set_instructions normalizes it to NULL).
    instructions: str = Field(max_length=2000)


class TagCandidateRequest(BaseModel):
    # A name (picked from the roster/picker) OR an email (invite-by-email).
    # `resolve_candidate` decides which shape it is and classifies the tier.
    needle: str = Field(min_length=1)


class AddMemoryEntryRequest(BaseModel):
    body: str = Field(min_length=1)


class UpdateMemoryEntryRequest(BaseModel):
    body: str = Field(min_length=1)


class AddArtifactRequest(BaseModel):
    artifact_type: Literal["prd", "evidence", "prototype", "report", "ticket_set"]
    artifact_id: int = Field(..., ge=1)


class SaveChatArtifactRequest(BaseModel):
    content: str = Field(..., min_length=1)
    title: str | None = None
    source_conversation_id: int | None = None


class PostGroupTurnRequest(BaseModel):
    content: str = Field(min_length=1)


class EmitDelegationEventRequest(BaseModel):
    event: str = Field(min_length=1)
    note: str | None = None


def _require_project(project_id: int, ctx: WorkspaceContext) -> dict:
    """Load a project and prove it belongs to the caller's
    (company, workspace) — 404 (never 403) on any mismatch or absence, so
    a foreign project id's existence is never disclosed."""
    project = projects_db.get_project(project_id)
    if not project or not projects_db.project_belongs_to_company(
        project_id, ctx.company_id, ctx.workspace_id
    ):
        raise HTTPException(404, "Project not found")
    return project


def _require_project_member(project_id: int, ctx: WorkspaceContext) -> dict:
    """`_require_project` PLUS membership: the caller must be a
    `project_members` row (AD-P11 — membership = access). A same-tenant
    caller who is NOT a member gets 403 (the project's existence within
    their own tenant is not a secret — only cross-tenant existence is
    hidden, via `_require_project`'s 404). Every handler below that reads
    or mutates a project's members-only surface (detail, memory CRUD,
    summary) goes through this — not just `add_member`."""
    project = _require_project(project_id, ctx)
    if not projects_db.is_project_member(project_id, ctx.user_id):
        raise HTTPException(403, "Not a member of this project")
    return project


@router.get("")
def list_projects(ctx: WorkspaceContext = Depends(require_workspace)):
    """Projects for the caller's active workspace, recency-ordered — AND
    scoped to projects the caller is a MEMBER of (membership = access,
    AD-P11); a workspace project the caller hasn't been added to never
    appears here, same principle as the per-project 403 below. No
    `dataset` param — tenant scoping is entirely
    `ctx.company_id`/`ctx.workspace_id`."""
    return {
        "projects": projects_db.list_projects_for_workspace(
            ctx.company_id, ctx.workspace_id, ctx.user_id
        )
    }


@router.post("")
def create_project(
    payload: CreateProjectRequest,
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """Create a project. `origin="prd_auto"` + `prd_id` is first-write-wins
    (AD-P9), same principle as the generation-time hook
    (`maybe_auto_create_project_for_prd`, `app/project_from_prd.py`):
    re-selecting an already-forked PRD in the create-modal's "Auto · from
    PRD" tab returns the EXISTING project instead of minting a duplicate.
    Every other origin (manual/artifact) is unaffected — no dedup key exists
    for them, and none is checked."""
    if payload.origin == "prd_auto" and payload.prd_id is not None:
        existing_id = find_existing_prd_auto_project(payload.prd_id, ctx.company_id)
        if existing_id is not None and projects_db.project_belongs_to_company(
            existing_id, ctx.company_id, ctx.workspace_id
        ):
            existing = projects_db.get_project(existing_id)
            if existing is not None:
                return existing

    project = projects_db.create_project(
        company_id=ctx.company_id,
        workspace_id=ctx.workspace_id,
        name=payload.name,
        created_by=ctx.user_id,
        origin=payload.origin,
    )
    logger.info("project_created project_id=%s", project["id"])
    return project


@router.get("/{project_id}")
def get_project(project_id: int, ctx: WorkspaceContext = Depends(require_workspace)):
    """Project detail: the row, human members (+ prepended virtual agent
    member, AD-P6), and the group-chat id (or null when no group chat
    has been created for this project yet). Membership-gated (403 for a
    same-tenant non-member) — the virtual agent member is irrelevant to
    this human-caller gate."""
    project = _require_project_member(project_id, ctx)
    members = projects_db.list_members(project_id)
    group_chat_id = projects_db.get_group_chat_id(project_id)
    return {
        **project,
        "members": [dict(_AGENT_MEMBER), *members],
        "group_chat_id": group_chat_id,
    }


@router.get("/{project_id}/instructions")
def get_instructions_route(project_id: int, ctx: WorkspaceContext = Depends(require_workspace)):
    """The project's saved instructions, or `null` when nothing has been
    set. Membership-gated like `get_project` (403 same-tenant non-member,
    404 foreign-tenant/absent, AD-TNM1)."""
    _require_project_member(project_id, ctx)
    return {"instructions": projects_db.get_instructions(project_id)}


@router.put("/{project_id}/instructions")
def set_instructions_route(
    project_id: int,
    body: SetInstructionsRequest,
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """Persist the project's instructions (any member may write — v1
    membership = access, AD-P11; no per-project role tier). An empty/
    whitespace-only body clears the saved value. Never logs the body
    itself — identifiers only."""
    _require_project_member(project_id, ctx)
    projects_db.set_instructions(project_id, body.instructions)
    logger.info("project_instructions_set project_id=%s", project_id)
    return {"instructions": projects_db.get_instructions(project_id)}


@router.post("/{project_id}/members")
def add_member(
    project_id: int,
    payload: AddMemberRequest,
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """Add an existing IN-TENANT user to the project by email. The caller must
    already be a project member (membership = access, AD-P11) — a non-member
    gets 403 and the roster is unchanged.

    IDOR fix (AD-TNM1): resolution goes through `resolve_candidate`, which
    classifies the email against THIS project's tenancy fail-closed, instead
    of the old global `user_id_for_email` (which resolved any company's email
    → a cross-company user could be added). Only an existing project member
    (`t_member`, idempotent) or an in-tenant existing user (`t_workspace`/
    `t_company`) is added; a foreign-company or non-user email → 404, no
    write, no cross-tenant existence disclosure. The success response is
    byte-identical to before (the member row / the existing member row).

    Inviting a not-yet-existing user by email is the tag endpoint's job
    (`POST .../tag`, which creates a project-carrying invite) — this route
    only grows the roster with an account that already exists in-tenant."""
    _require_project_member(project_id, ctx)
    res = projects_db.resolve_candidate(project_id, payload.email)
    tier = res["tier"]
    if tier == projects_db.TIER_MEMBER:
        return res["member"]  # idempotent — already a member, byte-identical
    if tier in (projects_db.TIER_WORKSPACE, projects_db.TIER_COMPANY):
        member = projects_db.add_member(project_id, res["user_id"])
        logger.info("project_member_added project_id=%s user_id=%s", project_id, res["user_id"])
        # NEW-membership only (the TIER_MEMBER re-add branch returned above):
        # drop a grounding greeting into this member's private project chat so
        # they land with context, not a blank thread. Best-effort/non-blocking
        # (AD-P7) — a greeting failure never breaks or delays the add.
        project_join_greeting.post_join_greeting(project_id, res["user_id"])
        return member
    # t_newuser / t_refuse (foreign company, or no in-tenant account) — no add,
    # no disclosure of which reason applied.
    raise HTTPException(404, "No account in your company for that email")


# ── Tag-action surface (the loop's one authorization/mutation surface) ──
# `POST /{project_id}/tag` classifies a mentioned name/email via
# `resolve_candidate` (the tenant-scoped tier resolver) and, per tier, adds the person to the project
# (t_workspace), sends a project-carrying invite (t_company/t_newuser), or
# hard-refuses (t_refuse) — each re-asserting tenancy immediately before the
# write (AD-TNM1, fail-closed). De-gated to ANY project member (AD-TNM4 — no
# admin/owner check), seat-priced for the invite tiers, and degrade-not-error
# on email failure (AD-TNM6). No LLM call anywhere here (pure CRUD).


def _invite_carrying_project(
    project: dict, email: str, invited_by: str, *, existing_member: bool
) -> dict:
    """Extensions A (t_company) + B (t_newuser) share this: create a
    workspace-join invite carrying THIS project (`project_id` + the project's
    workspace), then best-effort send the branded invite email with the
    project NAME only (AD-TNM2). Seat-priced (AD-TNM4): each pending invite
    reserves a seat, so a full company 409s before the row is created.

    Degrade-not-error (AD-TNM6): the invite ROW is written BEFORE the email
    attempt, so a FAILED send never loses the invite and never 500s the tag —
    the caller gets `email_status` and the person can be re-notified from Team
    settings (no raw accept link is exposed)."""
    company_id = project["company_id"]
    workspace_id = project["workspace_id"]

    # Seat guard — mirror routes/team.py::_require_free_seat exactly, incl. the
    # None-is-unlimited contract (never NameError/TypeError on the no-limit case).
    from app.routes.team import _seats_in_use

    limit = get_seat_limit(company_id)
    if limit is not None and _seats_in_use(company_id) >= limit:
        raise HTTPException(409, "No paid seats available")

    invite = team_db.create_invite(
        company_id=company_id,
        email=email,
        role="member",
        invited_by=invited_by,
        workspace_ids=[workspace_id],
        project_id=project["id"],  # carries the project (Extension B, AD-TNM3)
    )

    # Resolve personalization the same way routes/team.py::_send_invite_for_row
    # does — best-effort, a failed lookup falls back to a friendly default.
    from app.db.companies import display_name_for_company_id
    from app.db.profiles import first_name_for_email, first_name_for_user

    try:
        inviter_first = first_name_for_user(invited_by)
    except Exception:  # noqa: BLE001 — personalisation is best-effort
        inviter_first = ""
    try:
        workspace_name = display_name_for_company_id(company_id) or ""
    except Exception:  # noqa: BLE001
        workspace_name = ""
    try:
        invitee_first = first_name_for_email(email)
    except Exception:  # noqa: BLE001
        invitee_first = ""

    status = send_invite_email(
        email,
        inviter_first_name=inviter_first,
        workspace_name=workspace_name,
        first_name=invitee_first,
        project_name=project["name"],  # NAME only, never project content (AD-TNM2)
    )
    logger.info(
        "project_invite_created project_id=%s email_domain=%s status=%s existing=%s",
        project["id"], email.split("@")[-1], status, existing_member,
    )  # domain only — never the full address, invitee name, or needle text
    return {
        "tier": projects_db.TIER_COMPANY if existing_member else projects_db.TIER_NEWUSER,
        "invited": True,
        "email_status": status,
    }


def _tag_actor_name(actor_user_id: str) -> str | None:
    """Best-effort display name of the member doing the tag, for the private
    `mention.received` DTO's `actor_name` (the recipient renders "X mentioned
    you"). Returns None on any lookup failure — the signal is a nicety, never
    load-bearing, and this must never raise into the tag route (AD-P22)."""
    try:
        from app.db.profiles import first_name_for_user

        return first_name_for_user(actor_user_id) or None
    except Exception:  # noqa: BLE001 — best-effort personalisation
        return None


@router.post("/{project_id}/tag")
def tag_candidate_route(
    project_id: int,
    payload: TagCandidateRequest,
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """Classify a tagged name/email and act per tier (AD-TNM1). GATE 1 is
    `_require_project_member` (tenant + membership, ANY member — DE-GATED per
    AD-TNM4, no admin/owner check) BEFORE `resolve_candidate` touches any
    identity. Every add/invite re-asserts tenancy immediately before the
    write; a refuse never writes and never discloses which reason applied."""
    project = _require_project_member(project_id, ctx)  # GATE 1
    res = projects_db.resolve_candidate(project_id, payload.needle)
    tier = res["tier"]

    if tier == projects_db.TIER_MEMBER:
        # Notify-only response; additionally emit a best-effort private
        # "you were mentioned" signal on the mentioned member's OWN per-user
        # channel (AD-TNM5). The publisher swallows every failure — this never
        # changes the notify-only response or blocks it (AD-TNM2/AD-P22).
        member = res["member"]
        project_delegation._publish_mention_signal(
            project_id, member.get("user_id"), _tag_actor_name(ctx.user_id), project["name"]
        )
        return {"tier": tier, "member": member}

    if tier == projects_db.TIER_WORKSPACE:
        uid = res["user_id"]
        # AD-TNM1 backstop: re-assert live workspace membership against THIS
        # project's workspace immediately before the write — never trust the
        # tier the classifier returned as a substitute for the live check.
        if not workspaces_db.get_workspace_member(project["workspace_id"], uid):
            raise HTTPException(403, "That person can't be added to this project")
        member = projects_db.add_member(project_id, uid)
        logger.info("project_member_added project_id=%s user_id=%s via=tag", project_id, uid)
        # Best-effort live landing on the added person's OWN per-user channel
        # (AD-TNM5); swallows every failure, never changes this response or
        # rolls back the add (AD-TNM2/AD-P22).
        project_delegation._publish_member_added(project_id, uid, project["name"])
        return {"tier": tier, "added": member}

    if tier == projects_db.TIER_COMPANY:  # Extension A — workspace-join invite
        return _invite_carrying_project(
            project, res["email"], ctx.user_id, existing_member=True
        )

    if tier == projects_db.TIER_NEWUSER:  # Extension B — full company+workspace invite
        return _invite_carrying_project(
            project, res["email"], ctx.user_id, existing_member=False
        )

    # t_refuse — one opaque 403, no write, no disclosure of which reason
    # (cross_company / other_company / no_match / ambiguous / no_project).
    raise HTTPException(403, "That person can't be added to this project")


@router.get("/{project_id}/candidates")
def candidate_search_route(
    project_id: int,
    q: str = "",
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """Tenant-scoped candidate directory for the picker (feeds the picker UI,
    keeps it pure-frontend). Members already on the project + in-tenant non-members
    (workspace directory, then the rest of the company directory), each tagged
    `kind` in {"member","workspace","company"}, filtered by casefold-contains
    on name/email, capped at 20. NEVER lists anyone outside the project's
    `company_id`. Membership-gated (403 for a same-tenant non-member)."""
    project = _require_project_member(project_id, ctx)
    company_id = project["company_id"]
    workspace_id = project["workspace_id"]
    needle = (q or "").strip().casefold()

    out: list[dict] = []
    seen: set[str] = set()

    def _emit(user_id: str, name: str | None, email: str | None, kind: str) -> None:
        if not user_id or user_id in seen:
            return
        if needle:
            hay = f"{(name or '')} {(email or '')}".casefold()
            if needle not in hay:
                return
        seen.add(user_id)
        out.append({"kind": kind, "user_id": user_id, "name": name, "email": email})

    # 1) members already on the project.
    for m in projects_db.list_members(project_id):
        _emit(m.get("user_id"), m.get("name"), m.get("email"), "member")

    # 2) in-tenant workspace directory (non-members of the project).
    for e in workspaces_db.list_workspace_members(workspace_id):
        _emit(e.get("user_id"), e.get("display_name"), e.get("email"), "workspace")

    # 3) the rest of the company directory (same tenant, other workspaces).
    for e in team_db.list_company_members(company_id):
        _emit(e.get("user_id"), e.get("display_name"), e.get("email"), "company")

    return {"candidates": out[:20]}


@router.delete("/{project_id}/members/{user_id}")
def remove_member(
    project_id: int,
    user_id: str,
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """Remove a teammate from the project. The caller must already be a
    project member (membership = access, AD-P11) — a non-member gets 403
    and the roster is unchanged. Authorization is v1-simple: any member may
    remove any OTHER member except the creator (no per-project role column
    yet — that tightening to owner-only removal is a later ticket once
    `project_members.role` lands, the AD-P11 extension seam).

    Guards, checked in order (roster unchanged on every rejection):
      - self-removal ("leave project") is out of scope for this ticket →
        400.
      - the project creator (`projects.created_by`) can never be removed
        (would orphan the project) → 409.
      - a target who isn't currently a member → 404 (mirrors
        `delete_memory`'s not-found posture for a client-supplied id that
        doesn't resolve)."""
    project = _require_project_member(project_id, ctx)
    if user_id == ctx.user_id:
        raise HTTPException(400, "Removing yourself isn't supported here")
    if user_id == project["created_by"]:
        raise HTTPException(409, "The project creator can't be removed")
    removed = projects_db.remove_member(project_id, user_id)
    if not removed:
        raise HTTPException(404, "Not a member of this project")
    logger.info(
        "member_removed project_id=%s target_user_id=%s by=%s",
        project_id, user_id, ctx.user_id,
    )
    return {"removed": True}


@router.get("/{project_id}/artifacts")
def list_project_artifacts(project_id: int, ctx: WorkspaceContext = Depends(require_workspace)):
    """The project's artifacts, app-faithfully — the SAME unified shape
    `GET /v1/artifacts` returns, filtered to this project's refs (AD-P1/
    AD-P12, build spec §5.2). Membership-gated (403 for a same-tenant
    non-member, 404 for a foreign-tenant project, via
    `_require_project_member`)."""
    _require_project_member(project_id, ctx)
    items = list_artifacts_for_project(
        project_id=project_id, dataset=_dataset_for(ctx), company_id=ctx.company_id
    )
    return {"artifacts": items}


@router.post("/{project_id}/artifacts")
def add_project_artifact(
    project_id: int,
    payload: AddArtifactRequest,
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """Add an artifact ref to the project. Membership-gated, THEN write-time
    access validation (AD-P12 — the IDOR guard, R3): the caller must
    actually reach the artifact through their OWN company before the ref is
    written — the client-supplied `(artifact_type, artifact_id)` pair is
    never trusted alone.

    `prd`/`evidence` go through their dedicated ownership dep
    (`deps/ownership.py`). `prototype`/`report`/`ticket_set` have no
    dedicated dep — there is no `require_owned_prototype/report/ticket_set`
    — so they're validated by presence in the caller's own company fan-out
    (`list_artifacts_for_company`), reusing the existing scoping rather than
    inventing a new tenancy convention. On any validation failure: 404, no
    ref written (no cross-tenant existence leak — same posture as every
    other ownership gate in this codebase)."""
    _require_project_member(project_id, ctx)

    if payload.artifact_type == "prd":
        require_owned_prd(payload.artifact_id, ctx.company_id, ctx.workspace_id)
    elif payload.artifact_type == "evidence":
        require_owned_evidence(payload.artifact_id, ctx.company_id, ctx.workspace_id)
    else:
        owned_ids = {
            item["id"]
            for item in list_artifacts_for_company(
                dataset=_dataset_for(ctx), company_id=ctx.company_id
            )
            if item["type"] == payload.artifact_type
        }
        if payload.artifact_id not in owned_ids:
            raise HTTPException(404, f"{payload.artifact_type.capitalize()} not found")

    ref = projects_db.add_artifact(project_id, payload.artifact_type, payload.artifact_id)
    logger.info(
        "project_artifact_added project_id=%s type=%s artifact_id=%s",
        project_id, payload.artifact_type, payload.artifact_id,
    )
    return ref


# Same ceiling as the chat composer's own attachment parse (`routes/ask.py`'s
# `_MAX_EXTRACT_BYTES`) — a slide deck or spec is comfortably under this.
# A dedicated module-level const rather than importing ask.py's private one,
# so the two ceilings can be tuned independently later without cross-module
# coupling.
_MAX_DOC_BYTES = 25 * 1024 * 1024  # 25 MB


def _custom_artifact_item(row: dict) -> dict:
    """Shape a freshly-created `custom_artifacts` row into the SAME
    fan-out-shaped dict `db/artifacts.py`'s `list_artifacts_for_company`
    emits for a `custom_artifact` (see its own docstring for the field-by-
    field rationale) — built from the row in hand, no re-query. Lets the FE
    insert the returned item into its list without a refetch."""
    return {
        "type": "custom_artifact",
        "id": row["id"],
        "title": row.get("title") or "",
        "status": row.get("status") or "",
        "kind": row.get("kind") or "",
        "created_at": row.get("updated_at") or row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "born_at": row.get("created_at"),
        "source": {
            "kind": row.get("kind") or "",
            "conversation_id": row.get("conversation_id"),
            "conversation_title": None,
        },
        "open": {"custom_artifact_id": row["id"]},
    }


@router.post("/{project_id}/documents")
async def upload_project_document(
    project_id: int,
    file: UploadFile = File(...),
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """Upload a document (pdf/docx/pptx/xlsx/txt/md) and attach it to the
    project as a `custom_artifact` — the always-in-context "team documents"
    library, reused rather than a new concept (see the migration widening
    `project_artifacts.artifact_type`'s CHECK for why the attach write needs
    it). Text-only: the extracted markdown lands in `custom_artifacts.
    body_html` (Postgres) — no raw bytes are staged anywhere, no
    `document_catalog` row is created.

    Membership-gated (`_require_project_member`, AD-P11) BEFORE any read or
    write. `create_artifact` mints the document under `ctx.company_id`, so
    it is inherently the caller's — no separate ownership re-resolve is
    needed the way the generic `/artifacts` route's client-supplied id
    requires. `workspace_id` is stamped from `ctx`, never a baked default.

    Validation mirrors `POST /v1/ask/extract-file` exactly (empty → 400,
    oversize → 413, no-extractable-text → 422) — convert-failure/empty
    returns BEFORE any row is written, so there is no orphan on a rejected
    upload. If `add_artifact` fails after `create_artifact` succeeds, the
    document exists unattached (still reachable via the custom-artifact
    routes) — acceptable; this does NOT compensating-delete."""
    _require_project_member(project_id, ctx)
    data = await file.read()
    if not data:
        raise HTTPException(400, "Uploaded file is empty.")
    if len(data) > _MAX_DOC_BYTES:
        raise HTTPException(413, "File too large (max 25 MB).")
    markdown = await asyncio.to_thread(convert, file.filename or "upload", data)
    if not markdown.strip():
        raise HTTPException(
            422,
            "Could not extract any text from the file. Scanned/image-only "
            "PDFs and legacy .ppt are not supported — export to PDF or .pptx.",
        )
    try:
        artifact = create_artifact(
            ctx.company_id,
            kind="document",
            title=(file.filename or "Untitled document"),
            body_html=markdown,
            workspace_id=ctx.workspace_id,
            created_by=ctx.user_id,
        )
    except BodyTooLarge:
        raise HTTPException(413, "Document is too large to store (over 400,000 characters).")
    projects_db.add_artifact(project_id, "custom_artifact", int(artifact["id"]))
    logger.info(
        "project_document_uploaded project_id=%s custom_artifact_id=%s bytes=%s",
        project_id, artifact["id"], len(data),
    )
    return _custom_artifact_item(artifact)


class ProjectChatEditIn(BaseModel):
    instruction: str = Field(..., min_length=3, max_length=4000)
    # OPTIONAL — the id the caller picked off a prior `clarify` envelope's
    # `prd_options` (2+-PRD disambiguation). Omitted (the default, and the
    # ONLY value pre-fix callers ever sent), target resolution is unchanged:
    # server-side auto-select on exactly one project PRD, refuse on 0/2+.
    # A client-SUPPLIED id is NOT trusted on its own — `_resolve_prd_id`
    # returns it verbatim (`tool_input.get("prd_id")` → `(int(raw), None)`,
    # no signature change), but `apply_chat_edit_scoped` below runs the ★
    # cross-project (`assert_prd_on_project`) + cross-tenant
    # (`require_owned_prd`) gate on WHATEVER `prd_id` reaches it,
    # unconditionally and BEFORE any read/write — identically whether that
    # id was server-auto-selected or client-supplied. See
    # `test_project_chat_edit_explicit_id_cross_project_denied` for the
    # mutation-proofed IDOR guard.
    prd_id: int | None = Field(default=None, ge=1)


@router.post("/{project_id}/prd/chat-edit")
def project_chat_edit(
    project_id: int,
    body: ProjectChatEditIn,
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """The private (and, later, group) project chat's PRD-edit write path —
    the in-place, versioned counterpart to the retired propose/review
    `prd_patches` flow, reusing the SAME `apply_chat_edit_scoped`
    the main chat's `POST /v1/prd/{id}/chat-edit` calls guard-off.

    Membership-gated (`_require_project_member`), THEN the request-time
    `PROJECT_PRD_EDIT_ENABLED` rollout flag (503 semantics degrade to a
    no-op — off means no write and a no-edit reply, never an error), THEN
    target resolution: `_resolve_prd_id` auto-selects on exactly one project
    PRD, or — on 2+ PRDs — accepts the id the caller picked off a prior
    `clarify` envelope's `prd_options` (`body.prd_id`, OPTIONAL, `None` on
    every pre-fix call). 0/ambiguous-and-unpicked PRDs make no write and
    return a no-edit, answer-shaped payload (`{"edited": false, "answer"}`)
    instead of an error, so the private chat can degrade to a grounded ask.

    ★ `body.prd_id` is CLIENT-SUPPLIED and UNTRUSTED on its own — a member of
    project A could name a PRD on project B, or (probed) another tenant's.
    `apply_chat_edit_scoped` runs the ★ cross-project IDOR gate
    (`assert_prd_on_project`) — fail-closed, before any read/write — on
    WHATEVER `prd_id` reaches it, identically whether server-auto-selected
    or client-supplied, THEN the cross-tenant gate (`require_owned_prd`).
    This is the PRIMARY defense for the client-supplied case, not defense in
    depth — `_resolve_prd_id` performs no project/tenant check of its own on
    an explicit id. A `ProjectPrdWriteDenied` from that gate is caught
    and degrades to the same no-edit shape rather than a raw 403/404, since
    the resolved-target contract promises callers a soft refusal, not an
    error, on this route.
    """
    _require_project_member(project_id, ctx)

    if not project_prd_edit_enabled():
        return {
            "edited": False,
            "answer": "PRD editing from chat isn't turned on for this project yet.",
        }

    dataset = _dataset_for(ctx)
    # `body.prd_id` present (the caller picked a PRD off a prior `clarify`
    # envelope's `prd_options`) -> thread it through explicitly, same shape
    # `_resolve_prd_id` already honors (`tool_input.get("prd_id")`) for the
    # write-tool caller. Absent -> `{}`, preserving today's server-side
    # auto-select/refuse behavior byte-for-byte.
    tool_input = {"prd_id": body.prd_id} if body.prd_id is not None else {}
    prd_id, refusal = _resolve_prd_id(tool_input, project_id, dataset, ctx.company_id)
    if prd_id is None:
        return {"edited": False, "answer": refusal or "I couldn't work out which PRD to edit."}

    try:
        result = apply_chat_edit_scoped(
            prd_id, body.instruction, ctx, project_id=project_id, dataset=dataset,
        )
    except ProjectPrdWriteDenied:
        return {
            "edited": False,
            "answer": "I can only edit a PRD that's attached to this project.",
        }

    return {"edited": True, **result}


class ProjectPrdContentIn(BaseModel):
    """Full-document PRD save from a project surface — the direct-edit
    counterpart to the chat-edit route above. `html` is the whole serialized
    `<!DOCTYPE html>…` document the drawer's inline editor round-trips (the
    SAME shape `PUT /v1/prd/{id}` stores in `payload_md`)."""
    prd_id: int = Field(..., ge=1)
    title: str = Field(..., min_length=1)
    html: str = Field(...)


@router.post("/{project_id}/prd/content")
def project_prd_content_save(
    project_id: int,
    body: ProjectPrdContentIn,
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """Persist a full-HTML PRD edit made in THIS project's artifact drawer —
    the project-scoped, IDOR-gated equivalent of `PUT /v1/prd/{id}` (which is
    cross-TENANT-gated only and has no project concept).

    ★ Security: unlike the main-chat `PUT /{id}`, the `prd_id` here is
    client-supplied, so the ★ cross-PROJECT gate (`assert_prd_on_project`,
    `project_prd_gate.py`) runs FIRST — before any read or write — proving the
    PRD is on THIS project's tenant-scoped manifest. It is fail-closed by
    construction (a manifest read error propagates). The cross-TENANT
    `require_owned_prd` then runs as the SAME second gate `PUT /{id}` keeps.
    Neither gate may be bypassed: a cross-project id is denied 403, a
    cross-tenant id 404. The write NEVER goes through the global cross-tenant-
    only path.

    Membership-gated (`_require_project_member`, AD-P11) like every other
    project route. Auto-snapshots the pre-edit content to `prd_versions` (the
    caller's undo point), mirroring `PUT /{id}`.
    """
    _require_project_member(project_id, ctx)

    dataset = _dataset_for(ctx)
    # ★ cross-PROJECT gate — BEFORE any payload_md read or write.
    try:
        assert_prd_on_project(
            prd_id=body.prd_id, project_id=project_id,
            dataset=dataset, company_id=ctx.company_id,
        )
    except ProjectPrdWriteDenied:
        raise HTTPException(403, "This PRD isn't attached to this project.")

    # ★ cross-TENANT gate (the same check PUT /{id} keeps) — returns the row so
    # the pre-edit content can be snapshotted as an undo point.
    row = require_owned_prd(body.prd_id, ctx.company_id, ctx.workspace_id)
    try:
        save_prd_version(
            body.prd_id, row.get("title", ""), row.get("payload_md", ""),
            saved_by=(getattr(ctx, "user_email", None) or getattr(ctx, "user_id", None) or "auto"),
        )
    except Exception:
        logger.warning(
            "auto-version snapshot failed for prd_id=%s before project drawer "
            "save (undo point not captured)", body.prd_id, exc_info=True,
        )
    return update_prd_content(body.prd_id, body.title, body.html)


@router.post("/{project_id}/artifacts/from-chat")
def save_chat_artifact(
    project_id: int,
    payload: SaveChatArtifactRequest,
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """Promote a chat output into a listed `report` artifact (item-14
    substrate, build spec §2). Membership-gated (AD-P11) same as every
    other artifact route. The report is minted under `ctx.company_id` —
    there is no client-supplied artifact id to validate — so the project
    and the fresh report are same-company by construction (AD-P12); the
    `add_project_artifact` ownership re-check that pre-existing artifacts
    need does not apply here.

    A blank/whitespace-only `content` never reaches the writer (400,
    nothing written). A `None` return from the writer (insert yielded no
    row) is an explicit 502 with no ref written; a raised DB error is left
    to propagate (500) — this is a user-initiated save, not the best-effort
    `report_capture` path, so neither failure is swallowed."""
    _require_project_member(project_id, ctx)
    if not payload.content.strip():
        raise HTTPException(400, "Nothing to save")

    report_id = save_chat_output_as_report(
        content=payload.content,
        company_id=ctx.company_id,
        title=payload.title,
        workspace_id=ctx.workspace_id,
        conversation_id=payload.source_conversation_id,
    )
    if report_id is None:
        raise HTTPException(502, "Could not save chat output as an artifact")

    ref = projects_db.add_artifact(project_id, "report", report_id)
    logger.info(
        "project_chat_artifact_saved project_id=%s report_id=%s",
        project_id, report_id,
    )
    return {"artifact_type": "report", "artifact_id": report_id, **ref}


@router.get("/{project_id}/memory/summary")
def get_memory_summary(project_id: int, ctx: WorkspaceContext = Depends(require_workspace)):
    """The cached synthesized summary, read-only — never triggers an LLM
    call (synthesis is a Phase 2 writer). Membership-gated."""
    _require_project_member(project_id, ctx)
    return memory_db.get_summary(project_id)


@router.get("/{project_id}/memory/insight")
def get_memory_insight(project_id: int, ctx: WorkspaceContext = Depends(require_workspace)):
    """The single latest agent-promoted memory entry, shaped for the
    individual chat's cross-chat INSIGHT turn (design-spec AC7) — a
    distilled note, never a verbatim transcript. `null` when the project
    has no agent-promoted entry yet. Read-only, no LLM call. Membership-
    gated, same as every other memory read. A static subpath declared
    alongside `/memory/summary` — no parametric catch-all in this router
    shadows it."""
    _require_project_member(project_id, ctx)
    return memory_db.get_latest_insight(project_id)


@router.get("/{project_id}/memory")
def list_memory(project_id: int, ctx: WorkspaceContext = Depends(require_workspace)):
    _require_project_member(project_id, ctx)
    return {"entries": memory_db.list_entries(project_id)}


@router.post("/{project_id}/memory")
async def add_memory(
    project_id: int,
    payload: AddMemoryEntryRequest,
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """Add a user-authored memory entry. `author_user_id` is always the
    session user; `promoted_by` stays NULL — agent-promoted entries are a
    Phase 2 writer. Flips an existing summary's `stale` flag, then fires the
    bounded synthesis regen off the request path (`schedule_regen` —
    `app/project_memory.py`; the response below returns before that regen
    completes in prod, AD-P7). Membership-gated."""
    _require_project_member(project_id, ctx)
    entry = memory_db.add_entry(project_id, body=payload.body, author_user_id=ctx.user_id)
    logger.info("memory_entry_added project_id=%s entry_id=%s", project_id, entry["id"])
    schedule_regen(project_id)
    return entry


@router.patch("/{project_id}/memory/{entry_id}")
async def update_memory(
    project_id: int,
    entry_id: int,
    payload: UpdateMemoryEntryRequest,
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """Edit a memory entry's body — scoped to this project; an entry_id
    from another project 404s, unchanged. Membership-gated: a member may
    edit any entry in a project they belong to (v1 all-or-nothing,
    AD-P11), a same-tenant non-member gets 403 first. Fires the bounded
    synthesis regen off the request path on a real edit (`schedule_regen`)."""
    _require_project_member(project_id, ctx)
    entry = memory_db.update_entry(project_id, entry_id, body=payload.body)
    if not entry:
        raise HTTPException(404, "Memory entry not found")
    schedule_regen(project_id)
    return entry


@router.delete("/{project_id}/memory/{entry_id}")
async def delete_memory(
    project_id: int,
    entry_id: int,
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """Remove a memory entry — scoped to this project; an entry_id from
    another project 404s, unchanged. Membership-gated, same as
    `update_memory`. Fires the bounded synthesis regen off the request path
    on a real delete (`schedule_regen`)."""
    _require_project_member(project_id, ctx)
    deleted = memory_db.delete_entry(project_id, entry_id)
    if not deleted:
        raise HTTPException(404, "Memory entry not found")
    schedule_regen(project_id)
    return {"deleted": True}


# ── Group chat ────────────────────────────────────────────────────────
# One `kind='group'` conversation per project, open to every project member
# (AD-P2 — additive, never touches the per-user chat path). Every route here
# is membership-gated via `_require_project_member` (AD-P11 WAVE INVARIANT):
# a same-tenant non-member gets 403, a foreign-tenant project 404s.


@router.post("/{project_id}/group")
def create_group_chat_route(
    project_id: int, ctx: WorkspaceContext = Depends(require_workspace)
):
    """Create-if-absent the project's one group chat and return it.
    Idempotent — a second call returns the SAME conversation (AC1)."""
    _require_project_member(project_id, ctx)
    conversation = conversations_db.create_group_chat(project_id, ctx.user_id)
    logger.info(
        "group_chat_created project_id=%s conversation_id=%s",
        project_id, conversation["id"],
    )
    return conversation


@router.post("/{project_id}/individual")
def create_individual_chat_route(
    project_id: int, ctx: WorkspaceContext = Depends(require_workspace)
):
    """Get-or-create THIS caller's durable individual project chat
    (`kind='individual'`, scoped project_id+user_id) and return it.
    Idempotent per (project, caller) — mirrors `POST /{project_id}/group`
    one level down (per-user rather than per-project).

    This is what gives `ProjectIndividualChat.tsx` ("My chat with
    Sprntly") a real, reusable `conversation_id` to thread into `/v1/ask`:
    without it, every turn from that surface POSTed a fresh, unbound ask,
    so the individual-chat memory-promotion hook
    (`project_id is not None and conversation_id is not None`,
    `ask_job_runner._run_sync`) could never fire, no matter how durable an
    insight the turn produced."""
    _require_project_member(project_id, ctx)
    conversation = conversations_db.create_individual_project_chat(project_id, ctx.user_id)
    logger.info(
        "individual_project_chat_created project_id=%s conversation_id=%s",
        project_id, conversation["id"],
    )
    return conversation


@router.get("/{project_id}/individual/turns")
def list_individual_turns_route(
    project_id: int,
    since: int | None = None,
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """Poll/load read of the CALLER'S OWN individual project chat (read-side
    counterpart of the delegate-tool's cross-user delivery write) — this is
    what makes a delegated brief actually visible to the assignee, not just durably
    written. Resolves `ctx.user_id`'s own conversation server-side; the
    client never supplies a `conversation_id` (defense in depth — the reader
    itself re-checks ownership too, `list_individual_turns`'s own-conversation
    gate). Empty (not 404) when the caller hasn't opened this chat yet —
    nothing has been posted, which is a legitimate read state, not an error,
    mirroring `list_group_turns_route`'s own not-created posture."""
    _require_project_member(project_id, ctx)
    conversation = conversations_db.get_individual_project_chat(project_id, ctx.user_id)
    if not conversation:
        return {"turns": []}
    return {
        "turns": conversations_db.list_individual_turns(
            conversation["id"], ctx.user_id, since=since
        )
    }


@router.get("/{project_id}/individual/unread")
def get_individual_unread_route(
    project_id: int, ctx: WorkspaceContext = Depends(require_workspace)
):
    """Derived unread signal for the CALLER'S OWN individual project chat
    (AD-P3/AD-P20 — inputs-only + derive-at-read; no stored `unread`
    boolean anywhere). Resolves `ctx.user_id`'s own conversation
    server-side; the client never supplies a `conversation_id`/`user_id` —
    same own-conversation posture as `list_individual_turns_route` one
    handler up. No conversation yet (caller hasn't opened this chat) is a
    legitimate, unread-false read state, not an error."""
    _require_project_member(project_id, ctx)
    conversation = conversations_db.get_individual_project_chat(project_id, ctx.user_id)
    if not conversation:
        return {"unread": False, "latest_turn_id": None, "last_read_turn_id": 0}
    conv_id = conversation["id"]
    return {
        "unread": read_cursors_db.unread_for(conv_id, ctx.user_id),
        "latest_turn_id": read_cursors_db.latest_individual_turn_id(conv_id),
        "last_read_turn_id": read_cursors_db.get_cursor(conv_id, ctx.user_id),
    }


@router.post("/{project_id}/individual/read")
def mark_individual_read_route(
    project_id: int, ctx: WorkspaceContext = Depends(require_workspace)
):
    """Advance the CALLER'S OWN read cursor to the latest turn in their own
    individual project chat — clears the rail badge. Advance-only
    (`set_cursor`'s own `max(existing, new)` clamp, AC5): calling this
    twice, or calling it when nothing new has arrived, is a no-op past the
    first advance. No conversation yet → nothing to advance; returns the
    same zero-state shape as the unread route above rather than 404ing (not
    having opened the chat yet is not an error)."""
    _require_project_member(project_id, ctx)
    conversation = conversations_db.get_individual_project_chat(project_id, ctx.user_id)
    if not conversation:
        return {"last_read_turn_id": 0}
    conv_id = conversation["id"]
    latest = read_cursors_db.latest_individual_turn_id(conv_id) or 0
    updated = read_cursors_db.set_cursor(conv_id, ctx.user_id, latest)
    return {"last_read_turn_id": updated["last_read_turn_id"]}


@router.get("/{project_id}/group/turns")
def list_group_turns_route(
    project_id: int,
    since: int | None = None,
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """Poll read (AD-P4 — no realtime in v1): turns after `since` (a turn
    id cursor), ascending, each carrying `author_name`/`author_job_role`.
    Empty (not 404) when the group chat hasn't been created yet — nothing
    has been posted, which is a legitimate poll state, not an error."""
    _require_project_member(project_id, ctx)
    conversation = conversations_db.get_group_chat(project_id)
    if not conversation:
        return {"turns": []}
    return {"turns": conversations_db.list_group_turns(conversation["id"], since=since)}


def _is_solo_project(project_id: int) -> bool:
    """True when the project has exactly ONE human member — the user plus the
    virtual Sprntly agent, no other people. `projects_db.list_members` returns
    HUMAN members only (the AD-P6 agent member is prepended at the route layer,
    never stored), so its length IS the human count and no agent-exclusion is
    needed. Best-effort: any read failure returns False, falling back to the
    normal gate (never widens Sprntly's participation on error)."""
    try:
        return len(projects_db.list_members(project_id)) == 1
    except Exception:  # noqa: BLE001 — a roster read failure must not break the post
        logger.warning("solo_project_check_failed project_id=%s", project_id)
        return False


@router.post("/{project_id}/group/turns")
async def post_group_turn_route(
    project_id: int,
    payload: PostGroupTurnRequest,
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """Post a human turn to the group chat (create-if-absent, same as
    `POST /group`, so a client can post without a separate prior create
    call). A human-to-human turn is a cheap DB write by default. Decision
    order (AD-P7/AD-P10), evaluated only AFTER the human turn has already
    persisted so a gate/reply failure can never block the post:

      1. `@Sprntly` mention → reply, deterministic, no classifier call.
      2. Solo project (exactly one human member) → reply, deterministic, no
         classifier call: with no other human present, every message is for
         Sprntly, so it never "stays out" (fixes the unaddressed-opener
         silence). This bypasses the gate exactly like the mention path.
      3. No mention, multi-human project → consult
         `project_group_gate.should_respond` over the recent clamped
         transcript; `True` replies, `False` leaves the human turn standing
         (the UI's existing "stayed out" affordance shows). The gate's
         conservative AD-P10 posture is unchanged for these projects.

    Every path triggers AT MOST one best-effort agent reply, and the reply
    is BACKGROUNDED (spec §5.5): the gate decision above still runs
    synchronously, right here, before the route returns — only the reply
    ITSELF (`_respond_as_group_agent`, scheduled via `_schedule_group_reply`)
    is fire-and-forget. This route returns as soon as the human turn is
    persisted + broadcast + the gate has decided, never after the reply."""
    _require_project_member(project_id, ctx)
    conversation = conversations_db.create_group_chat(project_id, ctx.user_id)
    turn = conversations_db.post_group_turn(conversation["id"], ctx.user_id, payload.content)
    logger.info(
        "group_turn_posted project_id=%s conversation_id=%s turn_id=%s",
        project_id, conversation["id"], turn.get("id") if turn else None,
    )
    _publish_group_turn_created(project_id, conversation["id"], turn)
    if _MENTION_RE.search(payload.content):
        _schedule_group_reply(project_id, conversation["id"], ctx, trigger_kind="mention")
    elif _is_solo_project(project_id):
        # Solo project (exactly ONE human member + the virtual Sprntly agent):
        # Sprntly responds to EVERY message, no @mention needed and never
        # "stays out" — there is no other human the turn could be addressed to,
        # so the interjection gate's conservative stay-out default is wrong
        # here (an unaddressed opener in a solo project was getting silence).
        # This short-circuits the gate exactly like `mention`/`continuation`.
        # Multi-human projects fall through to the unchanged gate below, so the
        # AD-P10 conservative posture is preserved wherever it still applies.
        _schedule_group_reply(project_id, conversation["id"], ctx, trigger_kind="solo")
    else:
        recent = conversations_db.list_group_turns(conversation["id"])[-_GROUP_CONTEXT_TURNS:]
        # The turn just posted is `recent[-1]`; `recent[-2]` (if any) is the
        # immediately preceding turn. Sprntly authored it (role ==
        # "assistant") ⇒ this human turn may be a direct continuation of
        # the agent's own thread, so the gate bypasses its trivial-chatter
        # pre-filter and a short follow-up ("ok do that") can still trigger
        # a reply. A True decision in that state is a continuation (clear
        # addressee, no disambiguation needed); a True decision with no
        # prior agent turn is an ambiguous gate interjection that may ask
        # who it's for.
        agent_spoke_last = len(recent) >= 2 and (recent[-2].get("role") == "assistant")
        if should_respond(
            project_id, conversation["id"], recent, payload.content,
            agent_spoke_last=agent_spoke_last,
        ):
            trigger_kind = "continuation" if agent_spoke_last else "gate"
            _schedule_group_reply(project_id, conversation["id"], ctx, trigger_kind=trigger_kind)
    return turn


class ProjectChatIntentIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=120_000)
    # When set, prior turns are loaded (ownership-scoped, this caller's OWN
    # individual conversation) so deictic messages ("make it shorter")
    # resolve against the thread — same reason `/v1/chat/intent` takes one.
    conversation_id: int | None = None


@router.post("/{project_id}/chat/intent")
def project_chat_intent(
    project_id: int,
    body: ProjectChatIntentIn,
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """The PRIVATE project chat's classify decision — the project-scoped
    counterpart to `POST /v1/chat/intent` (`routes/chat.py`), giving the
    private surface the same server-side target resolution the GROUP
    surface already has via `_classify_and_maybe_edit_group_prd` below —
    both now share ONE resolve+classify sequence, `resolve_project_chat_
    intent` (single-sourced so the two surfaces can never drift on how a
    project's edit target is found).

    Without this route the private client classified with an EMPTY target
    (`prd_id=None`), so `resolve_chat_intent`'s `_NEEDS_PRD` downgrade
    rewrote every `edit_prd` verdict to `answer` and a project-attached
    PRD edit never reached `POST /{project_id}/prd/chat-edit`. Resolving
    the target here, server-side, fixes that without touching the shared
    `/v1/chat/intent` route or `resolve_chat_intent` itself.

    Membership-gated (`_require_project_member`). The target is resolved
    via `_resolve_prd_id` over THIS project's own PRDs — never a client-
    supplied id, so there is nothing here for a caller to spoof, and the
    classify target always agrees with the write route's own resolution.
    History is loaded ownership-scoped for the caller's own conversation
    via the SAME reader `/v1/chat/intent` uses (`_load_history`); a
    missing/absent `conversation_id` degrades to no-history classification,
    never an error.

    Returns the envelope in the SAME shape `/v1/chat/intent` returns, so
    the client's `dispatchChatIntent` needs no project-specific branch.
    No `open_artifact` lookup leg here — the private thread has no
    artifact viewer to open into; the client already falls that intent
    through to `onAnswer`.
    """
    _require_project_member(project_id, ctx)
    dataset = _dataset_for(ctx)
    history = _load_history(body.conversation_id, ctx.company_id, ctx.user_id)
    envelope, prd_id, refusal = resolve_project_chat_intent(
        project_id, body.message, history, dataset, ctx
    )
    # The `_NEEDS_PRD` downgrade (chat_intent.py) fires whenever a PRD-target
    # intent's target didn't resolve, rewriting the envelope to a plain
    # `answer` with `source="no_target_prd"` — that alone doesn't distinguish
    # "no PRD to edit" (nothing to disambiguate, the honest `answer` stands)
    # from "more than one PRD, which one?" (a genuine choice the caller was
    # never asked to make). Surface the latter as a first-class `clarify`
    # envelope instead of a silent no-op; single-sourced off `_project_prd_
    # ids` + the `_resolve_prd_id` refusal string, same as the group side.
    if envelope.get("source") == "no_target_prd":
        prd_options = _project_prd_ids(project_id, dataset, ctx.company_id)
        if len(prd_options) >= 2:
            envelope["intent"] = "clarify"
            envelope["clarification"] = refusal
            envelope["prd_options"] = prd_options
    envelope["prd_id"] = prd_id
    envelope["prd_title"] = None
    return envelope


def resolve_project_chat_intent(
    project_id: int,
    message: str,
    history: list[dict],
    dataset: str,
    ctx: WorkspaceContext,
) -> tuple[dict, int | None, str | None]:
    """The single-sourced resolve+classify pair BOTH project chat surfaces
    run: resolve the edit target server-side over THIS project's own PRDs
    (`_resolve_prd_id` — never a client/model-supplied id) then classify
    with that target threaded in (`resolve_chat_intent(..., prd_id=prd_id)`)
    so an `edit_prd` verdict survives the `_NEEDS_PRD` downgrade whenever a
    target actually resolves. Returns `(envelope, prd_id, refusal)` —
    callers decide what to do with each (the private route echoes envelope+
    prd_id onto the response; the group classifier gates its own edit-apply
    on prd_id and threads refusal into its no-fabrication fallback note).

    Extracted from what was, pre-refactor, duplicated inline in both the
    private route above and `_classify_and_maybe_edit_group_prd` below —
    this is the ONE place either surface's target resolution can live, so
    they cannot silently diverge.

    `refusal` is `_resolve_prd_id`'s human-readable reason a target did NOT
    resolve (no PRD / more than one PRD on the project) — `None` when a
    target resolved. The group caller uses it to tell an UN-applied edit
    request apart from a plain answer so its fallback reply can ask which
    PRD instead of silently generating a confirmation it never made."""
    prd_id, refusal = _resolve_prd_id({}, project_id, dataset, ctx.company_id)
    envelope = resolve_chat_intent(ctx.company_id, message, history, prd_id=prd_id)
    return envelope, prd_id, refusal


class _GroupEditOutcome(NamedTuple):
    """What one classify-then-maybe-edit pass produced, so the caller can
    tell three cases apart WITHOUT re-classifying or forking a second reply
    path (the single classify-and-edit path stays authoritative for every
    trigger kind — mention, continuation, gate):

    - `applied_turn` not None → a real edit was written in place
      (`apply_chat_edit_scoped` + `prd_versions` snapshot) and the
      completed-past-tense assistant turn is already posted/broadcast; the
      caller returns immediately and never reaches the unified-engine reply.
    - `applied_turn` None AND `was_edit_request` True → the latest turn WAS
      an edit request but nothing was written (flag off, or the target
      would not resolve — zero/ambiguous PRD, `refusal` says which when
      set). The caller falls through to the unified-engine reply and MUST NOT let
      the reply claim an edit happened; `refusal`, when set, lets it ask
      which PRD instead.
    - `applied_turn` None AND `was_edit_request` False → not an edit at all
      (answer/discussion); ordinary unified-engine reply.

    `needs_prd_clarify` — a SEPARATE, content-derived signal, NOT a
    restatement of `refusal` truthiness: True only when THIS turn's own
    classify came back downgraded-for-no-target (`envelope.get("source") ==
    "no_target_prd"` — the model classified a PRD-target intent) AND the
    project genuinely has 2+ PRDs to choose from. `refusal` alone depends
    only on the project's PRD COUNT, not on what the message said, so keying
    the "which PRD?" question off `refusal` truthiness would ask it on every
    ordinary message in any 2+-PRD project — this field exists precisely to
    avoid that over-fire while still surfacing the genuine ambiguity."""
    applied_turn: dict | None
    was_edit_request: bool
    refusal: str | None
    needs_prd_clarify: bool = False


def _classify_and_maybe_edit_group_prd(
    project_id: int,
    conversation_id: int,
    ctx: WorkspaceContext,
    message: str,
    history: list[dict],
    dataset: str,
) -> _GroupEditOutcome:
    """Classify one group turn via `resolve_chat_intent` (reused verbatim,
    spec §Composition — group) and, when the envelope comes back `edit_prd`
    with `PROJECT_PRD_EDIT_ENABLED` on AND a target actually resolves, apply
    the edit through the SAME `apply_chat_edit_scoped` the private surface
    calls (`project_chat_edit` route) — the ★ IDOR gate (`assert_prd_on_project`
    then `require_owned_prd`) fires exactly as it does there. On success,
    persists the result as an assistant turn, broadcasts it via
    `_publish_group_turn_created`, and returns a `_GroupEditOutcome` with
    `applied_turn` set.

    Returns a `_GroupEditOutcome` with `applied_turn=None` for every
    outcome that should fall through to the existing unified-engine reply
    instead — a non-`edit_prd` envelope, the flag off, or an
    unresolved/ambiguous target (`_resolve_prd_id` — NEVER a client/model-
    supplied id — over THIS project's own artifacts) — but ALWAYS reports
    `was_edit_request` truthfully (the latest turn's own intent, independent
    of why nothing got written) so the caller's fallback reply can tell a
    genuine non-edit turn apart from a requested-but-unwritten edit and
    never fabricate a completed change for the latter (B2 no-fabrication).

    `ProjectPrdWriteDenied` (cross-project) and the cross-tenant
    `HTTPException(404)` (from `require_owned_prd`, inside
    `apply_chat_edit_scoped`) PROPAGATE — this function makes ZERO writes on
    either refusal, fail-closed by construction same as the gate itself. The
    caller (`_respond_as_group_agent`) is the one wrapping this in a
    best-effort try/except (AD-P7); this function itself does not swallow."""
    allow_prd_edit = project_prd_edit_enabled()
    envelope, prd_id, refusal = resolve_project_chat_intent(
        project_id, message, history, dataset, ctx
    )
    # Content-derived clarify signal — computed from THIS turn's own classify
    # outcome, never from `refusal` truthiness (which depends only on the
    # project's PRD count, not on what was asked — see `_GroupEditOutcome`'s
    # docstring). `_project_prd_ids` is only read on the branch where the
    # downgrade actually fired, so a plain non-edit message never pays for
    # a manifest read it doesn't need.
    needs_prd_clarify = False
    if envelope.get("source") == "no_target_prd":
        needs_prd_clarify = len(_project_prd_ids(project_id, dataset, ctx.company_id)) >= 2
    was_edit_request = envelope["intent"] == "edit_prd"
    if not was_edit_request or not allow_prd_edit or prd_id is None:
        # Nothing is written on this pass. Report WHETHER the latest turn
        # WAS an edit request (regardless of WHY it didn't apply) so the
        # caller's unified-engine fallback can ask/answer honestly rather
        # than fabricate a "done" (B2 no-fabrication).
        return _GroupEditOutcome(
            applied_turn=None, was_edit_request=was_edit_request, refusal=refusal,
            needs_prd_clarify=needs_prd_clarify,
        )

    result = apply_chat_edit_scoped(
        prd_id, envelope["instruction"], ctx, project_id=project_id, dataset=dataset,
    )
    # The edit was written to `prds.payload_md` in place, versioned, right
    # here — there is no propose/queue/accept step. Word the assistant turn
    # as a COMPLETED, past-tense update ONLY when the editor actually
    # changed something (`sections_changed`); otherwise say plainly that
    # nothing was changed rather than claiming an update (B2 no-fabrication
    # — never misreport an edit as done when it wasn't). `summary` is the
    # editor's own one-line description of WHAT changed; if the model
    # returned nothing usable we fall back to a plain done-message.
    summary = (result.get("summary") or "").strip()
    if result.get("sections_changed"):
        narration = f"Done — I've updated the PRD. {summary}".strip() if summary \
            else "Done — I've updated the PRD."
    else:
        narration = summary or "I didn't find anything in the PRD to change for that."
    assistant_turn = conversations_db.post_group_turn(
        conversation_id, None, narration, role="assistant"
    )
    _publish_group_turn_created(project_id, conversation_id, assistant_turn)
    return _GroupEditOutcome(applied_turn=assistant_turn, was_edit_request=True, refusal=None)


# Addressing notes appended to the group agent's reply system prompt, keyed
# by how the turn triggered a reply. Only the ambiguous `gate` case invites
# the "are you assigning this to me?" disambiguation; a literal @Sprntly or
# a clear continuation of the agent's own thread is unambiguously for the
# agent, so those explicitly SUPPRESS the question.
_ADDRESSING_NOTES = {
    "mention": (
        "ADDRESSING: The latest turn tagged you with @Sprntly — it is "
        "clearly directed at you. Answer it directly; do NOT ask whether "
        "it is meant for you."
    ),
    "continuation": (
        "ADDRESSING: The latest turn is a direct continuation of your own "
        "last message (a reply or follow-up to what you just said) — it is "
        "clearly directed at you. Answer it directly; do NOT ask whether "
        "it is meant for you."
    ),
    "solo": (
        "ADDRESSING: You are the only non-human member of this project and "
        "there is exactly one human here, so every message is for you. Answer "
        "it directly; do NOT ask whether it is meant for you."
    ),
    "gate": (
        "ADDRESSING: The latest turn did not tag you with @Sprntly and is "
        "not a direct reply to your own last message. If it is genuinely "
        "ambiguous whether it is directed at you or at a human teammate "
        "(for example \"can you handle the export section?\" in a thread "
        "with several members and no clear addressee), do NOT assume it is "
        "for you and do NOT act or delegate — reply by briefly asking to "
        "confirm, e.g. \"Are you assigning this to me?\". Only answer or "
        "act normally if it is clearly meant for you."
    ),
}


def _respond_as_group_agent(
    project_id: int, conversation_id: int, ctx: WorkspaceContext,
    trigger_kind: str = "mention",
) -> None:
    """Called on an `@Sprntly` mention OR a `should_respond=True`
    smart-interjection decision (`post_group_turn_route` decides which, and
    derives `trigger_kind` — "mention" / "continuation" / "gate" / "solo" —
    for THIS call, then BACKGROUNDS this whole call via
    `_schedule_group_reply` — spec §5.5; this function itself stays SYNC and
    runs on the threadpool, never on the event loop). This function's own
    body runs the SAME single classify-and-edit path first regardless of
    `trigger_kind`: classify the triggering turn via
    `_classify_and_maybe_edit_group_prd` — a real edit applies in place and
    returns; a requested-but-unwritten edit steers the fallback reply with
    an `edit_note` so it never fabricates a completed change (B2
    no-fabrication); everything else (including `answer` and any
    generate/open phrasing — group generate/open is DEFERRED, spec ⭐)
    falls through to assemble recent group-turn context (each speaker
    tagged with their `author_name`/`author_job_role`) and produce ONE
    assistant turn (`role='assistant', author_user_id=NULL`) via the
    unified answer engine (`qa_agent.answer`, scoped to this project —
    RELOCATED from this function's own former inline `run_tool_loop` body,
    not reimplemented). Never raises (AD-P7 best-effort contract) — a
    failure (including a refused edit) yields no assistant turn and the
    human turn that triggered this already persisted, so the chat is never
    blocked.

    The reply carries the `delegate_task`/`execute_task`/read tools —
    zero new LLM calls: delegation and task execution piggyback on this
    same reply. `ctx` is threaded in only to derive `dataset`/`company_id`
    for the project tool handlers' artifact fold-in; reply/promotion
    behavior is otherwise unchanged.

    After a reply is actually produced, runs the best-effort memory-
    promotion classifier (`maybe_promote_turn`) over the same clamped
    transcript — reusing it rather than re-querying. `maybe_promote_turn`
    is itself never-raising, so this call cannot turn a successful reply
    into a failure; it only ever runs on the agent-reply path, never on a
    human-to-human turn or a structured edit_prd dispatch."""
    try:
        recent = conversations_db.list_group_turns(conversation_id)[-_GROUP_CONTEXT_TURNS:]
        transcript = render_group_transcript(recent)
        # The human who addressed Sprntly — the most recent turn with an
        # author_user_id (an agent turn has none). Used as the delegation
        # assigner if the model calls delegate_task on this reply, AND as
        # the message classified below.
        trigger = next((t for t in reversed(recent) if t.get("author_user_id")), None)
        assigner_user_id = trigger["author_user_id"] if trigger else None
        source_turn_id = trigger["id"] if trigger else None
        dataset = _dataset_for(ctx)

        # Every trigger kind (mention, continuation, gate, solo) runs the
        # ONE classify-then-edit path first — an applicable edit is applied
        # in place here and we return; anything else falls through to the
        # SAME unified-engine reply below. `edit_note` carries forward
        # whether this turn was an un-applied edit request so the fallback
        # reply can ask/answer honestly instead of fabricating a completed
        # edit (B2 no-fabrication).
        edit_note = ""
        if trigger is not None:
            history = [
                {"role": t.get("role") or "user", "content": t.get("content") or ""}
                for t in recent
                if t is not trigger
            ]
            edit = _classify_and_maybe_edit_group_prd(
                project_id, conversation_id, ctx, trigger["content"], history, dataset,
            )
            if edit.applied_turn is not None:
                return
            if edit.needs_prd_clarify:
                # Content-derived signal (NOT `edit.refusal` truthiness — see
                # `_GroupEditOutcome`'s docstring): this turn asked to edit
                # the PRD and the project genuinely has 2+ PRDs to choose
                # from. Ask which one via the single-sourced listing
                # (`_project_prd_ids` + the `_resolve_prd_id` refusal
                # string) instead of silently answering.
                listing = (edit.refusal or "more than one PRD exists on this project").rstrip(".")
                edit_note = (
                    "EDIT STATUS: The latest turn asked to change the PRD, but "
                    f"{listing}. You cannot edit the PRD in this reply. Do NOT "
                    "say you added, updated, or changed anything. Ask which "
                    "PRD is meant before doing anything else."
                )
            elif edit.was_edit_request:
                # An edit was asked for but NOT written for some OTHER reason
                # (flag off, or a target that failed to resolve without a
                # genuine 2+-PRD choice — e.g. zero PRDs). The reply has no
                # edit tool, so it must not claim a change was made; steer it
                # to explain.
                reason = (edit.refusal or "the edit could not be applied").rstrip(".")
                edit_note = (
                    "EDIT STATUS: The latest turn asked to change the PRD, but "
                    f"NO edit was made on this turn ({reason}). You cannot edit "
                    "the PRD in this reply. Do NOT say you added, updated, or "
                    "changed anything. Explain briefly what's needed."
                )

        roster = projects_db.list_members(project_id)

        # Inject the bounded project-context block (best-effort, never
        # raises) onto the roster system prompt, and hand the agent the
        # read tools alongside delegate_task/execute_task so it can answer
        # AND act on demand — exactly the six tools the private surface
        # carries, so both project surfaces stay single-sourced on the
        # unified engine's tool set.
        context_block = project_group_context.assemble_group_agent_context(
            project_id, dataset, ctx.company_id
        )
        addressing = _ADDRESSING_NOTES.get(trigger_kind, _ADDRESSING_NOTES["mention"])
        system_parts = [_group_system_with_roster(roster), addressing]
        if edit_note:
            system_parts.append(edit_note)
        from app.project_group_context import _instructions_block

        try:
            instructions = projects_db.get_instructions(project_id)
        except Exception:  # noqa: BLE001 — best-effort, AD-P7
            instructions = None
        instr_block = _instructions_block(instructions)
        if instr_block:
            system_parts.append(instr_block)

        # LT-8 input-shape switch (build-spec §Group) — `question` is either
        # the full attributed transcript or just the latest triggering
        # message; `prerendered_transcript` ALWAYS carries the full
        # attributed transcript either way, so the model never loses the
        # thread — it is never re-flattened into `answer()`'s single-user
        # history model (Invariant 4).
        question = (
            transcript if _GROUP_TRANSCRIPT_AS_QUESTION
            else (trigger["content"] if trigger else transcript)
        )
        scope = SurfaceScope(
            surface=Surface.project_group,
            project_id=project_id,
            context_payload=context_block,
            system_addendum="\n\n".join(system_parts),
            extra_tools=(
                project_delegation.DELEGATE_TASK_TOOL,
                project_task_execution.EXECUTE_TASK_TOOL,
                *project_group_context.read_tools(),
            ),
            roster=tuple(roster),
            assigner_identity={
                "assigner_user_id": assigner_user_id,
                "source_turn_id": source_turn_id,
                # Carried for the cost-log identifier only (qa_agent's
                # sixth branch) — matches the pre-collapse group log line's
                # identifier shape (project_id + conversation_id).
                "conversation_id": conversation_id,
            },
            post_turn=lambda content: conversations_db.post_group_turn(
                conversation_id, None, content, role="assistant"
            ),
            prerendered_transcript=transcript,
            capabilities={"streaming": False, "cancel": False},
            multi_party=True,
        )
        result = qa_agent.answer(
            enterprise_id=ctx.company_id, question=question, dataset=dataset,
            scope=scope,
        )
        reply = (result or {}).get("answer", "")
        assistant_turn = conversations_db.post_group_turn(
            conversation_id, None, reply, role="assistant"
        )
        _publish_group_turn_created(project_id, conversation_id, assistant_turn)
        maybe_promote_turn(project_id, conversation_id, transcript)
    except Exception as exc:  # noqa: BLE001 — best-effort, AD-P7: never block the chat
        logger.warning(
            "group_chat_mention_reply_failed project_id=%s conversation_id=%s error=%s",
            project_id, conversation_id, type(exc).__name__,
        )


# ── Delegation ledger (walking skeleton — the accountability ledger's one
# mutating surface, AD-P29) ─────────────────────────────────────────────


def _ledger_row_dto(row: dict, members: dict[str, str | None], view: str) -> dict:
    """Shape one `v_delegation_status` row into the ledger's read DTO.
    `other_party` is the ASSIGNER for `assigned_to_me` (the caller is the
    assignee) and the ASSIGNEE for `waiting_on` (the caller is the
    assigner); the name resolves via `list_members` — no PII beyond the
    roster name already shown elsewhere in the product."""
    other_party_id = (
        row["assigner_user_id"] if view == "assigned_to_me" else row["assignee_user_id"]
    )
    return {
        "delegation_id": row["delegation_id"],
        "task_summary": row["task_summary"],
        "status": row["status"],
        "status_at": row["status_at"],
        "bucket": "done" if row["status"] in delegation_events_db.CLOSED_STATES else "open",
        "other_party_user_id": other_party_id,
        "other_party_name": members.get(other_party_id),
        "delivered_conversation_id": row["delivered_conversation_id"],
        "delivered_turn_id": row["delivered_turn_id"],
    }


@router.post("/{project_id}/delegations/{delegation_id}/events")
def emit_delegation_event_route(
    project_id: int,
    delegation_id: int,
    payload: EmitDelegationEventRequest,
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """Append one lifecycle event, gated by FOUR fail-closed server checks,
    in this exact order — the ledger's IDOR-critical surface:

      1. membership (`_require_project_member`) — tenant + membership.
      2. delegation-in-project — a `delegation_id` that does not exist, or
         belongs to a DIFFERENT project (or tenant), 404s opaquely; a
         cross-project id's existence is never disclosed.
      3. party-role — the caller must be the correct PARTY for the
         requested event (`EVENT_PARTY`); `assigned` (server-only genesis)
         or any unknown event has no party at all and 422s before any
         state is revealed.
      4. transition-legality — the requested event must be a legal edge
         from the delegation's CURRENT derived status
         (`is_legal_transition`); an illegal edge 409s.

    Nothing is written unless all four pass — `record_event` is the last
    line before the return."""
    _require_project_member(project_id, ctx)                                   # GATE 1
    deleg = delegation_events_db.load_delegation_for_authz(delegation_id)
    if deleg is None or deleg["project_id"] != project_id:                     # GATE 2
        raise HTTPException(404, "Delegation not found")
    party = delegation_events_db.EVENT_PARTY.get(payload.event)                # GATE 3
    if party is None:
        raise HTTPException(422, "Unknown or non-emittable event")
    if deleg[f"{party}_user_id"] != ctx.user_id:
        raise HTTPException(403, "Not the correct party for this event")
    current = delegation_events_db.current_status(delegation_id)               # GATE 4
    if not delegation_events_db.is_legal_transition(current, payload.event):
        raise HTTPException(409, "Illegal transition")
    delegation_events_db.record_event(
        delegation_id=delegation_id,
        event=payload.event,
        actor_user_id=ctx.user_id,
        note=payload.note,
    )
    logger.info(
        "delegation_event_emitted delegation_id=%s event=%s actor=%s",
        delegation_id, payload.event, ctx.user_id,
    )  # ids only, never note text
    # Live dual per-user publish (AD-P30/AD-P22) — best-effort: the event is
    # already recorded, so a publish failure never fails the emit and the
    # response body is identical whether the publish succeeds or not. The DTO
    # goes to the assigner's and assignee's per-user channels ONLY, NEVER the
    # group channel `project:{id}` (a decline/cancel is private).
    try:
        dto = delegation_events_db.status_dto(delegation_id)
        if dto is not None:
            project_delegation._publish_delegation_event(
                project_id=project_id,
                assigner_user_id=deleg["assigner_user_id"],
                assignee_user_id=deleg["assignee_user_id"],
                dto=dto,
            )
    except Exception as exc:  # noqa: BLE001 — best-effort, AD-P22: realtime never blocks a recorded event
        logger.warning(
            "delegation_event_publish_prep_failed delegation_id=%s error_class=%s",
            delegation_id, type(exc).__name__,
        )
    return {
        "delegation_id": delegation_id,
        "status": delegation_events_db.current_status(delegation_id),
    }


@router.get("/{project_id}/delegations")
def list_delegations_route(
    project_id: int,
    view: str,
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """Party-filtered ledger reads (AD-P29) — a member never sees a
    delegation they are not a party to; there is no "all project
    delegations" read on this surface."""
    _require_project_member(project_id, ctx)
    if view == "assigned_to_me":
        rows = delegation_events_db.list_status_for_assignee(project_id, ctx.user_id)
    elif view == "waiting_on":
        rows = delegation_events_db.list_status_for_assigner(project_id, ctx.user_id)
    else:
        raise HTTPException(422, "Unknown view")
    members = {m["user_id"]: m.get("name") for m in projects_db.list_members(project_id)}
    return [_ledger_row_dto(r, members, view) for r in rows]


@router.get("/{project_id}/delegations/counts")
def delegation_counts_route(
    project_id: int,
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """Open-only ledger counts for the project rail card — the same derive-
    and-count shape as `individual/unread`. `reopened` counts as open;
    `completed`/`declined`/`cancelled` do not."""
    _require_project_member(project_id, ctx)
    assigned_to_me = delegation_events_db.list_status_for_assignee(project_id, ctx.user_id)
    waiting_on = delegation_events_db.list_status_for_assigner(project_id, ctx.user_id)
    return {
        "assigned_to_me_open": sum(
            1 for r in assigned_to_me if r["status"] in delegation_events_db.OPEN_STATES
        ),
        "waiting_on_open": sum(
            1 for r in waiting_on if r["status"] in delegation_events_db.OPEN_STATES
        ),
    }
