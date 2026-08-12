"""Project-chat PRD-edit propose tool (F11) — a PLAIN `run_tool_loop` tool.

Registry boundary (AD17): `PROPOSE_PROJECT_PRD_PATCH_TOOL` is a plain tool dict
for the PROJECT-chat registries (the private individual responder and the
@Sprntly group agent). It is NOT a Design-Agent action tool and NOT an
exit-sentinel — it must NEVER be added to `design_agent/tools.py`'s
`ACTION_TOOLS`/`SENTINEL_TOOLS` (AD17's fixed caps are about the Design-Agent
registry only). It does not end the loop; the model may keep talking after
proposing.

F11: the write path only ever inserts a SIBLING `prd_patches` row — it NEVER
touches `prds.payload_md`. The rendered PRD folds applied patches on read via
the existing `apply_patches_to_prd_md`.

Security: every write goes through the §C `assert_prd_on_project` gate BEFORE
`insert_patch`, fail-closed. `workspace_id` MUST be the caller's company UUID
(the same value the Design-Agent accept/reject routes filter on), so the same
company's accept/reject can see the row.
"""
from __future__ import annotations

import logging
import os

from app.db.artifacts import list_artifacts_for_project
from app.db.prd_patches import insert_patch
from app.project_prd_gate import ProjectPrdWriteDenied, assert_prd_on_project

logger = logging.getLogger(__name__)


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


PROPOSE_PROJECT_PRD_PATCH_TOOL = {
    "name": "propose_prd_patch",
    "description": (
        "Propose an edit to one of THIS project's PRDs as a pending suggestion "
        "the team reviews and accepts or rejects — it is NOT applied "
        "immediately, and it never overwrites the PRD directly. Call this ONLY "
        "when the conversation has produced a concrete decision or change the "
        "PRD should reflect (e.g. 'rewrite the problem statement', 'add a "
        "success metric', 'tighten scope'). Provide the exact markdown to add "
        "or change in `patch_md` and a one-line `rationale` for why. Pass "
        "`prd_id` when you know which PRD (from list_project_artifacts); omit it "
        "and it resolves automatically when the project has exactly one PRD. Do "
        "NOT call this for a purely conversational answer, an opinion or visual "
        "request, or when the project has no PRD — just answer in text instead."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "prd_id": {
                "type": "integer",
                "description": (
                    "the id of the PRD to edit, from list_project_artifacts; "
                    "optional when the project has exactly one PRD"
                ),
            },
            "rationale": {
                "type": "string",
                "description": "one line on why this edit is proposed",
            },
            "patch_md": {
                "type": "string",
                "description": "the markdown to add to / change in the PRD",
            },
        },
        "required": ["rationale", "patch_md"],
        "additionalProperties": False,
    },
}


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


def handle_propose_prd_patch(
    tool_input: dict,
    *,
    project_id: int,
    dataset: str,
    company_id: str,
    workspace_id: str,
) -> str:
    """The write path (gate BEFORE insert). Returns a short tool-result string
    for the model — never raises into `run_tool_loop`.

    `workspace_id` MUST be the caller's company UUID (== `company_id` here),
    matching the value the Design-Agent accept/reject routes filter on."""
    prd_id, refusal = _resolve_prd_id(tool_input, project_id, dataset, company_id)
    if prd_id is None:
        return refusal or "I couldn't work out which PRD to edit."

    try:
        assert_prd_on_project(
            prd_id=prd_id, project_id=project_id, dataset=dataset, company_id=company_id
        )
    except ProjectPrdWriteDenied:
        return "I can only edit a PRD that's attached to this project."
    except Exception:  # noqa: BLE001 — fail-closed: any gate read error refuses
        logger.warning(
            "project_prd_gate_error prd_id=%s project_id=%s", prd_id, project_id
        )
        return "I couldn't verify that PRD just now, so I didn't change anything."

    rationale = (tool_input.get("rationale") or "").strip()
    patch_md = (tool_input.get("patch_md") or "").strip()
    if not rationale or not patch_md:
        return "I need both a rationale and the proposed edit text to suggest a change."

    try:
        row = insert_patch(
            prd_id=prd_id,
            prototype_id=None,            # §B nullable — no prototype anchor
            workspace_id=workspace_id,    # == company_id (accept/reject filter key)
            rationale=rationale,
            patch_md=patch_md,
        )
    except Exception:  # noqa: BLE001 — never surface a write error into the loop
        logger.warning(
            "project_prd_patch_insert_failed prd_id=%s project_id=%s",
            prd_id, project_id,
        )
        return "I couldn't save that edit just now — nothing was changed."

    return (
        "I've proposed that PRD edit — it's pending your review "
        f"(patch #{row['id']}). Accept it to apply the change."
    )
