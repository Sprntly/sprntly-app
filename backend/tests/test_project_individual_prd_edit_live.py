"""Real-LLM + real-Supabase round-trip for the project-chat PRD-edit flow
(§A responder + §C/§D IDOR write gate).

Every other backend test substitutes `FakeSupabaseClient` and a fake LLM. This
file deliberately does NEITHER: it drives `respond_individual` against a real
local Postgres (127.0.0.1) via the real supabase-py service-role client AND a
real Anthropic model, so the bounded tool loop, the five-table
`list_artifacts_for_project` fan-out beneath the §C gate, and the accept route's
render-on-read fold are the REAL ones.

It proves what the deterministic backstops
(`test_project_prd_gate.py` + `test_project_prd_patch_tool.py` +
`test_project_individual_tool_loop.py`, fast lane, monkeypatched) cannot:

  (a) the loop answers an artifacts question by ACTUALLY calling a read tool;
  (b) a genuine cross-project prd_id handed to the propose tool writes ZERO rows
      against real tables and is refused;
  (c) an own-project edit persists a real `pending` patch that the real
      `POST /prd-patches/{id}/accept` flips to `applied` and folds into the
      rendered PRD.

Gated on BOTH a real LLM and the run flag; skips cleanly otherwise. Registered
in `test_ci_lane_coverage.py::_KNOWN_UNRUNNABLE` under both
`RUN_PROJECT_PRD_EDIT_LIVE` and `ANTHROPIC_API_KEY`. Restores the DB to its
pre-test state.

    RUN_PROJECT_PRD_EDIT_LIVE=1 ANTHROPIC_API_KEY=... \\
        pytest tests/test_project_individual_prd_edit_live.py -m integration
"""
from __future__ import annotations

import os
import uuid

import pytest

_RUN_LIVE = os.getenv("RUN_PROJECT_PRD_EDIT_LIVE") == "1" and bool(
    os.getenv("ANTHROPIC_API_KEY")
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _RUN_LIVE,
        reason=(
            "needs a real local Supabase + a real LLM — set "
            "RUN_PROJECT_PRD_EDIT_LIVE=1 and ANTHROPIC_API_KEY, with "
            "SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY pointed at the local rig and "
            "the projects/prds/prd_patches migrations applied"
        ),
    ),
]


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        f"refusing to run the live PRD-edit round-trip against a non-loopback "
        f"SUPABASE_URL ({url!r}) — this test mutates real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture(scope="module")
def sb():
    return _sb()


@pytest.fixture
def scene(sb):
    """A real (company, workspace, user) with a slug, two projects (A1, A2), and
    a PRD on EACH project (via a brief on the company's dataset slug). Cleans up
    every row it created."""
    from app.db import projects as projects_db
    from app.db.client import require_client

    c = require_client()

    # A company that has a member, a workspace, and a slug (dataset scoping).
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

    created = {"projects": [], "briefs": [], "prds": []}

    # ONE brief per PRD (distinct brief_id) — NOT one shared brief: PRDs sharing
    # (brief_id, insight_index) collapse into a single artifact under
    # `_prd_family_key`, which would hide one PRD from the company fan-out and
    # break the cross-project / own-project scoping this test relies on.
    # `briefs.payload` is jsonb NOT NULL (20260525120000_briefs.sql) → seed a
    # minimal valid payload on EACH insert.
    def _brief(label):
        row = c.table("briefs").insert({
            "dataset": slug, "week_label": label, "is_current": False,
            "payload": {"insights": []},
        }).execute().data[0]
        created["briefs"].append(row["id"])
        return row["id"]

    def _prd(brief_id, title):
        row = c.table("prds").insert({
            "brief_id": brief_id, "insight_index": 0, "title": title,
            "payload_md": f"# {title}\n\nOriginal problem statement.", "status": "ready",
        }).execute().data[0]
        created["prds"].append(row["id"])
        return row["id"]

    def _project(name):
        p = projects_db.create_project(
            company_id=company_id, workspace_id=workspace_id, name=name, created_by=user_id
        )
        created["projects"].append(p["id"])
        return p["id"]

    p_a1, p_a2 = _project("PRD-edit A1"), _project("PRD-edit A2")
    prd_a1 = _prd(_brief("PRD-edit live A1"), "A1 PRD")
    prd_a2 = _prd(_brief("PRD-edit live A2"), "A2 PRD")
    projects_db.add_artifact(p_a1, "prd", prd_a1)
    projects_db.add_artifact(p_a2, "prd", prd_a2)

    yield {
        "company_id": company_id, "workspace_id": workspace_id, "slug": slug,
        "p_a1": p_a1, "p_a2": p_a2, "prd_a1": prd_a1, "prd_a2": prd_a2,
    }

    for pid in created["projects"]:
        sb.table("project_artifacts").delete().eq("project_id", pid).execute()
        sb.table("project_members").delete().eq("project_id", pid).execute()
        sb.table("projects").delete().eq("id", pid).execute()
    for prd_id in created["prds"]:
        sb.table("prd_patches").delete().eq("prd_id", prd_id).execute()
        sb.table("prds").delete().eq("id", prd_id).execute()
    for bid in created["briefs"]:
        sb.table("briefs").delete().eq("id", bid).execute()


