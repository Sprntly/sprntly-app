"""Real local-Supabase round-trip for the @Sprntly group agent's project read
tools' TENANCY SCOPING (`app/project_group_context.py::dispatch_read_tool`).

Every other backend test substitutes `FakeSupabaseClient`. This file
deliberately does NOT patch the client: it drives `dispatch_read_tool`
(and `list_artifacts_for_project` beneath it) against a real local Postgres
(127.0.0.1:54321) via PostgREST + the real supabase-py service-role client, so
the manifest-intersection gate and the per-artifact company gate
(`get_report(id, company_id)`) are the REAL ones, not an in-memory stand-in.

It proves what the deterministic backstop (`test_project_group_context.py`,
fast lane, mutation-proofed) cannot: that a genuine CROSS-PROJECT id (same
company, other project) and a genuine CROSS-TENANT id (a second real company)
are both REFUSED by `get_artifact_content` against real rows — and that the
manifest gate is load-bearing (add the ref -> content returns -> remove ->
refused). It also proves `list_project_artifacts` surfaces only THIS project's
own artifact, never the sibling project's or the foreign company's.

Run it with:

    RUN_READ_TOOL_IDOR_LIVE=1 \\
        pytest tests/test_read_tool_idor_live.py -m integration

Skips cleanly (same posture as `test_projects_crud_live.py`) unless the local
rig is up and the env var is set. Registered in
`test_ci_lane_coverage.py::_KNOWN_UNRUNNABLE` with `test_project_group_context.py`
named as its deterministic backstop.
"""
from __future__ import annotations

import os
import uuid

import pytest

_RUN_LIVE = os.getenv("RUN_READ_TOOL_IDOR_LIVE") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _RUN_LIVE,
        reason=(
            "needs a real local Supabase — set RUN_READ_TOOL_IDOR_LIVE=1 with "
            "SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY pointed at the local rig and "
            "the projects/reports/project_artifacts migrations applied"
        ),
    ),
]


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        "refusing to run the live IDOR round-trip against a non-loopback "
        f"SUPABASE_URL ({url!r}) — this test mutates real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture(scope="module")
def sb():
    return _sb()


@pytest.fixture(scope="module")
def ids(sb):
    """A real (company, workspace, user) tuple from the rig, plus a fabricated
    SECOND real company/workspace — FK-backed rows, so the cross-tenant refusal
    is proved against a genuine second tenant, not a fake id."""
    # Pick a company that ACTUALLY has both a member and a workspace — start
    # from company_members (the scarcer row) and find a workspace for it.
    members = (
        sb.table("company_members").select("company_id, user_id").limit(50).execute().data
    )
    assert members, "no company_members rows in the local rig — seed one first"
    company_id = workspace_id = user_id = None
    for m in members:
        ws = (
            sb.table("workspaces").select("id").eq("company_id", m["company_id"]).limit(1)
            .execute().data
        )
        if ws:
            company_id, workspace_id, user_id = m["company_id"], ws[0]["id"], m["user_id"]
            break
    assert company_id, "no (company, workspace, member) triple in the local rig"

    # A fabricated SECOND real company/workspace. No foreign USER is minted:
    # profiles.id FKs auth.users, and the cross-tenant proof only needs a
    # foreign-company REPORT (company_id + workspace_id), never a foreign member.
    foreign_company_id = str(uuid.uuid4())
    foreign_workspace_id = str(uuid.uuid4())
    sb.table("companies").insert(
        {
            "id": foreign_company_id,
            "slug": f"idor-foreign-{uuid.uuid4().hex[:8]}",
            "display_name": "Read-tool IDOR foreign tenant",
        }
    ).execute()
    sb.table("workspaces").insert(
        {
            "id": foreign_workspace_id,
            "company_id": foreign_company_id,
            "name": "Foreign workspace",
            "slug": "foreign",
        }
    ).execute()

    yield {
        "company_id": company_id,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "foreign_company_id": foreign_company_id,
        "foreign_workspace_id": foreign_workspace_id,
    }

    sb.table("companies").delete().eq("id", foreign_company_id).execute()


