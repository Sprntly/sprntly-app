"""Real local-Supabase round-trip for `POST /v1/projects/{project_id}/prd/content`
— the project artifact drawer's full-document PRD-save path, driving the ★
cross-project IDOR gate + cross-tenant gate against REAL rows through the
REAL route (not the function directly), across TWO real tenants.

Every other backend test in this ticket substitutes `FakeSupabaseClient` for
the Supabase client. This file deliberately does not: it proves what that
deterministic backstop (`test_project_prd_content_route.py`, fast lane,
monkeypatched) cannot — that a genuine cross-project `prd_id` (same company,
sibling project) AND a genuine cross-tenant `prd_id` (a second real company)
are both refused end to end through the REAL route with ZERO writes, and that
a real in-tenant, on-project save updates `prds.payload_md` AND inserts
exactly one `prd_versions` snapshot row.

No LLM call anywhere on this route (pure CRUD), so this needs only a real
local Supabase — no `ANTHROPIC_API_KEY` dependency, unlike the sibling
`test_projects_prd_chat_edit_route_live.py`.

    RUN_PROJECT_PRD_CONTENT_LIVE=1 \\
        pytest tests/test_project_prd_content_live.py -m integration

Registered in `test_ci_lane_coverage.py::_KNOWN_UNRUNNABLE` under
`RUN_PROJECT_PRD_CONTENT_LIVE`. Skips cleanly otherwise. Restores the DB to
its pre-test state.
"""
from __future__ import annotations

import os
import time

import jwt as pyjwt
import pytest

_RUN_LIVE = os.getenv("RUN_PROJECT_PRD_CONTENT_LIVE") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _RUN_LIVE,
        reason=(
            "needs a real local Supabase — set RUN_PROJECT_PRD_CONTENT_LIVE=1 "
            "with SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY/SUPABASE_JWT_SECRET "
            "pointed at the local rig and the projects/prds/prd_versions "
            "migrations applied"
        ),
    ),
]


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        f"refusing to run the live PRD-content round-trip against a "
        f"non-loopback SUPABASE_URL ({url!r}) — this test mutates real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture(scope="module")
def sb():
    return _sb()


@pytest.fixture
def scene(sb):
    """TWO real (company, workspace, user) tuples — reuses whatever real
    tenant(s) already exist in the local rig for tenant A, and fabricates a
    real second company/workspace/member row for tenant B (mirrors
    `test_projects_crud_live.py`'s fixture_ids posture) — plus, on tenant A,
    two real projects (P1 with its own PRD attached, P2 with none) so a
    cross-project id is genuine, not simulated. Cleans up every row it
    created."""
    import uuid as _uuid

    from app.db import projects as projects_db
    from app.db.client import require_client

    c = require_client()

    members = sb.table("company_members").select("company_id, user_id").limit(50).execute().data
    company_id = workspace_id = user_id = slug = None
    for m in members:
        comp = sb.table("companies").select("slug").eq("id", m["company_id"]).limit(1).execute().data
        ws = sb.table("workspaces").select("id").eq("company_id", m["company_id"]).limit(1).execute().data
        if comp and comp[0].get("slug") and ws:
            company_id, workspace_id, user_id, slug = (
                m["company_id"], ws[0]["id"], m["user_id"], comp[0]["slug"]
            )
            break
    assert company_id, "no (company w/ slug, workspace, member) in the local rig"

    created = {"projects": [], "briefs": [], "prds": [], "companies": [], "workspaces": [], "members": []}

    def _brief(dataset, label):
        row = c.table("briefs").insert({
            "dataset": dataset, "week_label": label, "is_current": False,
            "payload": {"insights": []},
        }).execute().data[0]
        created["briefs"].append(row["id"])
        return row["id"]

    def _prd(brief_id, title):
        row = c.table("prds").insert({
            "brief_id": brief_id, "insight_index": 0, "title": title,
            "payload_md": f"<html><body><h1>{title}</h1><p>Original.</p></body></html>",
            "status": "ready",
        }).execute().data[0]
        created["prds"].append(row["id"])
        return row["id"]

    def _project(name):
        p = projects_db.create_project(
            company_id=company_id, workspace_id=workspace_id, name=name, created_by=user_id
        )
        created["projects"].append(p["id"])
        return p["id"]

    p1 = _project("prd content live P1")
    p2 = _project("prd content live P2")
    prd_p1 = _prd(_brief(slug, "prd content live P1"), "P1 PRD")
    projects_db.add_artifact(p1, "prd", prd_p1)

    # A genuine SECOND tenant — its own company/workspace/member/brief/prd, so
    # a cross-tenant id is a real foreign row, not simulated.
    b_slug = "live-b-" + _uuid.uuid4().hex[:8]
    b_company_id = _uuid.uuid4().hex
    c.table("companies").insert({"id": b_company_id, "slug": b_slug, "display_name": b_slug}).execute()
    created["companies"].append(b_company_id)
    from app.db.workspaces import ensure_default_workspace

    b_ws = ensure_default_workspace(b_company_id)
    created["workspaces"].append(b_ws["id"])
    b_user_id = "live-b-user-" + _uuid.uuid4().hex[:8]
    c.table("company_members").insert({
        "id": _uuid.uuid4().hex, "company_id": b_company_id, "user_id": b_user_id, "role": "owner",
    }).execute()
    created["members"].append((b_company_id, b_user_id))
    prd_b = _prd(_brief(b_slug, "prd content live B"), "B PRD (foreign tenant)")

    yield {
        "company_id": company_id, "workspace_id": workspace_id, "user_id": user_id,
        "p1": p1, "p2": p2, "prd_p1": prd_p1, "prd_b_foreign_tenant": prd_b,
    }

    for pid in created["projects"]:
        sb.table("project_artifacts").delete().eq("project_id", pid).execute()
        sb.table("project_members").delete().eq("project_id", pid).execute()
        sb.table("projects").delete().eq("id", pid).execute()
    for prd_id in created["prds"]:
        sb.table("prd_versions").delete().eq("prd_id", prd_id).execute()
        sb.table("prds").delete().eq("id", prd_id).execute()
    for bid in created["briefs"]:
        sb.table("briefs").delete().eq("id", bid).execute()
    for cid, uid in created["members"]:
        sb.table("company_members").delete().eq("company_id", cid).eq("user_id", uid).execute()
    for ws_id in created["workspaces"]:
        sb.table("workspaces").delete().eq("id", ws_id).execute()
    for cid in created["companies"]:
        sb.table("companies").delete().eq("id", cid).execute()


