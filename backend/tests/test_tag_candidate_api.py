"""Fast-lane route tests for the tag-action surface
(`POST /v1/projects/{id}/tag`, `GET .../candidates`) + the `add_member`-route
IDOR fix (AD-TNM1). Same harness as `test_projects_routes.py`
(`company_client` over the in-memory FakeSupabaseClient); `resolve_candidate`
is monkeypatched to pin the tier so these tests exercise the ROUTE's per-tier
dispatch, its tenancy re-assertion, and the real writes — the resolver's own
classification is proven in `test_resolve_candidate.py`.

The load-bearing tests are the per-tier MUTATION PROOFS (AC-6): stubbing the
`get_workspace_member` re-assertion always-true lets a cross-tenant user in,
restoring it blocks them; the `add_member`-route fix flipped off (classifier
returns an addable tier for a foreign uid) re-opens the old IDOR, on closes it.
"""
from __future__ import annotations

import logging

from app.db import projects as projects_db
from app.routes import projects as projects_routes
from tests._company_helpers import company_client
from tests._project_helpers import seed_same_tenant_non_member


# ── helpers ──────────────────────────────────────────────────────────────


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


def _invite_rows(company_id: str) -> list[dict]:
    from app.db.client import require_client

    return (
        require_client()
        .table("workspace_invites")
        .select("*")
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )


def _seed_workspace_member(ctx, workspace_id: str, *, role: str = "member") -> str:
    """A real user in the project's workspace (so the route's live
    `get_workspace_member` re-assertion passes). Returns the user id."""
    import uuid

    from app.db.workspaces import upsert_workspace_member

    uid = "ws-user-" + uuid.uuid4().hex[:8]
    upsert_workspace_member(workspace_id, uid, role)
    return uid


def _pin_resolver(monkeypatch, result: dict) -> None:
    monkeypatch.setattr(projects_db, "resolve_candidate", lambda pid, needle: result)


def _capture_invite_email(monkeypatch, status: str = "sent") -> list[dict]:
    """Replace the invite-email send bound INTO the routes module (it imports
    the name directly) and record every call's kwargs."""
    calls: list[dict] = []

    def _fake(email, **kwargs):
        calls.append({"email": email, **kwargs})
        return status

    monkeypatch.setattr(projects_routes, "send_invite_email", _fake)
    return calls


# ── AC-1..AC-5: per-tier dispatch ────────────────────────────────────────


def test_tag_member_returns_member_no_write(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    before = _project_member_rows(project["id"])
    _pin_resolver(
        monkeypatch,
        {"tier": projects_db.TIER_MEMBER, "member": {"user_id": ctx.user_id, "name": "Me"}},
    )
    r = ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": "Me"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tier"] == "t_member"
    assert body["member"]["user_id"] == ctx.user_id
    assert _project_member_rows(project["id"]) == before  # zero writes


