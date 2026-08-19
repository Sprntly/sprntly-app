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

Breadth only THIS phase: `extra_tools` stays empty. The 6-tool depth path
(the sixth tool-loop branch in `qa_agent.answer`) is a later phase; with no
extra tools the breadth block reaches the composer via
`qa_agent._fold_project_context`, which prepends the authoritative preamble
itself — so this assembler must NOT prepend it (doing so would double it).

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

        # ── Project instructions → system_addendum ───────────────────────────
        # Single-sourced format both surfaces use (`_instructions_block`); goes
        # on `system_addendum`, never `context_payload`. Best-effort.
        system_addendum = ""
        try:
            from app.db import projects as projects_db
            from app.project_group_context import _instructions_block

            system_addendum = _instructions_block(
                projects_db.get_instructions(project_id)
            )
        except Exception:  # noqa: BLE001 — best-effort
            system_addendum = ""

        return SurfaceScope(
            surface=surface,
            project_id=project_id,
            context_payload=block,
            system_addendum=system_addendum,
            # Breadth only this phase: NO depth tools. Empty `extra_tools` keeps
            # `answer()` off the sixth tool-loop branch and on the fold path.
            extra_tools=(),
        )
