"""Shared test helpers for the projects surface.

`_seed_same_tenant_non_member` mints a second real company_members +
workspace_members row in the SAME company/workspace as an existing
`company_client()` context — a caller who resolves `require_workspace`
successfully but is deliberately never added to any `project_members`
row. This is the exact shape needed to prove membership-gating (AD-P11):
a same-tenant caller who isn't a project member must be distinguishable
from a fully foreign-tenant caller (404) — they get 403 instead.
"""
from __future__ import annotations

import uuid

from tests._company_helpers import supabase_bearer


def seed_same_tenant_non_member(ctx) -> tuple[str, dict]:
    """Returns `(user_id, headers)` — pass `headers=` on `ctx.client` calls
    to drive requests as this second, same-tenant, non-project-member user."""
    from app.db.client import require_client
    from app.db.workspaces import ensure_default_workspace, upsert_workspace_member

    user_id = "non-member-" + uuid.uuid4().hex[:8]
    require_client().table("company_members").insert(
        {
            "id": uuid.uuid4().hex,
            "company_id": ctx.company_id,
            "user_id": user_id,
            "role": "member",
        }
    ).execute()
    ws = ensure_default_workspace(ctx.company_id)
    upsert_workspace_member(ws["id"], user_id, "member")
    return user_id, supabase_bearer(user_id)