def test_tag_workspace_adds_member(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    uid = _seed_workspace_member(ctx, project["workspace_id"])
    _pin_resolver(monkeypatch, {"tier": projects_db.TIER_WORKSPACE, "user_id": uid})

    r = ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": "Fortune"})
    assert r.status_code == 200, r.text
    assert r.json()["tier"] == "t_workspace"
    rows = _project_member_rows(project["id"])
    assert uid in {m["user_id"] for m in rows}

    # Re-tag is an idempotent no-op — no duplicate row.
    r2 = ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": "Fortune"})
    assert r2.status_code == 200
    rows2 = _project_member_rows(project["id"])
    assert len([m for m in rows2 if m["user_id"] == uid]) == 1


def test_tag_company_creates_workspace_join_invite(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    calls = _capture_invite_email(monkeypatch)
    _pin_resolver(
        monkeypatch,
        {"tier": projects_db.TIER_COMPANY, "user_id": "u-x", "email": "peer@acme.example"},
    )

    r = ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": "peer@acme.example"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"tier": "t_company", "invited": True, "email_status": "sent"}

    invites = _invite_rows(ctx.company_id)
    assert len(invites) == 1
    inv = invites[0]
    assert inv["project_id"] == project["id"]
    assert project["workspace_id"] in (inv["workspace_ids"] or [])
    # No project_members write at tag time — that lands on accept.
    assert not any(m["user_id"] == "u-x" for m in _project_member_rows(project["id"]))
    # Email carried the project NAME.
    assert calls and calls[0]["project_name"] == project["name"]


def test_tag_newuser_creates_full_invite_with_project(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    calls = _capture_invite_email(monkeypatch)
    _pin_resolver(monkeypatch, {"tier": projects_db.TIER_NEWUSER, "email": "new@acme.example"})

    r = ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": "new@acme.example"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tier"] == "t_newuser" and body["invited"] is True

    invites = _invite_rows(ctx.company_id)
    assert len(invites) == 1 and invites[0]["project_id"] == project["id"]
    assert calls[0]["project_name"] == project["name"]


def test_tag_refuse_403_no_write_no_disclosure(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)

    bodies = []
    for reason in ("cross_company", "other_company"):
        _pin_resolver(monkeypatch, {"tier": projects_db.TIER_REFUSE, "reason": reason})
        r = ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": "x@evil.example"})
        assert r.status_code == 403
        bodies.append(r.json()["detail"])
        assert _invite_rows(ctx.company_id) == []
    # Identical opaque body — no disclosure of which refuse reason applied.
    assert bodies[0] == bodies[1]
    # Only the creator is a member; nothing was added.
    assert {m["user_id"] for m in _project_member_rows(project["id"])} == {ctx.user_id}


# ── AC-6: the load-bearing mutation proofs ───────────────────────────────


def test_workspace_reassert_mutation_proof(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    from app.db import workspaces as workspaces_db

    foreign_uid = "foreign-uid-not-in-ws"
    _pin_resolver(monkeypatch, {"tier": projects_db.TIER_WORKSPACE, "user_id": foreign_uid})

    # Stub the re-assertion always-true → the cross-tenant add OCCURS.
    monkeypatch.setattr(workspaces_db, "get_workspace_member", lambda wid, uid: {"user_id": uid})
    r = ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": "ghost"})
    assert r.status_code == 200
    assert foreign_uid in {m["user_id"] for m in _project_member_rows(project["id"])}

    # Restore the real re-assertion (foreign uid not in workspace) → BLOCKED.
    project2 = _new_project(ctx, name="Second")
    monkeypatch.setattr(workspaces_db, "get_workspace_member", lambda wid, uid: None)
    r2 = ctx.client.post(f"/v1/projects/{project2['id']}/tag", json={"needle": "ghost"})
    assert r2.status_code == 403
    assert foreign_uid not in {m["user_id"] for m in _project_member_rows(project2["id"])}


def test_resolver_forced_workspace_route_backstop(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    # Resolver forced to t_workspace for a foreign uid; the route's own live
    # re-assertion (real get_workspace_member, no stub) is the backstop.
    _pin_resolver(monkeypatch, {"tier": projects_db.TIER_WORKSPACE, "user_id": "foreign-uid"})
    r = ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": "ghost"})
    assert r.status_code == 403
    assert "foreign-uid" not in {m["user_id"] for m in _project_member_rows(project["id"])}


def test_refuse_writes_nothing_all_reasons(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    for reason in ("cross_company", "other_company", "no_match", "ambiguous", "no_project"):
        _pin_resolver(monkeypatch, {"tier": projects_db.TIER_REFUSE, "reason": reason})
        r = ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": "x@evil.example"})
        assert r.status_code == 403, reason
        assert {m["user_id"] for m in _project_member_rows(project["id"])} == {ctx.user_id}, reason
        assert _invite_rows(ctx.company_id) == [], reason


# ── AC-7: add_member-route IDOR fix ──────────────────────────────────────


def test_add_member_route_rejects_cross_company(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)

    # Fix ON: a foreign email classifies as t_refuse → 404, no write.
    _pin_resolver(monkeypatch, {"tier": projects_db.TIER_REFUSE, "reason": "other_company"})
    r = ctx.client.post(
        f"/v1/projects/{project['id']}/members", json={"email": "foreign@evil.example"}
    )
    assert r.status_code == 404
    assert {m["user_id"] for m in _project_member_rows(project["id"])} == {ctx.user_id}

    # Fix OFF (regression proof): if resolution fails open and hands the route
    # an addable tier for a foreign uid (the pre-fix global-user_id_for_email
    # behaviour), the route adds the foreign user — the exact IDOR this fix
    # closes. Restoring the fail-closed classifier (above) blocks it.
    _pin_resolver(
        monkeypatch, {"tier": projects_db.TIER_WORKSPACE, "user_id": "foreign-uid-x"}
    )
    r2 = ctx.client.post(
        f"/v1/projects/{project['id']}/members", json={"email": "foreign@evil.example"}
    )
    assert r2.status_code == 200
    assert "foreign-uid-x" in {m["user_id"] for m in _project_member_rows(project["id"])}


def test_add_member_route_valid_in_tenant_unchanged(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    uid = _seed_workspace_member(ctx, project["workspace_id"])
    _pin_resolver(monkeypatch, {"tier": projects_db.TIER_WORKSPACE, "user_id": uid})

    r = ctx.client.post(
        f"/v1/projects/{project['id']}/members", json={"email": "peer@acme.example"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Byte-identical shape to a direct add_member (project_id + user_id row).
    assert body["project_id"] == project["id"]
    assert body["user_id"] == uid


# ── AC-8/AC-9: de-gate + seat pricing ────────────────────────────────────


def test_non_admin_member_can_tag_and_invite(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    _capture_invite_email(monkeypatch)
    # A same-tenant role='member' user, added to the project.
    member_uid, member_headers = seed_same_tenant_non_member(ctx)
    projects_db.add_member(project["id"], member_uid)

    # t_workspace add driven by the non-admin member.
    target = _seed_workspace_member(ctx, project["workspace_id"])
    _pin_resolver(monkeypatch, {"tier": projects_db.TIER_WORKSPACE, "user_id": target})
    r = ctx.client.post(
        f"/v1/projects/{project['id']}/tag", json={"needle": "x"}, headers=member_headers
    )
    assert r.status_code == 200, r.text
    assert target in {m["user_id"] for m in _project_member_rows(project["id"])}

    # t_newuser invite driven by the non-admin member — no admin/owner check.
    _pin_resolver(monkeypatch, {"tier": projects_db.TIER_NEWUSER, "email": "new@acme.example"})
    r2 = ctx.client.post(
        f"/v1/projects/{project['id']}/tag", json={"needle": "new@acme.example"},
        headers=member_headers,
    )
    assert r2.status_code == 200 and r2.json()["tier"] == "t_newuser"


def test_non_member_403_on_tag(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    _, non_member_headers = seed_same_tenant_non_member(ctx)
    _pin_resolver(monkeypatch, {"tier": projects_db.TIER_MEMBER, "member": {}})
    r = ctx.client.post(
        f"/v1/projects/{project['id']}/tag", json={"needle": "x"}, headers=non_member_headers
    )
    assert r.status_code == 403


def test_seat_limit_blocks_invite_not_workspace_add(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    _capture_invite_email(monkeypatch)
    from app.db.client import require_client

    # LIMITED-AND-FULL: seat_limit == current members (1 owner), no invites.
    require_client().table("companies").update({"seat_limit": 1}).eq(
        "id", ctx.company_id
    ).execute()
    _pin_resolver(monkeypatch, {"tier": projects_db.TIER_NEWUSER, "email": "new@acme.example"})
    r = ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": "new@acme.example"})
    assert r.status_code == 409
    assert _invite_rows(ctx.company_id) == []  # no invite row created

    # A T-workspace add consumes no seat — still succeeds at the same limit.
    target = _seed_workspace_member(ctx, project["workspace_id"])
    _pin_resolver(monkeypatch, {"tier": projects_db.TIER_WORKSPACE, "user_id": target})
    r2 = ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": "x"})
    assert r2.status_code == 200
    assert target in {m["user_id"] for m in _project_member_rows(project["id"])}

    # UNLIMITED (seat_limit NULL): the invite tier is NEVER blocked, no crash.
    require_client().table("companies").update({"seat_limit": None}).eq(
        "id", ctx.company_id
    ).execute()
    _pin_resolver(monkeypatch, {"tier": projects_db.TIER_NEWUSER, "email": "new2@acme.example"})
    r3 = ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": "new2@acme.example"})
    assert r3.status_code == 200 and r3.json()["invited"] is True


# ── AC-11/AC-12/AC-14: degrade + name-only + cost/PII ────────────────────


def test_email_failed_returns_link_fallback_not_500(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    _capture_invite_email(monkeypatch, status="failed")
    _pin_resolver(monkeypatch, {"tier": projects_db.TIER_NEWUSER, "email": "new@acme.example"})

    r = ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": "new@acme.example"})
    assert r.status_code == 200  # never 500 on a failed email
    body = r.json()
    assert body["invited"] is True and body["email_status"] == "failed"
    # The invite ROW is persisted regardless (write happens before the email).
    assert len(_invite_rows(ctx.company_id)) == 1


def test_invite_payload_carries_name_only(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    calls = _capture_invite_email(monkeypatch)
    _pin_resolver(monkeypatch, {"tier": projects_db.TIER_COMPANY, "email": "peer@acme.example"})

    ctx.client.post(f"/v1/projects/{project['id']}/tag", json={"needle": "peer@acme.example"})
    # AD-TNM2: only the project NAME reaches the email — never any content.
    assert calls[0]["project_name"] == project["name"]
    forbidden = {"messages", "brief", "members", "artifacts", "content", "memory"}
    assert not (set(calls[0]) & forbidden)
    # The invite row stores project_id (an int) + nothing else project-derived.
    inv = _invite_rows(ctx.company_id)[0]
    assert inv["project_id"] == project["id"]
    assert not (set(inv) & forbidden)


def test_no_llm_cost_line_and_no_pii_in_logs(isolated_settings, monkeypatch, caplog):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    _capture_invite_email(monkeypatch)
    _pin_resolver(monkeypatch, {"tier": projects_db.TIER_NEWUSER, "email": "newbie@acme.example"})

    with caplog.at_level(logging.INFO):
        ctx.client.post(
            f"/v1/projects/{project['id']}/tag", json={"needle": "newbie@acme.example"}
        )
    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "cost" not in text.lower()  # no LLM cost-summary line (pure CRUD)
    assert "newbie" not in text  # no full email local-part / needle text
    assert "acme.example" in text  # domain-only is logged


# ── AC-13: candidate search ──────────────────────────────────────────────


def test_candidate_search_tenant_scoped_capped(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)
    from app.db.client import require_client
    from app.db.workspaces import upsert_workspace_member

    # An in-tenant workspace non-member with a profile.
    ws_uid = "cand-ws-1"
    require_client().table("profiles").insert(
        {"id": ws_uid, "email": "wsperson@acme.example", "full_name": "Wanda Space"}
    ).execute()
    upsert_workspace_member(project["workspace_id"], ws_uid, "member")

    # A real, non-caller project member — proves exclusion is scoped to the
    # caller only, not every project member.
    peer_uid = "cand-peer-1"
    require_client().table("profiles").insert(
        {"id": peer_uid, "email": "peer@acme.example", "full_name": "Peer Person"}
    ).execute()
    projects_db.add_member(project["id"], peer_uid)

    # A FOREIGN-company member — must NEVER appear.
    require_client().table("companies").insert(
        {"id": "other-co", "slug": "other-co", "display_name": "Other Co"}
    ).execute()
    require_client().table("profiles").insert(
        {"id": "foreign-1", "email": "outsider@evil.example", "full_name": "Ivan Outside"}
    ).execute()
    require_client().table("company_members").insert(
        {"id": "cm-foreign", "company_id": "other-co", "user_id": "foreign-1", "role": "member"}
    ).execute()

    r = ctx.client.get(f"/v1/projects/{project['id']}/candidates?q=")
    assert r.status_code == 200, r.text
    cands = r.json()["candidates"]
    uids = {c["user_id"] for c in cands}
    assert ctx.user_id not in uids  # the caller is self-excluded, not listed
    assert peer_uid in uids  # a non-caller project member still appears
    assert ws_uid in uids  # in-tenant workspace non-member
    assert "foreign-1" not in uids  # cross-company never listed
    assert all(c["kind"] in ("member", "workspace", "company") for c in cands)
    assert len(cands) <= 20

    # A same-tenant non-member of the project → 403.
    _, non_member_headers = seed_same_tenant_non_member(ctx)
    r2 = ctx.client.get(
        f"/v1/projects/{project['id']}/candidates?q=", headers=non_member_headers
    )
    assert r2.status_code == 403


def test_candidate_search_excludes_caller(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _new_project(ctx)

    # The caller is the sole project member (the creator, added on project
    # creation) — the picker still must not list them, even with nobody
    # else to pick from.
    r = ctx.client.get(f"/v1/projects/{project['id']}/candidates?q=")
    assert r.status_code == 200, r.text
    cands = r.json()["candidates"]
    uids = {c["user_id"] for c in cands}
    assert ctx.user_id not in uids
    assert cands == []
