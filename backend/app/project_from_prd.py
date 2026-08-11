"""Auto-create-from-PRD hook (build spec AD-P9).

Generating a PRD through a conversation should auto-"fork" that thread into
a project — a project appears with the PRD as its first artifact and the
originating chat bound to it, with no forced "create project" step. This is
ONE entry point among several into `origin='prd_auto'` projects (the other is
the create-modal's explicit `auto` tab, `web/.../CreateProjectModal.tsx`) —
projects themselves are never PRD-bound; a project can hold any number of
artifacts, from any source.

Called from `routes/prd.py`, immediately after each existing
`bind_conversation_to_prd(...)` call — the only sites where a source
conversation, a real prd_id, and the caller's WorkspaceContext are all known
together. Mirrors `bind_conversation_to_prd`'s best-effort posture exactly:
this is a side effect of PRD generation, not a step in it, so it must never
turn a successful generation into a failed request.
"""
from __future__ import annotations

import logging

from app.db.client import require_client
from app.db.conversations import bind_conversation_to_project
from app.db.projects import add_artifact, create_project

logger = logging.getLogger(__name__)


def _conversation_project_id(conversation_id: int, company_id: str) -> int | None:
    """The project this conversation is already bound to, or None.

    Mirrors `app.db.conversations.get_conversation_prd_id`'s read shape,
    scoped to `project_id` instead — company-scoped only (no user_id
    filter): the first-write-wins guard needs to know whether ANY project
    already claimed this conversation, not just this caller's own view of
    it, so a re-issued generate from the same account can never spawn a
    second project for the same thread."""
    rows = (
        require_client()
        .table("conversations")
        .select("project_id")
        .eq("id", conversation_id)
        .eq("company_id", company_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    if rows:
        return rows[0].get("project_id")
    return None


def maybe_auto_create_project_for_prd(
    *,
    company_id: str,
    workspace_id: str,
    user_id: str,
    prd_id: int,
    prd_title: str,
    conversation_id: int | None,
) -> int | None:
    """Create-if-unbound: a project (`origin='prd_auto'`) + the PRD as its
    first artifact + bind the source conversation. First-write-wins (a
    conversation already bound to a project is left alone — no duplicate
    project for a re-issued generate). Never raises: any failure is logged
    and swallowed, returning None — PRD generation is unaffected either way.

    Skips entirely (returns None, no project) when `conversation_id` is
    None: an unbound generate has no thread to fork, so it is not
    force-forked into a project it never asked for."""
    if conversation_id is None:
        return None
    try:
        existing_project_id = _conversation_project_id(conversation_id, company_id)
        if existing_project_id is not None:
            return existing_project_id

        project = create_project(
            company_id=company_id,
            workspace_id=workspace_id,
            name=prd_title,
            created_by=user_id,
            origin="prd_auto",
        )
        project_id = project["id"]
        add_artifact(project_id, "prd", prd_id)
        bind_conversation_to_project(conversation_id, project_id, company_id, user_id)
        return project_id
    except Exception:  # noqa: BLE001 — best-effort, mirrors bind_conversation_to_prd
        logger.warning(
            "Failed to auto-create a project for PRD %s (conversation %s)",
            prd_id, conversation_id,
            exc_info=True,
        )
        return None
