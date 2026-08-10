"""Projects — container + membership CRUD and the layered project-memory
API (folds the build spec's `db/projects.py` CRUD + memory read/manual-add
into one ticket/one file pair).

Tenant gate: every route resolves tenancy from `WorkspaceContext`
(`require_workspace`, the `ask.py` pattern — `backend/app/routes/ask.py`).
Projects scope by `company_id`/`workspace_id` UUID, NOT by a dataset
slug — this router deliberately does not take a `?dataset=` query param
and does not call `require_owned_dataset`.

Scope boundary: no LLM calls (memory synthesis + promotion are Phase 2),
no group-chat turn endpoints, no artifact fan-out — each is a separate
ticket.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import WorkspaceContext, require_workspace
from app.db import project_memory_entries as memory_db
from app.db import projects as projects_db

logger = logging.getLogger(__name__)

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


class AddMemberRequest(BaseModel):
    email: str = Field(min_length=1)


class AddMemoryEntryRequest(BaseModel):
    body: str = Field(min_length=1)


class UpdateMemoryEntryRequest(BaseModel):
    body: str = Field(min_length=1)


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
    """Add an existing user to the project by email. The caller must
    already be a project member (membership = access, AD-P11) — a
    non-member gets 403 and the roster is unchanged. Inviting a non-user
    by email is `org_invites`-based and is a fast-follow (out of scope)."""
    _require_project_member(project_id, ctx)
    user_id = projects_db.user_id_for_email(payload.email)
    if not user_id:
        raise HTTPException(404, "No account found for that email")
    member = projects_db.add_member(project_id, user_id)
    logger.info("project_member_added project_id=%s user_id=%s", project_id, user_id)
    return member


@router.get("/{project_id}/memory/summary")
def get_memory_summary(project_id: int, ctx: WorkspaceContext = Depends(require_workspace)):
    """The cached synthesized summary, read-only — never triggers an LLM
    call (synthesis is a Phase 2 writer). Membership-gated."""
    _require_project_member(project_id, ctx)
    return memory_db.get_summary(project_id)


@router.get("/{project_id}/memory")
def list_memory(project_id: int, ctx: WorkspaceContext = Depends(require_workspace)):
    _require_project_member(project_id, ctx)
    return {"entries": memory_db.list_entries(project_id)}


@router.post("/{project_id}/memory")
def add_memory(
    project_id: int,
    payload: AddMemoryEntryRequest,
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """Add a user-authored memory entry. `author_user_id` is always the
    session user; `promoted_by` stays NULL — agent-promoted entries are a
    Phase 2 writer. Flips an existing summary's `stale` flag.
    Membership-gated."""
    _require_project_member(project_id, ctx)
    entry = memory_db.add_entry(project_id, body=payload.body, author_user_id=ctx.user_id)
    logger.info("memory_entry_added project_id=%s entry_id=%s", project_id, entry["id"])
    return entry


@router.patch("/{project_id}/memory/{entry_id}")
def update_memory(
    project_id: int,
    entry_id: int,
    payload: UpdateMemoryEntryRequest,
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """Edit a memory entry's body — scoped to this project; an entry_id
    from another project 404s, unchanged. Membership-gated: a member may
    edit any entry in a project they belong to (v1 all-or-nothing,
    AD-P11), a same-tenant non-member gets 403 first."""
    _require_project_member(project_id, ctx)
    entry = memory_db.update_entry(project_id, entry_id, body=payload.body)
    if not entry:
        raise HTTPException(404, "Memory entry not found")
    return entry


@router.delete("/{project_id}/memory/{entry_id}")
def delete_memory(
    project_id: int,
    entry_id: int,
    ctx: WorkspaceContext = Depends(require_workspace),
):
    """Remove a memory entry — scoped to this project; an entry_id from
    another project 404s, unchanged. Membership-gated, same as
    `update_memory`."""
    _require_project_member(project_id, ctx)
    deleted = memory_db.delete_entry(project_id, entry_id)
    if not deleted:
        raise HTTPException(404, "Memory entry not found")
    return {"deleted": True}
