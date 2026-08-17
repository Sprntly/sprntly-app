"""Project PRD-edit rollout flag — the survivor of the retired propose/review
`prd_patches` project write tool (spec §"PPE-01 retire / keep").

This module used to also define the project-chat propose-tool dict and its
handler, and the server-side edit-target resolution helpers that enumerated
a project's own PRDs. All three were RETIRED: in-place editing is proven on
both the private and group surfaces
(`project_chat_edit.py::apply_chat_edit_scoped`, the single writer of
`prds.payload_md` in a project chat context now) — see that module's
docstring for the ★ IDOR gate — and the edit target is now ALWAYS the
explicit open-drawer `prd_id` the client/turn already carries (parity with
main chat's tab-bound target, `routes/chat.py:87`), never server-resolved
across a project's own PRDs. This module's one survivor is KEPT + imported by
both the private route (`routes/projects.py::project_chat_edit`) and the
group agent (`routes/projects.py::_respond_as_group_agent`):

- `project_prd_edit_enabled()` — the request-time rollout flag, the write
  gate BOTH surfaces check before calling `apply_chat_edit_scoped`.
"""
from __future__ import annotations

import os


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
