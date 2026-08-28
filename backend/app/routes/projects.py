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
"""
from __future__ import annotations

import logging
import sys
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import qa_agent
from app.ask_job_runner import ExecutionOutcome, run_execution_job
from app.auth import WorkspaceContext, require_workspace
from app.chat_intent import resolve_chat_intent
from app.db import asks as asks_db
from app.db.asks import start_ask_job, touch_ask_job
from app.db import conversations as conversations_db
from app.db import delegation_events as delegation_events_db
from app.db import project_delegations as project_delegations_db
from app.db import project_memory_entries as memory_db
from app.db import projects as projects_db
from app.db import team as team_db
from app.db import workspaces as workspaces_db
from app.db.artifacts import list_artifacts_for_company, list_artifacts_for_project
from app.db.companies import get_seat_limit
from app.db.prds import save_prd_version, update_prd_content
from app.team_email import send_invite_email
from app.deps.ownership import require_owned_evidence, require_owned_prd
from app import drip_email
from app import project_delegation
from app import project_join_greeting
from app import project_task_execution
from app.project_chat_edit import apply_chat_edit_scoped
from app.project_prd_gate import ProjectPrdWriteDenied, assert_prd_on_project
from app.realtime import publish_broadcast
from app.project_artifact_capture import save_chat_output_as_report
from app.project_from_prd import find_existing_prd_auto_project
from app.project_origin_seed import seed_project_origin_memory
from app.project_title import generate_project_title
from app.delegation_status_ingest import maybe_ingest_status, notify_requester_task_completed
from app.project_memory import maybe_promote_turn, schedule_regen
from app.chat_envelope import enrich_chat_envelope
from app.report_capture import capture_report
from app.routes.ask import _load_history
from app.routes.chat import _dataset_for
from app.surface_scope import PROJECT_TOOL_NUDGE, Surface, SurfaceScope

logger = logging.getLogger(__name__)


def _is_unique_violation(exc: Exception) -> bool:
    """True for a Postgres/PostgREST unique-constraint violation (23505) — or
    the FakeSupabaseClient's sqlite `IntegrityError` equivalent."""
    import sqlite3

    if isinstance(exc, sqlite3.IntegrityError):
        return True
    return getattr(exc, "code", None) == "23505" or "duplicate key" in str(exc).lower()