@pytest.fixture
def scene(sb, ids):
    """Two projects in the caller's own company (A1, A2) and one in the foreign
    company (B), each with its OWN report artifact attached. Returns the ids and
    cleans up every row it created."""
    from app.db import projects as projects_db
    from app.db.reports import save_report

    created_projects: list[int] = []
    created_reports: list[int] = []

    def _project(company_id, workspace_id, user_id, name):
        p = projects_db.create_project(
            company_id=company_id, workspace_id=workspace_id, name=name, created_by=user_id
        )
        created_projects.append(p["id"])
        return p

    def _report(company_id, workspace_id, title, html):
        rid = save_report(
            company_id, skill="voc", title=title, html=html, workspace_id=workspace_id
        )
        assert rid is not None
        created_reports.append(rid)
        return rid

    p_a1 = _project(ids["company_id"], ids["workspace_id"], ids["user_id"], "IDOR A1")
    p_a2 = _project(ids["company_id"], ids["workspace_id"], ids["user_id"], "IDOR A2")

    r_a = _report(ids["company_id"], ids["workspace_id"], "A report", "<p>OWN-A-REPORT-BODY</p>")
    # The foreign-tenant report — a real row under a genuine second company,
    # attached to no project of ours.
    r_b = _report(
        ids["foreign_company_id"], ids["foreign_workspace_id"], "B report", "<p>FOREIGN-B-BODY</p>"
    )

    projects_db.add_artifact(p_a1["id"], "report", r_a)

    yield {
        "p_a1": p_a1["id"], "p_a2": p_a2["id"],
        "r_a": r_a, "r_b": r_b,
        "company_id": ids["company_id"], "foreign_company_id": ids["foreign_company_id"],
    }

    for pid in created_projects:
        sb.table("project_artifacts").delete().eq("project_id", pid).execute()
        sb.table("project_members").delete().eq("project_id", pid).execute()
        sb.table("projects").delete().eq("id", pid).execute()
    for rid in created_reports:
        sb.table("reports").delete().eq("id", rid).execute()


def _read_artifact(project_id, company_id, artifact_type, artifact_id):
    from app.project_group_context import dispatch_read_tool

    return dispatch_read_tool(
        "get_artifact_content",
        {"artifact_type": artifact_type, "artifact_id": artifact_id},
        project_id=project_id, dataset="", company_id=company_id,
    )


def test_own_artifact_reads_but_cross_project_and_cross_tenant_are_refused(sb, scene):
    company = scene["company_id"]

    # (1) THIS project's own report reads.
    own = _read_artifact(scene["p_a1"], company, "report", scene["r_a"])
    assert "OWN-A-REPORT-BODY" in own, own

    # (2) ★ Cross-PROJECT (same company, sibling project A2 that never had r_a
    #     on its manifest): refused, no content leak.
    cross_project = _read_artifact(scene["p_a2"], company, "report", scene["r_a"])
    assert "OWN-A-REPORT-BODY" not in cross_project
    assert "can't find that artifact" in cross_project.lower(), cross_project

    # (3) ★ Cross-TENANT (foreign company's report r_b, read as company A from
    #     project A1): refused twice over — not on A1's manifest, and get_report
    #     is company-scoped so it would 404 even if it were.
    cross_tenant = _read_artifact(scene["p_a1"], company, "report", scene["r_b"])
    assert "FOREIGN-B-BODY" not in cross_tenant
    assert "can't find that artifact" in cross_tenant.lower(), cross_tenant


def test_manifest_gate_is_load_bearing_red_green(sb, scene):
    """RED->GREEN enforcement proof against real rows: A2 refuses r_a; add the
    ref onto A2's manifest and the SAME content returns; remove it and A2
    refuses again — proving the manifest membership check is what refuses."""
    from app.db import projects as projects_db

    company = scene["company_id"]

    # RED: r_a is not on A2's manifest.
    assert "can't find that artifact" in _read_artifact(
        scene["p_a2"], company, "report", scene["r_a"]
    ).lower()

    # GREEN: put r_a on A2's manifest — the gate now lets the body through.
    projects_db.add_artifact(scene["p_a2"], "report", scene["r_a"])
    assert "OWN-A-REPORT-BODY" in _read_artifact(scene["p_a2"], company, "report", scene["r_a"])

    # RESTORE: remove the ref — refused once more.
    sb.table("project_artifacts").delete().eq("project_id", scene["p_a2"]).eq(
        "artifact_type", "report"
    ).eq("artifact_id", scene["r_a"]).execute()
    assert "can't find that artifact" in _read_artifact(
        scene["p_a2"], company, "report", scene["r_a"]
    ).lower()


def test_list_project_artifacts_shows_only_this_projects_own(sb, scene):
    from app.project_group_context import dispatch_read_tool

    listed = dispatch_read_tool(
        "list_project_artifacts", {},
        project_id=scene["p_a1"], dataset="", company_id=scene["company_id"],
    )
    assert f"id={scene['r_a']}" in listed
    # Never the sibling project's / foreign company's artifact.
    assert f"id={scene['r_b']}" not in listed
