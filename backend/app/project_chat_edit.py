"""The shared scoped chat-edit callable — the SINGLE place that writes
`prds.payload_md` in response to a free-form chat instruction, on either the
main chat's PRD tab (`project_id=None`) or a project chat surface
(`project_id=<int>`, private today, group in a later ticket).

★ LOAD-BEARING SECURITY ITEM ★ — the edit_prd cross-project IDOR gate.

The reused main-chat editor only ever checked cross-TENANT ownership
(`require_owned_prd`) — it never asked whether a PRD belongs to the CALLER'S
project, because the main chat has no project concept. Handed a project
context, that gap lets a member of project A edit a PRD that lives on project
B in the SAME workspace (the model/client controls which `prd_id` an intent
names). When `project_id` is passed, `assert_prd_on_project` (the kept and
promoted cross-project scope guard, `project_prd_gate.py`) runs FIRST —
before any `payload_md` read or write — and is fail-closed by construction (a
manifest read error propagates, never a default-allow).

`project_id=None` (the main chat's own PRD tab) skips the project gate
entirely and behaves byte-for-byte like the pre-extraction `chat_edit` route
body — this module is a pure LIFT of that body, not a rewrite.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import HTTPException

from app.db.prds import save_prd_version, update_prd_content, get_prd_rendered
from app.deps.ownership import require_owned_prd
from app.project_prd_gate import assert_prd_on_project

logger = logging.getLogger(__name__)


def _actor(company) -> str:
    """Who to record on a version snapshot — mirrors `routes/prd.py`'s own
    `_actor` exactly (same total, never-raises getattr fallback chain).
    Reimplemented here rather than imported: `routes/prd.py` delegates its
    `chat_edit` route to THIS module, so importing `_actor` back from
    `routes/prd` would be a circular import for one one-line helper."""
    return getattr(company, "user_email", None) or getattr(company, "user_id", None) or "auto"


def apply_chat_edit_scoped(
    prd_id: int,
    instruction: str,
    company,
    *,
    project_id: int | None = None,
    dataset: str | None = None,
) -> dict:
    """Apply a free-form chat edit instruction to a PRD, versioned and scoped.

    `company` is a `WorkspaceContext`-shaped object carrying `.company_id` /
    `.workspace_id` (and, best-effort, `.user_email`/`.user_id` for the
    version's `saved_by`). When `project_id` is not None, `dataset` MUST also
    be given — both are required by `assert_prd_on_project`'s manifest read.

    Returns `{"prd", "sections_changed", "summary"}` — the SAME shape the
    pre-extraction `chat_edit` route returned. Raises `HTTPException(409)` on
    an empty PRD and `HTTPException(502)` when the editor returns no usable
    HTML — both status codes the main-chat route's contract already promised
    callers, now enforced once, here, instead of duplicated per caller.
    `ProjectPrdWriteDenied` (from `assert_prd_on_project`) and the cross-
    tenant `HTTPException(404)` (from `require_owned_prd`) propagate
    unchanged — callers that want a softer "no-edit" reply instead of a raw
    404/denial catch those themselves (see `routes/projects.py`'s new route).
    """
    from app.prd_edit import apply_chat_edit  # local: mirrors routes/prd.py's own lazy import of the same call

    if project_id is not None:
        # ★ cross-PROJECT gate — BEFORE any payload_md read or write.
        assert_prd_on_project(
            prd_id=prd_id, project_id=project_id,
            dataset=dataset, company_id=company.company_id,
        )

    # Cross-TENANT gate (reused unchanged — the pre-extraction body's only check).
    row = require_owned_prd(prd_id, company.company_id, company.workspace_id)

    # Edit the RAW payload_md (the pure PRD HTML) — same discipline as the
    # input-answer editor: design-agent 'applied' patches are folded on read by
    # get_prd_rendered, so editing the raw doc keeps them folding once.
    prd_html = (row.get("payload_md") or "").strip()
    if not prd_html:
        raise HTTPException(409, "PRD has no content to edit yet")

    try:
        edit = apply_chat_edit(
            prd_html, instruction, enterprise_id=company.company_id
        )
    except RuntimeError as exc:
        raise HTTPException(502, f"Could not apply the edit: {exc}")

    if edit["sections_changed"]:
        # Snapshot the pre-edit content so the change is undoable (mirrors
        # PUT /{id} and the input-answer path).
        try:
            save_prd_version(prd_id, row.get("title", ""), prd_html, saved_by=_actor(company))
        except Exception:
            logger.warning(
                "auto-version snapshot failed for prd_id=%s before chat edit "
                "(undo point not captured)", prd_id, exc_info=True,
            )
        update_prd_content(prd_id, row.get("title", ""), edit["html"])
    # No sections changed → the editor judged the instruction wasn't an edit;
    # leave the stored document untouched (no snapshot, no write).

    return {
        "prd": get_prd_rendered(prd_id),
        "sections_changed": edit["sections_changed"],
        "summary": edit["summary"],
    }
