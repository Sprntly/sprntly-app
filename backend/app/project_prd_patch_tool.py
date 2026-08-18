"""Project PRD-edit helper — the survivor of the retired propose/review
`prd_patches` project write tool (spec §"PPE-01 retire / keep"). The rollout
flag it used to read was retired when project PRD-edit went unconditionally on.

This module used to also define the project-chat propose-tool dict and its
handler, and the server-side edit-target resolution helpers that enumerated
a project's own PRDs. All three were RETIRED: in-place editing is proven on
both the private and group surfaces
(`project_chat_edit.py::apply_chat_edit_scoped`, the single writer of
`prds.payload_md` in a project chat context now) — see that module's
docstring for the ★ IDOR gate — and the edit target is now ALWAYS the
explicit open-drawer `prd_id` the client/turn already carries (parity with
main chat's tab-bound target, `routes/chat.py:87`), never server-resolved
across a project's own PRDs. This module's one survivor is `project_prd_edit_enabled()`, now an always-true
helper still called by `routes/design_agent.py` so its three pending-patch
routes stay served whenever project PRD-edit is on (which is now always).
"""
from __future__ import annotations


# ── Project PRD-edit: unconditionally ON ────────────────────────────────────
def project_prd_edit_enabled() -> bool:
    """Project PRD-edit is now always on — the rollout flag was retired. Kept as
    an always-true helper because `routes/design_agent.py` still calls it to keep
    the three pending-patch routes served whenever project PRD-edit is on (now
    always). Membership + the cross-project/cross-tenant IDOR gates in
    `project_chat_edit.py::apply_chat_edit_scoped` remain the real boundary."""
    return True
