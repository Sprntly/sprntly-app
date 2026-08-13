"""Project PRD-edit target resolution + the rollout flag — the survivors of
the retired propose/review `prd_patches` project write tool (spec §"PPE-01
retire / keep").

This module used to also define the project-chat propose-tool dict and its
handler. Both were RETIRED once in-place editing was proven on both the
private and group surfaces (`project_chat_edit.py::apply_chat_edit_scoped`,
the single writer of `prds.payload_md` in a project chat context now) — see
that module's docstring for the ★ IDOR gate; see
`test_ppe01_retirement.py` for the retirement guard. This module's two
survivors are KEPT + imported by both the private route
(`routes/projects.py::project_chat_edit`) and the group agent
(`routes/projects.py::_classify_and_maybe_edit_group_prd`):

- `project_prd_edit_enabled()` — the request-time rollout flag, the write
  gate BOTH surfaces check before calling `apply_chat_edit_scoped`.
- `_resolve_prd_id()` / `_project_prd_ids()` — server-side target resolution
  over a project's OWN artifact manifest; the edit target is NEVER a client-
  or model-supplied id.
"""
from __future__ import annotations

import os

from app.db.artifacts import list_artifacts_for_project


# ── Feature flag (request-time, default OFF) ────────────────────────────────
def project_prd_edit_enabled() -> bool:
    """Read PROJECT_PRD_EDIT_ENABLED at REQUEST TIME (never import time),
    mirroring `design_agent._feature_enabled`. Default off; never default-1 in
    any commit. This is the security boundary — the propose tool is absent from
    the loop's registry when off (belt), and the handler is unreachable
    (braces). The frontend uses a SEPARATE `NEXT_PUBLIC_PROJECT_PRD_EDIT_ENABLED`
    for banner visibility only, which is not a security boundary."""
    val = (os.environ.get("PROJECT_PRD_EDIT_ENABLED") or "").strip().lower()
    return val in {"1", "true", "yes"}


def _project_prd_ids(project_id: int, dataset: str, company_id: str) -> list[dict]:
    """This project's PRD artifacts ({id, title}), tenant-scoped. Any manifest
    read error propagates (the caller is fail-closed)."""
    manifest = list_artifacts_for_project(
        project_id=project_id, dataset=dataset, company_id=company_id
    )
    return [
        {"id": it.get("id"), "title": (it.get("title") or "Untitled")}
        for it in manifest
        if it.get("type") == "prd"
    ]


def _resolve_prd_id(
    tool_input: dict, project_id: int, dataset: str, company_id: str
) -> tuple[int | None, str | None]:
    """Resolve which PRD to edit. Returns `(prd_id, refusal)`:

    - explicit `prd_id` given → `(prd_id, None)` (the §C gate still validates it).
    - omitted + exactly one project PRD → `(that_id, None)`.
    - omitted + zero PRDs → `(None, refusal)`.
    - omitted + >1 PRD → `(None, disambiguation string listing ids/titles)`.

    A manifest read error propagates to the caller (fail-closed)."""
    raw = tool_input.get("prd_id")
    if raw is not None:
        try:
            return int(raw), None
        except (TypeError, ValueError):
            return None, "That PRD id isn't valid."

    prds = _project_prd_ids(project_id, dataset, company_id)
    if not prds:
        return None, "This project has no PRD to edit."
    if len(prds) == 1:
        return int(prds[0]["id"]), None
    listing = ", ".join(f"{p['title']} [id {p['id']}]" for p in prds)
    return None, (
        "This project has more than one PRD — tell me which to edit by id: "
        f"{listing}."
    )
