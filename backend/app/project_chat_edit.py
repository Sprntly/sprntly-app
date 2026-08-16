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
    from app.prd_questions import apply_chat_edit  # local: mirrors routes/prd.py's own lazy import of the same call

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


def propose_chat_edit_scoped(
    prd_id: int,
    instruction: str,
    company,
    *,
    project_id: int,
    dataset: str | None = None,
    conversation_id: int | None = None,
    surface: str,
    client_message_id: str | None = None,
) -> dict:
    """PROPOSE a project PRD edit without writing it: run the SAME two gates
    `apply_chat_edit_scoped` runs (the ★ cross-project gate then the
    cross-tenant gate — BOTH before any read or compute), compute the patch,
    and STORE it keyed by a single-use token. Nothing in `prds` is touched.

    This is the propose half of the confirmation gate for the project chat
    surfaces (private + group). The main chat's own PRD-tab edit
    (`project_id=None`) does NOT go through here — it stays on
    `apply_chat_edit_scoped`'s immediate-apply path, unchanged.

    When the editor reports no `sections_changed`, the instruction was not an
    edit — there is nothing to confirm, so no token is minted and
    `{"proposed": False, ...}` is returned. Otherwise a token is minted, the
    computed patch (`proposed_html`/`proposed_title`) plus the original
    `instruction` are persisted, and `{"proposed": True, "token", ...}` is
    returned. The confirm step commits exactly the stored patch.

    `ProjectPrdWriteDenied` (cross-project) and `HTTPException(404)`
    (cross-tenant, from `require_owned_prd`) propagate unchanged — callers
    that want a soft "no-edit" reply catch them (see `routes/projects.py`).
    """
    from app.prd_questions import apply_chat_edit  # local: mirrors the immediate path's lazy import
    from app.db.prd_edit_proposals import create_proposal

    # ★ cross-PROJECT gate then cross-TENANT gate — BEFORE any payload_md read
    # or compute, identical to the immediate-apply path. A member of project A
    # proposing against project B, or a probed cross-tenant id, is refused here
    # and NO proposal row is ever written.
    assert_prd_on_project(
        prd_id=prd_id, project_id=project_id,
        dataset=dataset, company_id=company.company_id,
    )
    row = require_owned_prd(prd_id, company.company_id, company.workspace_id)

    base_html = (row.get("payload_md") or "").strip()
    if not base_html:
        raise HTTPException(409, "PRD has no content to edit yet")

    try:
        edit = apply_chat_edit(
            base_html, instruction, enterprise_id=company.company_id
        )
    except RuntimeError as exc:
        raise HTTPException(502, f"Could not apply the edit: {exc}")

    if not edit["sections_changed"]:
        # The editor judged the instruction wasn't an edit — nothing to
        # confirm. No token, no row.
        return {
            "proposed": False,
            "summary": edit["summary"],
            "sections_changed": [],
        }

    token = str(uuid.uuid4())
    create_proposal(
        token=token,
        prd_id=prd_id,
        project_id=project_id,
        conversation_id=conversation_id,
        surface=surface,
        company_id=company.company_id,
        workspace_id=company.workspace_id,
        instruction=instruction,
        base_html=base_html,
        proposed_title=row.get("title", ""),
        proposed_html=edit["html"],
        summary=edit["summary"],
        sections_changed=edit["sections_changed"],
        client_message_id=client_message_id,
    )
    return {
        "proposed": True,
        "token": token,
        "summary": edit["summary"],
        "sections_changed": edit["sections_changed"],
        "prd_id": prd_id,
    }


def apply_proposed_chat_edit(
    token: str,
    company,
    *,
    project_id: int,
    dataset: str | None = None,
) -> dict:
    """APPLY a previously-proposed edit: commit exactly the token's stored
    patch. The stored token target is NOT trusted — the two IDOR gates run
    AGAIN, on the CALLER, at this step:

      - `get_proposal` is tenant-scoped (a cross-tenant token → no row → 404)
        AND expiry-filtered (an expired row is never returned → 404).
      - the stored `project_id` must equal the caller's `project_id`.
      - `assert_prd_on_project` + `require_owned_prd` re-run on the STORED
        `prd_id` against the caller — an attacker cannot apply a token whose
        target they do not own even if they somehow hold the token.

    Single-use: the proposal is deleted BEFORE the write commits, so a replay
    of a consumed token finds no row (404, no double-write). Concurrent-change
    guard: if the PRD's current content differs from what was proposed against
    (`base_html`), nothing is clobbered — the stale proposal is deleted and a
    conflict result returned.

    Returns `{"applied": True, "instruction", "prd", "sections_changed",
    "summary"}` on success (carrying the original `instruction` so the route
    can persist the turn pair), or `{"applied": False, "conflict": True}` on a
    concurrent change. Raises `HTTPException(404)` when the token is unknown,
    expired, cross-tenant, or its stored target the caller cannot access.
    """
    from app.db.prd_edit_proposals import get_proposal, delete_proposal

    proposal = get_proposal(token, company.company_id, company.workspace_id)
    if proposal is None:
        # Unknown, expired, already-consumed, or cross-tenant — indistinguishable
        # by design (no existence disclosure).
        raise HTTPException(404, "Proposal not found")

    if proposal["project_id"] != project_id:
        raise HTTPException(404, "Proposal not found")

    prd_id = proposal["prd_id"]

    # ★ RE-RUN both IDOR gates on the CALLER against the STORED prd_id — the
    # token target is untrusted exactly like a client-supplied prd_id.
    assert_prd_on_project(
        prd_id=prd_id, project_id=project_id,
        dataset=dataset, company_id=company.company_id,
    )
    row = require_owned_prd(prd_id, company.company_id, company.workspace_id)

    current_html = (row.get("payload_md") or "").strip()
    if current_html != proposal["base_html"]:
        # The PRD moved since the proposal was made — do NOT clobber. Drop the
        # now-stale proposal so its token can't apply later either.
        delete_proposal(token, company.company_id)
        return {"applied": False, "conflict": True}

    proposed_title = proposal.get("proposed_title") or ""
    proposed_html = proposal["proposed_html"]

    # Single-use consume BEFORE the write: a replay of this token now finds no
    # row (404) and cannot double-write.
    delete_proposal(token, company.company_id)

    try:
        save_prd_version(prd_id, proposed_title, current_html, saved_by=_actor(company))
    except Exception:
        logger.warning(
            "auto-version snapshot failed for prd_id=%s before confirmed chat edit "
            "(undo point not captured)", prd_id, exc_info=True,
        )
    update_prd_content(prd_id, proposed_title, proposed_html)

    return {
        "applied": True,
        "instruction": proposal["instruction"],
        "prd": get_prd_rendered(prd_id),
        "sections_changed": proposal.get("sections_changed") or [],
        "summary": proposal.get("summary") or "",
        # Routing context the confirm route needs to persist the turn pair
        # (private) or post the completed group turn (group) — the proposal
        # row is consumed above, so these ride back on the result.
        "surface": proposal.get("surface"),
        "conversation_id": proposal.get("conversation_id"),
        "client_message_id": proposal.get("client_message_id"),
    }