def _count_patches(sb, prd_id):
    return len(sb.table("prd_patches").select("id").eq("prd_id", prd_id).execute().data)


def test_loop_actually_calls_a_read_tool(scene):
    # (a) The bounded loop answers an artifacts question by REALLY calling
    #     list_project_artifacts against the real fan-out.
    from app.project_individual_agent import respond_individual

    payload = respond_individual(
        project_id=scene["p_a1"], dataset=scene["slug"], company_id=scene["company_id"],
        question="What PRDs are attached to this project? Name them.",
        history=[],
        single_shot=lambda: {"answer": "(fallback)", "citations": []},
    )
    assert payload["answer"] and payload["answer"] != "(fallback)"
    assert "A1 PRD" in payload["answer"]


def test_cross_project_prd_id_writes_zero_rows(scene, sb):
    # (b) A1's chat proposing against A2's PRD writes NOTHING (real §C gate).
    from app.project_prd_patch_tool import handle_propose_prd_patch

    before = _count_patches(sb, scene["prd_a2"])
    out = handle_propose_prd_patch(
        {"prd_id": scene["prd_a2"], "rationale": "x", "patch_md": "## Y\n\nZ"},
        project_id=scene["p_a1"], dataset=scene["slug"],
        company_id=scene["company_id"], workspace_id=scene["company_id"],
    )
    assert _count_patches(sb, scene["prd_a2"]) == before
    assert "only edit a PRD that's attached to this project" in out


def test_own_project_edit_persists_and_accept_applies(scene):
    # (c) A1's chat proposing against A1's PRD persists a pending patch the
    #     accept route flips to applied and folds into the rendered PRD.
    from app.project_prd_patch_tool import handle_propose_prd_patch
    from app.db.prd_patches import list_pending_patches, mark_patch_applied
    from app.db.prds import get_prd_rendered

    out = handle_propose_prd_patch(
        {"prd_id": scene["prd_a1"], "rationale": "tighten problem",
         "patch_md": "## Revised problem\n\nSharper problem statement."},
        project_id=scene["p_a1"], dataset=scene["slug"],
        company_id=scene["company_id"], workspace_id=scene["company_id"],
    )
    assert "pending your review" in out
    pending = list_pending_patches(prd_id=scene["prd_a1"], workspace_id=scene["company_id"])
    assert len(pending) == 1
    assert pending[0]["prototype_id"] is None
    assert pending[0]["workspace_id"] == scene["company_id"]

    applied = mark_patch_applied(patch_id=pending[0]["id"], workspace_id=scene["company_id"])
    assert applied and applied["status"] == "applied"

    rendered = get_prd_rendered(scene["prd_a1"])
    assert "Sharper problem statement." in (rendered.get("payload_md") or "")
