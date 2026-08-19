"""Project-surface context assembler — registers under `context_source`
kind `"project"`.

Produces a BREADTH-ONLY `SurfaceScope` for the two project-chat surfaces
(private "My chat with Sprntly" and the @Sprntly group agent): the project's
roster + task-ledger digest + artifact manifest + memory, framed
authoritatively, reusing the EXISTING single-sourced assemblers in
`app.project_group_context` so private and group can never drift on which
members/tasks/artifacts they report.

Membership gate FIRST (IDOR-critical), ported verbatim in shape from the
pre-collapse caller (`routes/ask.py` at commit `b09801dd^`): a project not in
the caller's `(company, workspace)` 404s, a same-tenant NON-member 403s — and
BOTH checks run BEFORE any project data is read, so knowing a project id can
never leak its memory into an answer. The gate is NOT best-effort: it raises.

Breadth AND depth: `extra_tools` carries the 6 project tools (4 shared read
tools + `delegate_task` + `execute_task`), so the EXISTING sixth tool-loop
branch in `qa_agent.answer` (`_try_scoped_tool_answer`, which reads
`scope.extra_tools`) claims a project-content / delegate / execute turn. A
plain-Q&A project ask is NOT claimed by that branch — its own intent gate
decides — and still reaches the composer via `qa_agent._fold_project_context`,
which prepends the authoritative preamble itself, so this assembler must NOT
prepend it to `context_payload` (doing so would double it on the fold path).

Block assembly IS best-effort (AD-P7): a read failure degrades to an empty
block and never blocks the answer. Returns a `SurfaceScope` (the type
`answer(scope=...)` already consumes), populating the breadth fields only.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException

from app.context_assembler import AssembleRequest
from app.surface_scope import Surface, SurfaceScope

logger = logging.getLogger(__name__)


class ProjectContextAssembler:
    """`ContextAssembler` for `kind == "project"`. See module docstring for the
    membership-gate and breadth-only contract."""

    def assemble(self, req: AssembleRequest) -> SurfaceScope:
        params = req.params or {}
        project_id_raw = params.get("project_id")
        surface_name = params.get("surface") or "private"

        # No project to scope to → behave as the no-source main path (a
        # main-surface scope is a no-op ALIAS for `scope is None`).
        if project_id_raw is None:
            return SurfaceScope(surface=Surface.main)
        project_id = int(project_id_raw)

        # ── Membership gate FIRST (IDOR-critical) ────────────────────────────
        # Ported from `b09801dd^:backend/app/routes/ask.py`: 404 when the
        # project isn't in the caller's `(company, workspace)` (same
        # non-disclosure posture as the dataset/prd gates — "exists but not
        # yours" and "doesn't exist" are indistinguishable), 403 when the caller
        # is a same-tenant NON-member. Both run BEFORE any project data is read.
        from app.db.projects import is_project_member, project_belongs_to_company

        if not project_belongs_to_company(
            project_id, req.company_id, req.workspace_id
        ):
            raise HTTPException(404, "Project not found")
        if not is_project_member(project_id, req.user_id):
            raise HTTPException(403, "Not a member of this project")

        surface = (
            Surface.project_group
            if surface_name == "group"
            else Surface.project_private
        )

        # ── Breadth block ────────────────────────────────────────────────────
        # The SAME single-sourced assemblers both surfaces already use — private
        # gets the caller's own memory + roster/ledger/manifest; group gets the
        # memory-summary + latest-insight + roster/ledger/manifest variant.
        # Best-effort (AD-P7): a read failure degrades to an empty block. The
        # authoritative preamble is added by `qa_agent._fold_project_context`,
        # NOT here (folding it in would double it).
        block = ""
        try:
            from app import project_group_context

            if surface == Surface.project_group:
                block = project_group_context.assemble_group_agent_context(
                    project_id, req.dataset, req.company_id
                )
            else:
                block = project_group_context.assemble_private_project_context(
                    project_id, req.user_id, req.dataset, req.company_id
                )
        except Exception:  # noqa: BLE001 — best-effort, never blocks the answer
            logger.warning(
                "project context assembly failed project_id=%s surface=%s",
                project_id, surface_name, exc_info=True,
            )
            block = ""

        # ── Project instructions block ───────────────────────────────────────
        # Single-sourced format both surfaces use (`_instructions_block`); folded
        # into `system_addendum` below (never `context_payload`). Best-effort.
        instr_block = ""
        try:
            from app.db import projects as projects_db
            from app.project_group_context import _instructions_block

            instr_block = _instructions_block(
                projects_db.get_instructions(project_id)
            )
        except Exception:  # noqa: BLE001 — best-effort
            instr_block = ""

        # ── Depth tools (the breadth → depth flip) ───────────────────────────
        # Populate `extra_tools` with the 6 project tools so the EXISTING sixth
        # ladder branch (`qa_agent._try_scoped_tool_answer`, which reads
        # `scope.extra_tools`) claims a project-content / delegate / execute
        # turn. Ported in shape — NOT reimplemented — from `b09801dd^:app/
        # ask_job_runner._build_private_scope`: the 4 shared read tools +
        # `delegate_task` + `execute_task`, with the three sidecar fields that
        # branch's dispatch consumes: `roster` (free-text assignee → member
        # resolution), `assigner_identity` (delegation attribution) and
        # `post_turn` (execute-task progress posts). All best-effort (AD-P7).
        #
        # This does NOT route EVERY project ask through the tool loop: the sixth
        # branch's own intent gate (`is_project_tool_request` /
        # `is_project_content_request` / bare-send / edit) decides which turns it
        # claims. A plain-Q&A project ask still falls through to the breadth/
        # composer path below the branch, exactly as it did with empty tools.
        from app import project_delegation, project_task_execution
        from app.db import projects as projects_db
        from app.project_group_context import read_tools

        try:
            roster = projects_db.list_members(project_id)
        except Exception:  # noqa: BLE001 — best-effort, AD-P7
            roster = []

        # ── system_addendum composition ──────────────────────────────────────
        # Ported verbatim in COMPOSITION from `b09801dd^:app/ask_job_runner.
        # _build_private_scope`: the private surface folds the relocated
        # tool-usage system guidance (`_PRIVATE_SCOPE_SYSTEM`, which itself already
        # appends `PROJECT_TOOL_NUDGE`) + the roster block + the project
        # instructions, so the model gets WHEN/HOW guidance for delegate_task /
        # execute_task / edit-in-place alongside the 6 depth tools — not just the
        # facts. Reuses the `roster` fetched just above for the sidecars (no
        # re-fetch). The constants/helper are imported (not reimplemented) from
        # `app.ask_job_runner`, where they still live on this commit.
        #
        # Group surface stays instructions-only for now: group depth routing is
        # deferred (group runs as main chat), and no trivially-additive group
        # scope-system helper exists on this commit to fold in, so composing one
        # would mean building group routing machinery — explicitly out of scope.
        if surface == Surface.project_private:
            from app.ask_job_runner import (
                _PRIVATE_SCOPE_SYSTEM,
                _private_roster_block,
            )

            system_addendum = (
                f"{_PRIVATE_SCOPE_SYSTEM}\n\n{_private_roster_block(roster)}"
            )
            if instr_block:
                system_addendum = f"{system_addendum}\n\n{instr_block}"
        else:
            system_addendum = instr_block

        # `post_turn` — the execute-task progress writer. RELOCATED in shape from
        # `_build_private_scope`: the private surface's turn writer, bound to the
        # ask's own conversation. `None` when the ask carries no conversation
        # (nothing to post into), which degrades `execute_task` to no progress
        # posts rather than erroring.
        post_turn = None
        if req.conversation_id is not None:
            from app.db.conversations import post_individual_turn

            post_turn = (
                lambda content: post_individual_turn(
                    req.conversation_id, "assistant", content
                )
            )

        return SurfaceScope(
            surface=surface,
            project_id=project_id,
            context_payload=block,
            system_addendum=system_addendum,
            # The 6 project tools, stable order: delegate + execute + the 4
            # shared read tools. Non-empty `extra_tools` is the on-switch the
            # sixth branch gates on (along with its intent gate).
            extra_tools=(
                project_delegation.DELEGATE_TASK_TOOL,
                project_task_execution.EXECUTE_TASK_TOOL,
                *read_tools(),
            ),
            roster=tuple(roster),
            assigner_identity={
                "assigner_user_id": req.user_id,
                "source_conversation_id": req.conversation_id,
            },
            post_turn=post_turn,
        )
