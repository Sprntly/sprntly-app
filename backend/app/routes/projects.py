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

import logging
import re
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import WorkspaceContext, require_workspace
from app.db import conversation_read_cursors as read_cursors_db
from app.db import conversations as conversations_db
from app.db import delegation_events as delegation_events_db
from app.db import project_memory_entries as memory_db
from app.db import projects as projects_db
from app.db import team as team_db
from app.db import workspaces as workspaces_db
from app.db.artifacts import list_artifacts_for_company, list_artifacts_for_project
from app.db.companies import get_seat_limit
from app.team_email import send_invite_email
from app.deps.ownership import require_owned_evidence, require_owned_prd
from app.llm import DEFAULT_MODEL, run_tool_loop
from app.llm_telemetry import RunUsage, log_llm_run
from app import project_delegation
from app import project_group_context
from app.project_chat_edit import apply_chat_edit_scoped
from app.project_prd_gate import ProjectPrdWriteDenied
from app.project_prd_patch_tool import (
    PROPOSE_PROJECT_PRD_PATCH_TOOL,
    _resolve_prd_id,
    handle_propose_prd_patch,
    project_prd_edit_enabled,
)
from app.realtime import publish_broadcast
from app.project_artifact_capture import save_chat_output_as_report
from app.project_from_prd import find_existing_prd_auto_project
from app.project_group_gate import render_group_transcript, should_respond
from app.project_memory import maybe_promote_turn, schedule_regen
from app.routes.chat import _dataset_for

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

You KNOW this project. The PROJECT CONTEXT block below gives you the
project's shared memory, its members (the roster), its open tasks (the
delegation ledger), and its artifacts (PRDs, prototypes, evidence,
reports). Answer questions about any of these directly — never say you
"can't see" the team's files, tasks, or members. For the FULL detail
behind the summary, use your read tools: get_project_memory,
list_project_artifacts, get_artifact_content (to read a specific PRD/
report/evidence body), and get_task_ledger. Every one of these is scoped
to THIS project only. When someone asks what a document says, call
get_artifact_content and answer from the real content.
"""


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

router = APIRouter(prefix="/v1/projects", tags=["projects"])


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


class ProjectChatEditIn(BaseModel):
    instruction: str = Field(..., min_length=3, max_length=4000)


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
    target resolution: the edit target is resolved SERVER-side over THIS
    project's own artifacts via `_resolve_prd_id` (single-PRD auto-select /
    ambiguous-disambiguate) — never a client-supplied `prd_id`, so there is
    nothing here for a caller to spoof. 0/ambiguous PRDs make no write and
    return a no-edit, answer-shaped payload (`{"edited": false, "answer"}`)
    instead of an error, so the private chat can degrade to a grounded ask.

    `apply_chat_edit_scoped` then runs the ★ cross-project IDOR gate
    (`assert_prd_on_project`) before any read/write — defense in depth here
    (the resolved id is already this project's own), and the SAME single-
    sourced gate a future group-chat write path will call with a
    less-trusted target. A `ProjectPrdWriteDenied` from that gate is caught
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
    prd_id, refusal = _resolve_prd_id({}, project_id, dataset, ctx.company_id)
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


@router.post("/{project_id}/group/turns")
def post_group_turn_route(
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
      2. No mention → consult `project_group_gate.should_respond` over the
         recent clamped transcript; `True` replies, `False` leaves the
         human turn standing (the UI's existing "stayed out" affordance
         shows).

    Either path triggers AT MOST one best-effort agent reply."""
    _require_project_member(project_id, ctx)
    conversation = conversations_db.create_group_chat(project_id, ctx.user_id)
    turn = conversations_db.post_group_turn(conversation["id"], ctx.user_id, payload.content)
    logger.info(
        "group_turn_posted project_id=%s conversation_id=%s turn_id=%s",
        project_id, conversation["id"], turn.get("id") if turn else None,
    )
    _publish_group_turn_created(project_id, conversation["id"], turn)
    if _MENTION_RE.search(payload.content):
        _respond_as_group_agent(project_id, conversation["id"], ctx)
    else:
        recent = conversations_db.list_group_turns(conversation["id"])[-_GROUP_CONTEXT_TURNS:]
        if should_respond(project_id, conversation["id"], recent, payload.content):
            _respond_as_group_agent(project_id, conversation["id"], ctx)
    return turn