def _bearer(user_id: str) -> dict[str, str]:
    from app.config import settings

    now = int(time.time())
    token = pyjwt.encode(
        {"sub": user_id, "aud": "authenticated", "exp": now + 3600},
        settings.supabase_jwt_secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _make_client(user_id: str, workspace_id: str):
    import app.main as main_mod
    from app.config import settings
    from fastapi.testclient import TestClient

    headers = _bearer(user_id)
    headers["X-Workspace-Id"] = workspace_id
    headers["Origin"] = settings.origins_list[0]
    return TestClient(main_mod.app, headers=headers)


def _payload(sb, prd_id):
    return sb.table("prds").select("payload_md").eq("id", prd_id).execute().data[0]["payload_md"]


def _version_count(sb, prd_id):
    return len(sb.table("prd_versions").select("id").eq("prd_id", prd_id).execute().data)


def test_project_prd_content_route_two_tenant_roundtrip_live(scene, sb):
    client = _make_client(scene["user_id"], scene["workspace_id"])

    # (a) Cross-project: P1's own PRD posted against P2 (same tenant, no ref
    # on P2's manifest) — the REAL assert_prd_on_project gate must deny it,
    # zero writes.
    before_payload = _payload(sb, scene["prd_p1"])
    before_versions = _version_count(sb, scene["prd_p1"])
    resp_cross_project = client.post(
        f"/v1/projects/{scene['p2']}/prd/content",
        json={"prd_id": scene["prd_p1"], "title": "Hijacked", "html": "<html><body>hijacked</body></html>"},
    )
    assert resp_cross_project.status_code == 403, resp_cross_project.text
    assert _payload(sb, scene["prd_p1"]) == before_payload
    assert _version_count(sb, scene["prd_p1"]) == before_versions

    # (b) Cross-tenant: a real foreign-tenant prd_id posted against P1 — the
    # REAL require_owned_prd gate must deny it (assert_prd_on_project would
    # also refuse it first — the foreign PRD is never on P1's own tenant-
    # scoped fan-out — so this proves the end-to-end refuse either way),
    # zero writes on the foreign PRD.
    foreign_before_payload = _payload(sb, scene["prd_b_foreign_tenant"])
    foreign_before_versions = _version_count(sb, scene["prd_b_foreign_tenant"])
    resp_cross_tenant = client.post(
        f"/v1/projects/{scene['p1']}/prd/content",
        json={
            "prd_id": scene["prd_b_foreign_tenant"], "title": "Stolen",
            "html": "<html><body>stolen</body></html>",
        },
    )
    assert resp_cross_tenant.status_code in (403, 404), resp_cross_tenant.text
    assert _payload(sb, scene["prd_b_foreign_tenant"]) == foreign_before_payload
    assert _version_count(sb, scene["prd_b_foreign_tenant"]) == foreign_before_versions

    # (c) A real in-tenant, on-project save through the REAL route: updates
    # prds.payload_md AND inserts exactly one prd_versions snapshot row.
    versions_before_valid = _version_count(sb, scene["prd_p1"])
    resp_valid = client.post(
        f"/v1/projects/{scene['p1']}/prd/content",
        json={
            "prd_id": scene["prd_p1"], "title": "P1 PRD (revised)",
            "html": "<html><body><h1>P1 PRD (revised)</h1></body></html>",
        },
    )
    assert resp_valid.status_code == 200, resp_valid.text
    assert _payload(sb, scene["prd_p1"]) == "<html><body><h1>P1 PRD (revised)</h1></body></html>"
    assert _version_count(sb, scene["prd_p1"]) == versions_before_valid + 1
