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

`find_existing_prd_auto_project` below is the REVERSE lookup: given a PRD
(not a conversation), has it already been forked into a project? The
create-modal's "Auto · from PRD" tab calls `POST /v1/projects` directly —
it never resolves a `conversation_id`, only the PRD the user picked — so it
cannot reuse `_conversation_project_id` (which needs one) as-is.

Keyed on `project_artifacts` (`artifact_type='prd', artifact_id=<prd_id>`)
scoped to `origin='prd_auto'` projects only — NOT on the `conversations`
binding `_conversation_project_id` reads. That's deliberate: BOTH fork
paths (this module's own hook, AND the create-modal's follow-up
`POST .../artifacts` call) always attach the PRD as a `project_artifacts`
row, but only the hook's path ever binds a conversation — two consecutive
create-modal forks of the same PRD never touch `conversations` at all, so
a conversation-keyed lookup can't dedupe them. The artifact-ref fact is the
one both paths share, so it's the single dedup mechanism for
`origin='prd_auto'` projects.
"""
from __future__ import annotations

import logging

from app.db.client import require_client
from app.db.conversations import bind_conversation_to_project
from app.db.projects import add_artifact, create_project
from app.project_origin_seed import seed_project_origin_memory
from app.project_title import generate_project_title

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


def find_existing_prd_auto_project(prd_id: int, company_id: str) -> int | None:
    """The `origin='prd_auto'` project already forked for `prd_id`, if any —
    the dedup check behind the create-modal's "Auto · from PRD" tab
    (`POST /v1/projects`, `origin=prd_auto`) AND (transitively, since it
    always writes the same `project_artifacts` fact) this module's own
    generation-time hook.

    Two-step lookup: find every project holding this PRD as an artifact ref
    (`project_artifacts.artifact_type='prd', artifact_id=prd_id` — written
    by BOTH `maybe_auto_create_project_for_prd` below AND the create-modal's
    follow-up `POST .../artifacts` call), then narrow to the caller's own
    company AND `origin='prd_auto'` specifically — a MANUAL project that
    happens to reference the same PRD as one of its artifacts must NEVER be
    matched here; only an auto-created fork dedupes against another
    auto-created fork. Company-scoped so first-write-wins holds regardless
    of who/which conversation originally forked the PRD, same rationale as
    `_conversation_project_id`. None when no such project exists — the
    caller creates a fresh one, same as always."""
    client = require_client()
    artifact_rows = (
        client.table("project_artifacts")
        .select("project_id")
        .eq("artifact_type", "prd")
        .eq("artifact_id", prd_id)
        .execute()
        .data
        or []
    )
    candidate_project_ids = {row["project_id"] for row in artifact_rows}
    if not candidate_project_ids:
        return None

    project_rows = (
        client.table("projects")
        .select("id")
        .in_("id", list(candidate_project_ids))
        .eq("company_id", company_id)
        .eq("origin", "prd_auto")
        .order("id")
        .limit(1)
        .execute()
        .data
        or []
    )
    return project_rows[0]["id"] if project_rows else None


def maybe_auto_create_project_for_prd(
    *,
    company_id: str,
    workspace_id: str,
    user_id: str,
    prd_id: int,
    prd_title: str,
    conversation_id: int | None,
    allow_without_conversation: bool = False,
) -> int | None:
    """Create-if-unbound: a project (`origin='prd_auto'`) + the PRD as its
    first artifact + (when there is one) bind the source conversation.
    First-write-wins — no duplicate project for a re-issued generate. Never
    raises: any failure is logged and swallowed, returning None — PRD
    generation is unaffected either way.

    Two dedup keys, by whether a source conversation exists:

    - WITH a `conversation_id` (chat / `from_task` path): a conversation
      binds to exactly ONE project for its lifetime, so every distinct PRD
      generated in that conversation belongs in the same project, not just
      the first. Dedup on the conversation→project binding
      (`_conversation_project_id`): the first PRD in a conversation still
      CREATES the project (below); every subsequent PRD in that same
      conversation is instead ATTACHED to the already-bound project as a
      `project_artifacts` row (`add_artifact` upserts on the
      `(project_id, artifact_type, artifact_id)` primary key, so re-attaching
      a PRD already on the project is a no-op — no duplicate row).

    - WITHOUT a `conversation_id`, only when `allow_without_conversation` is
      set (the ideation `/generate-from-ideation` and weekly-brief `/generate`
      paths, which have no chat thread): dedup on the PRD-artifact fact
      (`find_existing_prd_auto_project`) so re-generating the SAME PRD returns
      the SAME project instead of spawning a duplicate. No conversation is
      bound and origin-memory seeding (which reads chat turns) is skipped —
      there is no thread to summarize.

    Skips entirely (returns None, no project) when `conversation_id` is None
    AND `allow_without_conversation` is False — the original guard, so the
    chat path and older clients that omit `conversation_id` are unaffected."""
    if conversation_id is None and not allow_without_conversation:
        return None
    try:
        if conversation_id is not None:
            existing_project_id = _conversation_project_id(conversation_id, company_id)
            if existing_project_id is not None:
                # A later, distinct PRD generated in an already-bound
                # conversation joins the same project instead of being
                # orphaned from it.
                add_artifact(existing_project_id, "prd", prd_id)
                return existing_project_id
        else:
            # Conversation-less fork: the PRD-artifact ref is the only shared
            # fact to dedup on (mirrors the create-modal's "Auto · from PRD"
            # tab), so a repeated conversation-less generate of the same PRD
            # reuses its project.
            existing_project_id = find_existing_prd_auto_project(prd_id, company_id)
            if existing_project_id is not None:
                return existing_project_id

        # Name the project for what the PRD is ABOUT, not the PRD's title
        # verbatim — the shared name-derivation point both fork paths route
        # through. Best-effort: falls back to `prd_title` on any failure.
        project_name = generate_project_title(prd_id=prd_id, fallback_title=prd_title)
        project = create_project(
            company_id=company_id,
            workspace_id=workspace_id,
            name=project_name,
            created_by=user_id,
            origin="prd_auto",
        )
        project_id = project["id"]
        add_artifact(project_id, "prd", prd_id)
        if conversation_id is not None:
            bind_conversation_to_project(conversation_id, project_id, company_id, user_id)
            # Seed the NEW project's memory with the origin context — the
            # decisions/reasoning from the originating chat + a brief of what
            # the PRD is. Best-effort and self-contained: it never raises, so a
            # summarizer failure can't turn a created project into a None return
            # (this runs ONLY in the new-project branch — an already-bound
            # conversation returned above and is never re-seeded). Skipped for
            # the conversation-less path: there is no chat thread to read.
            seed_project_origin_memory(
                project_id=project_id,
                origin="prd_auto",
                prd_id=prd_id,
                prd_title=prd_title,
                conversation_id=conversation_id,
            )
        return project_id
    except Exception:  # noqa: BLE001 — best-effort, mirrors bind_conversation_to_prd
        logger.warning(
            "Failed to auto-create a project for PRD %s (conversation %s)",
            prd_id, conversation_id,
            exc_info=True,
        )
        return None


def maybe_pin_conversation_artifact_to_project(
    conversation_id: int | None,
    company_id: str | None,
    artifact_type: str,
    artifact_id: int,
) -> int | None:
    """Pin `artifact_id` to whatever project `conversation_id` is bound to.

    The generalised, artifact-type-agnostic sibling of the PRD-specific
    `maybe_auto_create_project_for_prd`'s already-bound branch (line ~168):
    given a conversation that already belongs to a project (a project's own
    individual/group chat, or a chat auto-forked into a project), UPSERT the
    generated artifact into that project's `project_artifacts` so it shows up
    on the project's artifact rail AND in the project context manifest the
    context-assembler injects (both are derived at read time from
    `project_artifacts`, so this single write is all that's needed).

    Called from the generation loci that mint a NON-PRD artifact server-side
    (evidence / ticket-set), which had no project-pin of their own — so a doc
    generated inside a project chat used to orphan from the project. PRDs are
    already pinned by `maybe_auto_create_project_for_prd` at their generation
    routes and do not go through here.

    Robust to client-close by construction: it runs server-side wherever the
    generation job does, not on the client. Idempotent (add_artifact upserts
    on the PK), so a force-regen that mints a NEW artifact id simply pins the
    new id — resolve-forward on the rail covers the superseded old pin.

    `artifact_type` must be a pinnable type (prd/evidence/prototype/report/
    ticket_set); a `custom_artifact` team document is excluded by the
    `project_artifacts` CHECK constraint and must never be passed here.

    Best-effort — never raises (mirrors `bind_conversation_to_prd` /
    `add_artifact`): a failed pin must never break the generation it
    accompanies. Non-project (main-chat) generation is unaffected: an unbound
    conversation (or none at all) reads None and nothing is written. Returns
    the project id it pinned to, or None."""
    if conversation_id is None or not company_id:
        return None
    try:
        project_id = _conversation_project_id(conversation_id, company_id)
        if project_id is None:
            return None
        add_artifact(project_id, artifact_type, artifact_id)
        return project_id
    except Exception:  # noqa: BLE001 — best-effort, mirrors bind_conversation_to_prd
        logger.warning(
            "Failed to pin %s %s to conversation %s's project",
            artifact_type, artifact_id, conversation_id,
            exc_info=True,
        )
        return None


def maybe_pin_prototype_to_prd_projects(
    prd_id: int,
    prototype_id: int,
    company_id: str | None,
) -> list[int]:
    """Pin `prototype_id` to every project that already holds `prd_id`.

    The prototype-generation path (`routes/design_agent.py`) is PRD-scoped, not
    conversation-scoped — it carries a `prd_id` but no `conversation_id` — so the
    conversation-keyed `maybe_pin_conversation_artifact_to_project` doesn't fit.
    A prototype built off a project's PRD is part of that project's work, so it
    belongs on the project's artifact rail + injected context alongside the PRD.

    The PRD is the join key: every project-bound PRD is already pinned as a
    `project_artifacts('prd', prd_id)` ref — by `maybe_auto_create_project_for_prd`
    at the PRD generation routes, or by a manual `POST .../artifacts` add — so
    that ref IS the authoritative "which project(s) own this PRD" fact. There is
    no `prds.conversation_id` to walk back to a project the other way, which is
    why this resolves via the artifact ref rather than the conversation binding.

    Reverse-look up the ref (company-scoped, mirroring `find_existing_prd_auto_project`
    minus the `origin='prd_auto'` narrowing — a MANUAL project that added the PRD
    must get the prototype too) and upsert the prototype into each project.
    Usually exactly one; a PRD shared across several is pinned into all of them,
    each `add_artifact` upsert idempotent on the PK.

    Best-effort — never raises: a failed pin must never break prototype
    generation. A prototype whose PRD is in no project (a non-project prototype)
    writes nothing and is unaffected. Returns the project ids pinned (possibly
    empty)."""
    if not company_id:
        return []
    try:
        client = require_client()
        artifact_rows = (
            client.table("project_artifacts")
            .select("project_id")
            .eq("artifact_type", "prd")
            .eq("artifact_id", prd_id)
            .execute()
            .data
            or []
        )
        candidate_ids = {row["project_id"] for row in artifact_rows}
        if not candidate_ids:
            return []
        project_rows = (
            client.table("projects")
            .select("id")
            .in_("id", list(candidate_ids))
            .eq("company_id", company_id)
            .execute()
            .data
            or []
        )
        pinned: list[int] = []
        for row in project_rows:
            add_artifact(row["id"], "prototype", prototype_id)
            pinned.append(row["id"])
        return pinned
    except Exception:  # noqa: BLE001 — best-effort, mirrors bind_conversation_to_prd
        logger.warning(
            "Failed to pin prototype %s to prd %s's project(s)",
            prototype_id, prd_id,
            exc_info=True,
        )
        return []


def maybe_pin_custom_artifact_to_project(
    *,
    company_id: str,
    conversation_id: int | None,
    artifact_id: int,
) -> int | None:
    """Attach a just-generated custom document (team document) to the project
    its conversation is already bound to, if any. Returns the project id it
    pinned to, or None (no conversation, no bound project, or a swallowed
    failure). Never raises.

    ATTACH-ONLY, unlike `maybe_auto_create_project_for_prd`: a custom doc is
    NOT a project-origin trigger, so this never CREATES a project — it only
    joins a document to a project that some earlier PRD/fork already
    established for the thread. A doc drafted in a bare chat with no project
    stays project-less; nothing to pin it to, and inventing one would fork a
    project off a "draft a leadership update" the way only a PRD is meant to.

    Conversation-keyed, reusing `_conversation_project_id` — the SAME
    first-write-wins binding the PRD path reads. `add_artifact` upserts on the
    `(project_id, artifact_type, artifact_id)` primary key, so a re-issued
    generate re-attaching the same doc is a no-op.

    Best-effort and total: called from the generate route right after the row
    is created, so it runs server-side regardless of whether the client is
    still connected. Any failure is logged and swallowed — a document that
    generated fine must never fail its request because the pin missed; the
    project's own poll/refetch reconciles a dropped realtime nudge anyway."""
    if conversation_id is None:
        return None
    try:
        project_id = _conversation_project_id(conversation_id, company_id)
        if project_id is None:
            return None
        add_artifact(project_id, "custom_artifact", artifact_id)
        return project_id
    except Exception:  # noqa: BLE001 — best-effort, mirrors the PRD path above
        logger.warning(
            "Failed to pin custom artifact %s to its project (conversation %s)",
            artifact_id, conversation_id,
            exc_info=True,
        )
        return None
