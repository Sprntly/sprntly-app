"""Multi-workspace (2026-07) — workspaces routes, require_workspace, invites.

Covers:
  - GET /v1/workspaces self-heals the default workspace and lists it with the
    caller's effective role (org owner → admin).
  - POST /v1/workspaces creates a non-default workspace with a deduped slug,
    the creator's admin membership, and a bound '{company}--{slug}' dataset.
  - require_workspace: missing header → default workspace fallback; a forged
    X-Workspace-Id from another company → 404 (no existence disclosure);
    a plain org member without a workspace_members row → 403.
  - Invites: POST /v1/team/invites stores workspace_ids; accept grants
    workspace_members rows (viewer role included — the old CHECK bug);
    stale ids fall back to the default workspace.
  - POST /v1/onboarding/workspace renames the default workspace (never a
    second one), grants the caller workspace-admin, binds the dataset.
"""
from __future__ import annotations

import uuid

import app.auth  # noqa: F401 — load app.config + app.auth into sys.modules

from tests._company_helpers import (
    company_client,
    seed_company,
    supabase_bearer,
)


def _rows(table: str, **eq) -> list[dict]:
    from app.db.client import require_client

    q = require_client().table(table).select("*")
    for k, v in eq.items():
        q = q.eq(k, v)
    return q.execute().data or []


def _seed_profile(user_id: str, email: str) -> None:
    from app.db.client import require_client

    require_client().table("profiles").insert(
        {"id": user_id, "email": email, "first_name": "T", "last_name": "U"}
    ).execute()


# ─────────────────────── /v1/workspaces ───────────────────────


