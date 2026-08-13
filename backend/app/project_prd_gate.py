"""Project-scoped PRD-write gate (F11 / workspace-isolation #20-#23).

★ LOAD-BEARING SECURITY ITEM ★ — the in-tenant cross-project IDOR gate.

`prd_patches` is workspace/company-scoped, NOT project-scoped: the write
helpers filter by `workspace_id` only. A project-chat agent is handed a
`prd_id` the MODEL controls, so without this gate a chat on project A could
propose a patch against ANY PRD in the caller's company (cross-project IDOR),
or — on a probed id — a PRD in another company (cross-tenant). This module is
the single choke-point that proves a PRD belongs to THIS project before any
write happens, and it is fail-closed by construction.

Why the project artifact manifest is sufficient (DRY — no second scoping
query): `list_artifacts_for_project` already reads the project's OWN
`(type, id)` refs AND intersects them with the caller's tenant-scoped
company-wide fan-out, so a `(prd, id)` survives only if it is attached to THIS
project AND owned by THIS company. A cross-project id is absent from the ref
set; a cross-tenant id is absent from the fan-out. Both fall away.

Fail-closed on read error: if `list_artifacts_for_project` raises, the
exception PROPAGATES — never a default-allow. The write handler (§D) treats any
gate exception as a refusal (a safe tool-result string, zero write).

Observability (Rule #24): a denial logs identifiers only — never PRD body,
`patch_md`, or `rationale`.
"""
from __future__ import annotations

import logging

from app.db.artifacts import list_artifacts_for_project

logger = logging.getLogger(__name__)


class ProjectPrdWriteDenied(Exception):
    """Raised when a PRD is not on the given project (cross-project /
    cross-tenant / absent) — the propose handler catches it and refuses the
    write with a plain, non-leaking string."""


def prd_on_project(
    *, prd_id: int, project_id: int, dataset: str, company_id: str
) -> bool:
    """True iff `(type="prd", id=prd_id)` is on THIS project's tenant-scoped
    artifact manifest. Any read error propagates (fail-closed — never a
    default-True)."""
    manifest = list_artifacts_for_project(
        project_id=project_id, dataset=dataset, company_id=company_id
    )
    return any(
        it.get("type") == "prd" and it.get("id") == prd_id for it in manifest
    )


def assert_prd_on_project(
    *, prd_id: int, project_id: int, dataset: str, company_id: str
) -> None:
    """Raise `ProjectPrdWriteDenied` unless the PRD is on this project. A read
    error from the manifest PROPAGATES unchanged (fail-closed)."""
    if not prd_on_project(
        prd_id=prd_id, project_id=project_id, dataset=dataset, company_id=company_id
    ):
        # Identifiers only — never PRD body / patch_md / rationale (Rule #24).
        logger.warning(
            "project_prd_write_denied prd_id=%s project_id=%s", prd_id, project_id
        )
        raise ProjectPrdWriteDenied(
            "PRD is not attached to this project"
        )
