"""Route-wiring proofs for the "added to a project" follow-ups:

  - the POST /members add path now publishes `member.added` on the added
    person's per-user channel (previously only the /tag path did), so an
    already-logged-in existing user can land live in the project;
  - a NET-NEW existing-user add (POST /members, /tag t_workspace) fires exactly
    one "added to project X" email; a re-add (t_member) fires none.

Same harness as `test_tag_candidate_api.py` (`company_client` over the
in-memory FakeSupabaseClient); `resolve_candidate` is pinned so the ROUTE's
per-tier dispatch is what's exercised. The email/publish transports are
monkeypatched at the seam the route imports, so these are wiring proofs — the
sender payload itself is proven in `test_drip_emails.py`.
"""
from __future__ import annotations

from app.db import projects as projects_db
from app.routes import projects as projects_routes
from tests._company_helpers import company_client


def _new_project(ctx, name: str = "Launch") -> dict:
    r = ctx.client.post("/v1/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def _project_member_rows(project_id: int) -> list[dict]:
    from app.db.client import require_client

    return (
        require_client()
        .table("project_members")
        .select("*")
        .eq("project_id", project_id)
        .execute()
        .data
        or []
    )


def _seed_workspace_member(ctx, workspace_id: str, *, role: str = "member") -> str:
    import uuid

    from app.db.workspaces import upsert_workspace_member

    uid = "ws-user-" + uuid.uuid4().hex[:8]
    upsert_workspace_member(workspace_id, uid, role)
    return uid


def _pin_resolver(monkeypatch, result: dict) -> None:
    monkeypatch.setattr(projects_db, "resolve_candidate", lambda pid, needle: result)


def _capture_publish(monkeypatch) -> list[tuple]:
    calls: list[tuple] = []
    monkeypatch.setattr(
        projects_routes.project_delegation,
        "_publish_member_added",
        lambda pid, uid, name: calls.append((pid, uid, name)),
    )
    return calls


def _capture_added_email(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    monkeypatch.setattr(
        projects_routes.drip_email,
        "send_project_added_email",
        lambda **kw: calls.append(kw) or True,
    )
    return calls


# ── Item 2: the /members add path publishes member.added ──────────────


def test_members_add_publishes_member_added(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    published = _capture_publish(monkeypatch)
    _pin_resolver(
        monkeypatch,
        {"tier": projects_db.TIER_WORKSPACE, "user_id": "added-uid", "email": "peer@acme.example", "name": "Peer"},
    )

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/members", json={"email": "peer@acme.example"}
    )
    assert r.status_code == 200, r.text
    # Exactly one member.added, on the added user's per-user channel.
    assert published == [(project["id"], "added-uid", project["name"])]


def test_members_re_add_does_not_publish(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    published = _capture_publish(monkeypatch)
    # An already-member re-add returns at the t_member branch — no write, no signal.
    _pin_resolver(
        monkeypatch,
        {"tier": projects_db.TIER_MEMBER, "member": {"user_id": ctx.user_id, "name": "Me"}},
    )
    r = ctx.client.post(
        f"/v1/projects/{project['id']}/members", json={"email": "me@acme.example"}
    )
    assert r.status_code == 200
    assert published == []


# ── Item 3: the "added to project X" email fires on net-new only ──────


def test_members_net_new_add_sends_one_email(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    emails = _capture_added_email(monkeypatch)
    _pin_resolver(
        monkeypatch,
        {"tier": projects_db.TIER_WORKSPACE, "user_id": "added-uid", "email": "peer@acme.example", "name": "Peer"},
    )

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/members", json={"email": "peer@acme.example"}
    )
    assert r.status_code == 200, r.text
    assert len(emails) == 1
    call = emails[0]
    assert call["to_email"] == "peer@acme.example"
    assert call["project_name"] == project["name"]
    assert f"/projects?id={project['id']}" in call["project_url"]


def test_members_re_add_sends_no_email(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    emails = _capture_added_email(monkeypatch)
    _pin_resolver(
        monkeypatch,
        {"tier": projects_db.TIER_MEMBER, "member": {"user_id": ctx.user_id, "name": "Me"}},
    )
    r = ctx.client.post(
        f"/v1/projects/{project['id']}/members", json={"email": "me@acme.example"}
    )
    assert r.status_code == 200
    assert emails == []


def test_tag_workspace_add_sends_one_email(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    emails = _capture_added_email(monkeypatch)
    uid = _seed_workspace_member(ctx, project["workspace_id"])
    _pin_resolver(
        monkeypatch,
        {"tier": projects_db.TIER_WORKSPACE, "user_id": uid, "email": "wspeer@acme.example", "name": "Wanda"},
    )

    r = ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": "Wanda"})
    assert r.status_code == 200, r.text
    assert len(emails) == 1
    assert emails[0]["to_email"] == "wspeer@acme.example"
    assert emails[0]["project_name"] == project["name"]


def test_tag_member_re_add_sends_no_email(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    emails = _capture_added_email(monkeypatch)
    _pin_resolver(
        monkeypatch,
        {"tier": projects_db.TIER_MEMBER, "member": {"user_id": ctx.user_id, "name": "Me"}},
    )
    r = ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": "Me"})
    assert r.status_code == 200
    assert emails == []