def _respond_as_group_agent(
    project_id: int, conversation_id: int, ctx: WorkspaceContext
) -> None:
    """Called on an `@Sprntly` mention OR a `should_respond=True`
    smart-interjection decision (`post_group_turn_route` decides which;
    this function's own body is unchanged either way): assemble recent
    group-turn context (each speaker tagged with their
    `author_name`/`author_job_role`) and produce ONE assistant turn
    (`role='assistant', author_user_id=NULL`). Never
    raises (AD-P7 best-effort contract) — a failure yields no assistant
    turn and the human turn that triggered this already persisted, so the
    chat is never blocked. Meters ONLY this call (the structured
    cost-summary log line — never emitted for a human-to-human turn,
    because none is made for one).

    The reply call is a `run_tool_loop` (AD-P15) carrying the
    `delegate_task` tool — zero new LLM calls: delegation piggybacks on
    this same reply. `ctx` is threaded in only to derive
    `dataset`/`company_id` for `project_delegation.handle_delegate_task`'s
    artifact fold-in; reply/promotion behavior is otherwise unchanged.

    After a reply is actually produced, runs the best-effort memory-
    promotion classifier (`maybe_promote_turn`) over the same clamped
    transcript — reusing it rather than re-querying. `maybe_promote_turn`
    is itself never-raising, so this call cannot turn a successful reply
    into a failure; it only ever runs on the agent-reply path, never on a
    human-to-human turn."""
    start = time.monotonic()
    try:
        recent = conversations_db.list_group_turns(conversation_id)[-_GROUP_CONTEXT_TURNS:]
        transcript = render_group_transcript(recent)
        # The human who addressed Sprntly — the most recent turn with an
        # author_user_id (an agent turn has none). Used as the delegation
        # assigner if the model calls delegate_task on this reply.
        trigger = next((t for t in reversed(recent) if t.get("author_user_id")), None)
        assigner_user_id = trigger["author_user_id"] if trigger else None
        source_turn_id = trigger["id"] if trigger else None
        roster = projects_db.list_members(project_id)
        dataset = _dataset_for(ctx)
        # Behind PROJECT_PRD_EDIT_ENABLED (default off): the group agent may also
        # propose a PRD edit against a PRD on THIS project. Same plain
        # run_tool_loop tool + §C IDOR gate + workspace_id=company_id as the
        # private chat, so a group chat can never patch another project's PRD.
        allow_prd_edit = project_prd_edit_enabled()
        meta: dict = {}

        def _dispatch(name: str, tool_input: dict) -> str:
            # Project-scoped read tools first (breadth+depth project awareness);
            # returns None when `name` isn't one of them, so delegate_task and
            # the unknown-tool fallback still apply.
            read = project_group_context.dispatch_read_tool(
                name, tool_input,
                project_id=project_id, dataset=dataset, company_id=ctx.company_id,
            )
            if read is not None:
                return read
            if name == "delegate_task":
                return project_delegation.handle_delegate_task(
                    project_id=project_id,
                    assigner_user_id=assigner_user_id,
                    source_conversation_id=conversation_id,
                    source_turn_id=source_turn_id,
                    roster=roster,
                    dataset=dataset,
                    company_id=ctx.company_id,
                    tool_input=tool_input,
                )
            if allow_prd_edit and name == "propose_prd_patch":
                return handle_propose_prd_patch(
                    tool_input,
                    project_id=project_id, dataset=dataset,
                    company_id=ctx.company_id, workspace_id=ctx.company_id,
                )
            return f"(unknown tool: {name})"

        # Inject the bounded project-context block (best-effort, never raises)
        # onto the roster system prompt, and hand the agent the read tools
        # alongside delegate_task so it can answer AND retrieve on demand.
        context_block = project_group_context.assemble_group_agent_context(
            project_id, dataset, ctx.company_id
        )
        system = f"{_group_system_with_roster(roster)}\n\n{context_block}"
        tools = [project_delegation.DELEGATE_TASK_TOOL, *project_group_context.read_tools()]
        if allow_prd_edit:
            tools.append(PROPOSE_PROJECT_PRD_PATCH_TOOL)
        reply = run_tool_loop(
            system=system,
            user=transcript,
            tools=tools,
            dispatch=_dispatch,
            model=DEFAULT_MODEL,
            meta_out=meta,
        )
        assistant_turn = conversations_db.post_group_turn(
            conversation_id, None, reply, role="assistant"
        )
        _publish_group_turn_created(project_id, conversation_id, assistant_turn)
        usage = RunUsage(
            cache_creation_input_tokens=meta.get("cache_creation_input_tokens", 0),
            cache_read_input_tokens=meta.get("cache_read_input_tokens", 0),
            input_tokens=meta.get("input_tokens", 0),
            output_tokens=meta.get("output_tokens", 0),
        )
        log_llm_run(
            operation="projects.group_chat.mention_reply",
            identifier={"project_id": project_id, "conversation_id": conversation_id},
            usage=usage,
            duration_ms=int((time.monotonic() - start) * 1000),
            status="complete",
            model=meta.get("model") or DEFAULT_MODEL,
            mode="group",
        )
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