def test_list_workspaces_self_heals_default(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    assert _rows("workspaces", company_id=ctx.company_id) == []

    r = ctx.client.get("/v1/workspaces")
    assert r.status_code == 200
    # The caller's COMPANY-level role rides along — the frontend gates its
    # create-workspace affordances on this, not the per-workspace roles.
    assert r.json()["org_role"] == "owner"
    ws = r.json()["workspaces"]
    assert len(ws) == 1
    assert ws[0]["is_default"] is True
    assert ws[0]["role"] == "admin"  # org owner → implicit admin
    # The default workspace's dataset is the bare company slug.
    assert ws[0]["dataset"] == "acme"


def test_create_workspace_binds_dataset_and_membership(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post("/v1/workspaces", json={"name": "Notifications"})
    assert r.status_code == 201
    body = r.json()
    assert body["is_default"] is False
    assert body["slug"] == "notifications"
    assert body["dataset"] == "acme--notifications"

    members = _rows("workspace_members", workspace_id=body["id"])
    assert [(m["user_id"], m["role"]) for m in members] == [(ctx.user_id, "admin")]

    ds = _rows("datasets", slug="acme--notifications")
    assert ds and ds[0]["workspace_id"] == body["id"]

    # Same name again → deduped slug, separate dataset.
    r2 = ctx.client.post("/v1/workspaces", json={"name": "Notifications"})
    assert r2.status_code == 201
    assert r2.json()["slug"] == "notifications-2"


def test_delete_refuses_default_workspace(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    default = ctx.client.get("/v1/workspaces").json()["workspaces"][0]
    r = ctx.client.delete(f"/v1/workspaces/{default['id']}")
    assert r.status_code == 409


# ─────────────────────── require_workspace ───────────────────────


def test_forged_workspace_header_is_404(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    other_company = seed_company(user_id="other-" + uuid.uuid4().hex[:8], slug="rival")
    from app.db.workspaces import ensure_default_workspace

    foreign_ws = ensure_default_workspace(other_company)

    r = ctx.client.get(
        "/v1/conversations", headers={"X-Workspace-Id": foreign_ws["id"]}
    )
    assert r.status_code == 404


def test_missing_header_falls_back_to_default(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.get("/v1/conversations")
    assert r.status_code == 200
    assert r.json()["conversations"] == []


def test_plain_member_without_workspace_row_is_403(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.db.client import require_client

    member_id = "member-" + uuid.uuid4().hex[:8]
    require_client().table("company_members").insert(
        {
            "id": uuid.uuid4().hex,
            "company_id": ctx.company_id,
            "user_id": member_id,
            "role": "member",
        }
    ).execute()

    r = ctx.client.get("/v1/conversations", headers=supabase_bearer(member_id))
    assert r.status_code == 403

    # Granting a workspace_members row on the default workspace unlocks it.
    from app.db.workspaces import ensure_default_workspace, upsert_workspace_member

    default = ensure_default_workspace(ctx.company_id)
    upsert_workspace_member(default["id"], member_id, "member")
    r2 = ctx.client.get("/v1/conversations", headers=supabase_bearer(member_id))
    assert r2.status_code == 200


# ─────────────────────── invites → workspace grants ───────────────────────


def test_invite_carries_workspace_ids_and_accept_grants(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    ws = ctx.client.post("/v1/workspaces", json={"name": "Checkout"}).json()

    email = f"invitee-{uuid.uuid4().hex[:8]}@example.com"
    r = ctx.client.post(
        "/v1/team/invites",
        json={"email": email, "role": "viewer", "workspace_ids": [ws["id"]]},
    )
    assert r.status_code == 201, r.text
    assert r.json()["workspace_ids"] == [ws["id"]]

    invitee = "invitee-" + uuid.uuid4().hex[:8]
    _seed_profile(invitee, email)
    r2 = ctx.client.post("/v1/invites/accept", headers=supabase_bearer(invitee))
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["company_id"] == ctx.company_id
    assert body["role"] == "viewer"
    assert body["workspace_ids"] == [ws["id"]]

    granted = _rows("workspace_members", workspace_id=ws["id"], user_id=invitee)
    assert granted and granted[0]["role"] == "viewer"


def test_invite_with_unknown_workspace_id_is_400(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/team/invites",
        json={
            "email": "x@example.com",
            "role": "member",
            "workspace_ids": [uuid.uuid4().hex],
        },
    )
    assert r.status_code == 400


def test_accept_with_stale_workspace_falls_back_to_default(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    ws = ctx.client.post("/v1/workspaces", json={"name": "Doomed"}).json()
    email = f"stale-{uuid.uuid4().hex[:8]}@example.com"
    assert (
        ctx.client.post(
            "/v1/team/invites",
            json={"email": email, "role": "member", "workspace_ids": [ws["id"]]},
        ).status_code
        == 201
    )
    # The workspace is deleted between invite and accept.
    assert ctx.client.delete(f"/v1/workspaces/{ws['id']}").status_code == 204

    invitee = "stale-" + uuid.uuid4().hex[:8]
    _seed_profile(invitee, email)
    r = ctx.client.post("/v1/invites/accept", headers=supabase_bearer(invitee))
    assert r.status_code == 200, r.text
    granted_ws = r.json()["workspace_ids"]
    assert len(granted_ws) == 1
    from app.db.workspaces import default_workspace_for_company

    assert granted_ws[0] == default_workspace_for_company(ctx.company_id)["id"]


# ─────────────────── member workspace grants (Settings → Team) ───────────────────


def test_members_list_carries_workspace_ids(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    ws = ctx.client.post("/v1/workspaces", json={"name": "Checkout"}).json()

    from app.db.client import require_client
    from app.db.workspaces import upsert_workspace_member

    member_id = "member-" + uuid.uuid4().hex[:8]
    require_client().table("company_members").insert(
        {
            "id": uuid.uuid4().hex,
            "company_id": ctx.company_id,
            "user_id": member_id,
            "role": "member",
        }
    ).execute()
    upsert_workspace_member(ws["id"], member_id, "member")

    rows = ctx.client.get("/v1/team/members").json()["members"]
    by_user = {r["user_id"]: r for r in rows}
    assert by_user[member_id]["workspace_ids"] == [ws["id"]]
    # The org owner created "Checkout" (creator auto-membership) but has no
    # grant on the default workspace — access there is implicit.
    assert by_user[ctx.user_id]["workspace_ids"] == [ws["id"]]


def test_put_member_workspaces_replaces_grants(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    default = ctx.client.get("/v1/workspaces").json()["workspaces"][0]
    ws2 = ctx.client.post("/v1/workspaces", json={"name": "Growth"}).json()

    from app.db.client import require_client
    from app.db.workspaces import upsert_workspace_member

    member_id = "member-" + uuid.uuid4().hex[:8]
    require_client().table("company_members").insert(
        {
            "id": uuid.uuid4().hex,
            "company_id": ctx.company_id,
            "user_id": member_id,
            "role": "member",
        }
    ).execute()
    upsert_workspace_member(default["id"], member_id, "viewer")

    r = ctx.client.put(
        f"/v1/team/members/{member_id}/workspaces",
        json={"workspace_ids": [ws2["id"]]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["workspace_ids"] == [ws2["id"]]

    # The default-workspace grant is gone; the Growth grant defaults to
    # role 'member'.
    assert _rows("workspace_members", workspace_id=default["id"], user_id=member_id) == []
    granted = _rows("workspace_members", workspace_id=ws2["id"], user_id=member_id)
    assert granted and granted[0]["role"] == "member"

    # Re-granting a workspace keeps the existing per-workspace role.
    upsert_workspace_member(default["id"], member_id, "viewer")
    r2 = ctx.client.put(
        f"/v1/team/members/{member_id}/workspaces",
        json={"workspace_ids": [default["id"], ws2["id"]]},
    )
    assert r2.status_code == 200
    kept = _rows("workspace_members", workspace_id=default["id"], user_id=member_id)
    assert kept and kept[0]["role"] == "viewer"


def test_put_member_workspaces_unknown_id_is_400(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.db.client import require_client

    member_id = "member-" + uuid.uuid4().hex[:8]
    require_client().table("company_members").insert(
        {
            "id": uuid.uuid4().hex,
            "company_id": ctx.company_id,
            "user_id": member_id,
            "role": "member",
        }
    ).execute()

    r = ctx.client.put(
        f"/v1/team/members/{member_id}/workspaces",
        json={"workspace_ids": [uuid.uuid4().hex]},
    )
    assert r.status_code == 400

    r2 = ctx.client.put(
        f"/v1/team/members/{uuid.uuid4().hex}/workspaces",
        json={"workspace_ids": []},
    )
    assert r2.status_code == 404


# ─────────────────────── onboarding workspace step ───────────────────────


def test_onboarding_workspace_renames_default(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/onboarding/workspace",
        json={
            "name": "Sprntly App",
            # The six "Your workspace" fields now land on the workspace row.
            "team_scope": "Owns food logging end to end.",
            "team_strategy": "Grow WAU this half.",
            "team_roadmap": "Q3: logging v2.",
            "sizing_methodology": "Fibonacci points.",
            "additional_context": "We call it 'sleep sync'.",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_default"] is True
    assert body["name"] == "Sprntly App"
    assert body["slug"] == "default"  # rename never churns the slug

    # Never a second workspace; caller became workspace-admin; dataset bound.
    all_ws = _rows("workspaces", company_id=ctx.company_id)
    assert len(all_ws) == 1
    # The five typed fields were written to the workspace row, not companies.
    row = all_ws[0]
    assert row["team_scope"] == "Owns food logging end to end."
    assert row["team_strategy"] == "Grow WAU this half."
    assert row["team_roadmap"] == "Q3: logging v2."
    assert row["sizing_methodology"] == "Fibonacci points."
    assert row["additional_context"] == "We call it 'sleep sync'."
    members = _rows("workspace_members", workspace_id=body["id"], user_id=ctx.user_id)
    assert members and members[0]["role"] == "admin"
    ds = _rows("datasets", slug="acme")
    assert ds and ds[0]["workspace_id"] == body["id"]

    # Idempotent on a resumed step.
    r2 = ctx.client.post("/v1/onboarding/workspace", json={"name": "Sprntly App"})
    assert r2.status_code == 200
    assert len(_rows("workspaces", company_id=ctx.company_id)) == 1


def test_patch_workspace_updates_owned_fields(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    default = ctx.client.get("/v1/workspaces").json()["workspaces"][0]

    # A partial PATCH writes only the provided fields (name + a subset).
    r = ctx.client.patch(
        f"/v1/workspaces/{default['id']}",
        json={"name": "Growth", "team_scope": "Activation & retention."},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Growth"
    assert body["team_scope"] == "Activation & retention."
    # The GET serializer exposes the workspace-owned fields too.
    listed = ctx.client.get("/v1/workspaces").json()["workspaces"][0]
    assert listed["team_scope"] == "Activation & retention."

    row = _rows("workspaces", id=default["id"])[0]
    assert row["name"] == "Growth"
    assert row["team_scope"] == "Activation & retention."