router = APIRouter(
    prefix="/v1/projects",
    tags=["projects"],
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
    # Optional grounding text for the origin "why" memory seed: the
    # creator's first message/instructions for a manual project, or an
    # excerpt of the seeding artifact for an artifact-origin project.
    # Ignored for prd_auto (that origin seeds via the chat-time fork hook,
    # `app/project_from_prd.py`, unchanged). Not yet sent by the create
    # modal — falls back to a name-only deterministic brief
    # (`project_origin_seed._fallback_brief_generic`) until a future ticket
    # wires the field into the create-modal UI.
    seed_text: str | None = Field(default=None, max_length=4000)


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
    artifact_type: Literal["prd", "evidence", "prototype", "report", "ticket_set", "custom_artifact"]
    artifact_id: int = Field(..., ge=1)


class SaveChatArtifactRequest(BaseModel):
    content: str = Field(..., min_length=1)
    title: str | None = None
    source_conversation_id: int | None = None


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
    name = payload.name
    if payload.origin == "prd_auto" and payload.prd_id is not None:
        existing_id = find_existing_prd_auto_project(payload.prd_id, ctx.company_id)
        if existing_id is not None and projects_db.project_belongs_to_company(
            existing_id, ctx.company_id, ctx.workspace_id
        ):
            existing = projects_db.get_project(existing_id)
            if existing is not None:
                return existing
        # The "Auto · from PRD" tab forwards the PRD's own title as `name`.
        # Name the project for what the PRD is ABOUT instead — the same shared
        # derivation the generation-time hook uses. Best-effort: falls back to
        # the incoming `payload.name` (the PRD title) on any failure.
        name = generate_project_title(prd_id=payload.prd_id, fallback_title=payload.name)

    project = projects_db.create_project(
        company_id=ctx.company_id,
        workspace_id=ctx.workspace_id,
        name=name,
        created_by=ctx.user_id,
        origin=payload.origin,
    )
    logger.info("project_created project_id=%s", project["id"])
    if payload.origin != "prd_auto" and payload.seed_text:
        # Seed the new project's memory with a grounded "why" — the
        # prd_auto origin already gets this from the chat-time fork hook
        # (`app/project_from_prd.py`) when it runs; a project created here
        # with prd_auto (the create-modal's own "Auto · from PRD" tab, a
        # DIFFERENT path from that hook) has no conversation to seed from,
        # so it is left as-is rather than seeding a thin, conversation-less
        # brief under the same origin label.
        #
        # Gated on `payload.seed_text` being present: the create modal does
        # not send it yet (a future ticket wires the field into the UI), so
        # this call is inert today — no side effect on the many existing
        # tests/flows that create a manual/artifact project with no
        # instructions. Once a caller DOES send `seed_text`, the seed's own
        # `_fallback_brief_generic` floor still applies inside
        # `seed_project_origin_memory` for the (rarer) case where the text
        # turns out to be pure whitespace. `seed_project_origin_memory` is
        # best-effort and never raises (AD-P7) — no extra try/except needed
        # at this call site.
        seed_project_origin_memory(
            project_id=project["id"],
            origin=payload.origin,
            project_name=name,
            seed_text=payload.seed_text,
        )
    return project


@router.get("/{project_id}")
def get_project(project_id: int, ctx: WorkspaceContext = Depends(require_workspace)):
    """Project detail: the row and human members (+ prepended virtual agent
    member, AD-P6). Membership-gated (403 for a same-tenant non-member) —
    the virtual agent member is irrelevant to this human-caller gate."""
    project = _require_project_member(project_id, ctx)
    members = projects_db.list_members(project_id)
    return {
        **project,
        "members": [dict(_AGENT_MEMBER), *members],
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


def _notify_added_to_project(
    *, email: str | None, recipient_name: str | None, project_id: int, project_name: str | None
) -> None:
    """Best-effort "you've been added to project X" email for a NET-NEW
    existing-user add (POST /members, /tag t_workspace). Brand-new email
    invitees already get the branded invite; this closes the gap for an
    existing in-tenant user, who previously got nothing. Fully swallowed
    (AD-P22): a missing key no-ops inside the sender, and any failure here
    never breaks or delays the add. Sends the project NAME + a deep link to
    it, never project content (AD-TNM2)."""
    if not email:
        return
    try:
        from app import config as config_mod

        base = (getattr(config_mod.settings, "frontend_url", "") or "https://app.sprntly.ai").rstrip("/")
        project_url = f"{base}/projects?id={project_id}"
        drip_email.send_project_added_email(
            to_email=email,
            project_name=project_name or "",
            project_url=project_url,
            recipient_name=recipient_name or "",
        )
    except Exception:  # noqa: BLE001 — best-effort, never blocks the add
        logger.warning("project_added_email_failed project_id=%s", project_id)


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
    project = _require_project_member(project_id, ctx)
    res = projects_db.resolve_candidate(project_id, payload.email)
    tier = res["tier"]
    if tier == projects_db.TIER_MEMBER:
        return res["member"]  # idempotent — already a member, byte-identical
    if tier in (projects_db.TIER_WORKSPACE, projects_db.TIER_COMPANY):
        member = projects_db.add_member(project_id, res["user_id"])
        logger.info("project_member_added project_id=%s user_id=%s", project_id, res["user_id"])
        # Best-effort live landing on the added person's OWN per-user channel
        # (AD-TNM5) — mirrors the /tag route's TIER_WORKSPACE branch so BOTH add
        # paths emit `member.added` and an already-logged-in existing user lands
        # live in the project. Swallows every failure, never changes this
        # response or rolls back the add (AD-TNM2/AD-P22).
        project_delegation._publish_member_added(project_id, res["user_id"], project["name"])
        # NEW-membership only (the TIER_MEMBER re-add branch returned above):
        # drop a grounding greeting into this member's private project chat so
        # they land with context, not a blank thread. Best-effort/non-blocking
        # (AD-P7) — a greeting failure never breaks or delays the add.
        project_join_greeting.post_join_greeting(
            project_id, res["user_id"], dataset=_dataset_for(ctx), company_id=ctx.company_id
        )
        # NET-NEW existing-user add → the "added to project X" email (best-effort;
        # a re-add returned at the TIER_MEMBER branch above, so it never fires).
        _notify_added_to_project(
            email=res.get("email"), recipient_name=res.get("name"),
            project_id=project_id, project_name=project["name"],
        )
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
        # Same grounding greeting `add_member` drops for a new membership —
        # this tag-invite branch adds a member too and previously skipped it,
        # leaving a tag-added member with a blank private chat. Best-effort/
        # non-blocking (AD-P7) — a greeting failure never breaks or delays
        # the add.
        project_join_greeting.post_join_greeting(
            project_id, uid, dataset=_dataset_for(ctx), company_id=ctx.company_id
        )
        # NET-NEW existing-user add → the "added to project X" email (best-effort).
        # The t_company / t_newuser tiers below already send an invite email, so
        # only this direct-add branch needs it.
        _notify_added_to_project(
            email=res.get("email"), recipient_name=res.get("name"),
            project_id=project_id, project_name=project["name"],
        )
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
        # Self-exclude: the picker is only used to find someone ELSE to
        # delegate/mention/add — every caller of this route reads the
        # on-project roster elsewhere, so dropping the caller loses nothing.
        if user_id == ctx.user_id:
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
    # The PRD open in the artifact drawer beside this chat — the EXPLICIT
    # edit target (parity with main chat's tab-bound `prd_id`,
    # `routes/chat.py:87`). There is no server-side inference: `None` means
    # no PRD is open, and the route returns the simple "open a PRD" clarify
    # rather than guessing or disambiguating across the project's PRDs.
    # `apply_chat_edit_scoped` runs the ★ cross-project
    # (`assert_prd_on_project`) + cross-tenant (`require_owned_prd`) gate on
    # WHATEVER `prd_id` reaches it, before any read/write — this is the
    # PRIMARY defense against a client naming a PRD on a different project.
    prd_id: int | None = Field(default=None, ge=1)
    # The idempotency key a retry/double-submit carries for the owned
    # both-sides persist below — client-issued when the sender's engine
    # mints one; the route mints a server uuid4 when absent so persistence
    # is never skipped for an older client.
    client_message_id: str | None = None


@router.post("/{project_id}/prd/chat-edit")
def project_chat_edit(
    project_id: int,
    body: ProjectChatEditIn,
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """The private project chat's PRD-edit write path — applies
    directly through the SAME `apply_chat_edit_scoped` the main chat's
    `POST /v1/prd/{id}/chat-edit` calls, no confirm step.

    Membership-gated (`_require_project_member`), THEN the explicit open-drawer
    target:
    `body.prd_id` is None when no PRD is open beside this chat, in which case
    the route returns the simple "open a PRD" clarify — never an inferred or
    auto-selected target, and never a cross-project PRD enumeration.

    ★ `body.prd_id` is CLIENT-SUPPLIED and UNTRUSTED on its own — a member of
    project A could name a PRD on project B, or (probed) another tenant's.
    `apply_chat_edit_scoped` runs the ★ cross-project IDOR gate
    (`assert_prd_on_project`) — fail-closed, before any read/write — on
    WHATEVER `prd_id` reaches it, THEN the cross-tenant gate
    (`require_owned_prd`). This is the PRIMARY defense for the
    client-supplied target. A `ProjectPrdWriteDenied` from that gate is
    caught and degrades to the same no-edit shape rather than a raw
    403/404, since the resolved-target contract promises callers a soft
    refusal, not an error, on this route.
    """
    _require_project_member(project_id, ctx)

    resolved_client_message_id = body.client_message_id or str(uuid.uuid4())

    def _persist_edit_turns(answer_text: str) -> None:
        """Owned, idempotent both-sides persist (AC2): the user's
        instruction + the assistant's shown answer, keyed on the SAME
        client_message_id so a double-submit dedups per side. Best-effort —
        a persist failure never blocks the edit's already-computed result."""
        try:
            conversations_db.post_owned_individual_user_turn(
                project_id=project_id,
                user_id=ctx.user_id,
                content=body.instruction,
                client_message_id=resolved_client_message_id,
            )
            conversations_db.post_owned_individual_assistant_turn(
                project_id=project_id,
                user_id=ctx.user_id,
                content=answer_text,
                client_message_id=resolved_client_message_id,
            )
        except Exception:  # noqa: BLE001 — best-effort, AD-P7
            logger.warning(
                "failed to persist individual-chat edit turns project_id=%s",
                project_id, exc_info=True,
            )

    if body.prd_id is None:
        # No PRD open beside this chat — simple clarify, no inference across
        # the project's PRDs (Design clarification, mirrors main's open-tab
        # requirement).
        answer = "Open a PRD beside this chat and I'll edit it."
        _persist_edit_turns(answer)
        return {"edited": False, "answer": answer}

    dataset = _dataset_for(ctx)
    try:
        result = apply_chat_edit_scoped(
            body.prd_id, body.instruction, ctx,
            project_id=project_id, dataset=dataset,
        )
    except ProjectPrdWriteDenied:
        answer = "I can only edit a PRD that's attached to this project."
        _persist_edit_turns(answer)
        return {"edited": False, "answer": answer}

    if not result["sections_changed"]:
        # The editor judged the instruction wasn't an edit — nothing changed.
        answer = result.get("summary") or "I didn't find anything in the PRD to change for that."
        _persist_edit_turns(answer)
        return {"edited": False, "answer": answer}

    summary = result.get("summary") or ""
    done_answer = f"Done — I've updated the PRD. {summary}".strip() if summary \
        else "Done — I've updated the PRD."
    _persist_edit_turns(done_answer)

    return {
        "edited": True,
        "prd": result["prd"],
        "sections_changed": result["sections_changed"],
        "summary": result["summary"],
    }


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


# ── Individual project chat ─────────────────────────────────────────────
# One `kind='individual'` conversation per (project, user) — "My chat with
# Sprntly". Every route here is membership-gated via
# `_require_project_member` (AD-P11 WAVE INVARIANT): a same-tenant
# non-member gets 403, a foreign-tenant project 404s.


@router.post("/{project_id}/individual")
def create_individual_chat_route(
    project_id: int, ctx: WorkspaceContext = Depends(require_workspace)
):
    """Get-or-create THIS caller's durable individual project chat
    (`kind='individual'`, scoped project_id+user_id) and return it.
    Idempotent per (project, caller).

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


class PostIndividualTurnsRequest(BaseModel):
    """The owned turn-pair body for the branches with no chat-route home:
    a generate branch, a clarify settle, or a terminal outcome (error/
    cancel/artifact-attach-failed) — each persists the question actually
    asked and the assistant text actually shown, so a reload restores the
    real dialogue rather than a blank."""
    client_message_id: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1, max_length=120_000)
    answer: str = Field(..., min_length=1, max_length=120_000)


@router.post("/{project_id}/individual/turns")
def persist_individual_turns_route(
    project_id: int,
    body: PostIndividualTurnsRequest,
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """Persist the CALLER'S OWN user+assistant turn pair into THEIR
    individual project chat (AC2/AC3) — the explicit-owner home for the
    branches whose backend work doesn't already have a chat-route home
    (`runGeneratePrd`/`runGenerateTickets`/clarify-settle/terminal
    outcomes). Membership-gated; ownership is resolved SERVER-side from
    `(project_id, ctx.user_id)` by the §B writers — the client supplies no
    `conversation_id` (AC6). Idempotent on `client_message_id` (AC4): a
    double-submit returns the SAME pair rather than writing a second one."""
    _require_project_member(project_id, ctx)
    user_turn = conversations_db.post_owned_individual_user_turn(
        project_id=project_id,
        user_id=ctx.user_id,
        content=body.question,
        client_message_id=body.client_message_id,
    )
    assistant_turn = conversations_db.post_owned_individual_assistant_turn(
        project_id=project_id,
        user_id=ctx.user_id,
        content=body.answer,
        client_message_id=body.client_message_id,
    )
    return {"user_turn_id": user_turn["id"], "assistant_turn_id": assistant_turn["id"]}


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
    nothing has been posted, which is a legitimate read state, not an
    error."""
    _require_project_member(project_id, ctx)
    conversation = conversations_db.get_individual_project_chat(project_id, ctx.user_id)
    if not conversation:
        return {"turns": []}
    return {
        "turns": conversations_db.list_individual_turns(
            conversation["id"], ctx.user_id, since=since
        )
    }


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
    _require_project_member(project_id, ctx)  # GATE 1
    deleg = delegation_events_db.load_delegation_for_authz(delegation_id)
    if deleg is None or deleg["project_id"] != project_id:  # GATE 2
        raise HTTPException(404, "Delegation not found")
    party = delegation_events_db.EVENT_PARTY.get(payload.event)  # GATE 3
    if party is None:
        raise HTTPException(422, "Unknown or non-emittable event")
    if deleg[f"{party}_user_id"] != ctx.user_id:
        raise HTTPException(403, "Not the correct party for this event")
    current = delegation_events_db.current_status(delegation_id)  # GATE 4
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
    if payload.event == "completed":
        # Best-effort, non-fatal (mirrors the publish block's own posture
        # immediately below): a `status_dto` read failure here must not turn
        # an already-durably-recorded completion into a client-visible 500.
        try:
            dto = delegation_events_db.status_dto(delegation_id)
            notify_requester_task_completed(
                project_id, delegation_id,
                assignee_user_id=deleg["assignee_user_id"],
                task_summary=(dto or {}).get("task_summary"),
            )
        except Exception as exc:  # noqa: BLE001 — best-effort, see comment above
            logger.warning(
                "delegation_completion_notice_prep_failed delegation_id=%s error_class=%s",
                delegation_id, type(exc).__name__,
            )
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
    """Open-only ledger counts for the project rail card — a derived,
    never-stored count. `reopened` counts as open;
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
