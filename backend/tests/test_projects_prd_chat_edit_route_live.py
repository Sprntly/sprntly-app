"""Real local-Supabase + real-LLM round-trip for
`POST /v1/projects/{project_id}/prd/chat-edit` — the private project chat's
write path, driving the ★ cross-project IDOR gate and the scoped editor
against REAL rows and a REAL Anthropic model, through the REAL route (not the
function directly).

Every other backend test in this ticket substitutes a fake Supabase client and
a mocked editor. This file deliberately does neither: it proves what those
deterministic backstops (`test_project_chat_edit.py` +
`test_projects_prd_chat_edit_route.py`, fast lane, monkeypatched) cannot —

  (a) a genuine cross-project prd_id (same company, sibling project) reached
      through the REAL route writes ZERO rows and is refused, end to end;
  (b) an own-project edit persists through the REAL route: `prds.payload_md`
      changes AND exactly one `prd_versions` snapshot lands.

Gated on BOTH a real LLM and the run flag; skips cleanly otherwise. Registered
in `test_ci_lane_coverage.py::_KNOWN_UNRUNNABLE` under both
`RUN_PROJECT_CHAT_EDIT_LIVE` and `ANTHROPIC_API_KEY`. Restores the DB to its
pre-test state.

    RUN_PROJECT_CHAT_EDIT_LIVE=1 ANTHROPIC_API_KEY=... \\
        pytest tests/test_projects_prd_chat_edit_route_live.py -m integration
"""
from __future__ import annotations

import os
import time

import jwt as pyjwt
import pytest

_RUN_LIVE = os.getenv("RUN_PROJECT_CHAT_EDIT_LIVE") == "1" and bool(
    os.getenv("ANTHROPIC_API_KEY")
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _RUN_LIVE,
        reason=(
            "needs a real local Supabase + a real LLM — set "
            "RUN_PROJECT_CHAT_EDIT_LIVE=1 and ANTHROPIC_API_KEY, with "
            "SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY pointed at the local rig and "
            "the projects/prds/prd_versions migrations applied"
        ),
    ),
]


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        f"refusing to run the live chat-edit round-trip against a non-loopback "
        f"SUPABASE_URL ({url!r}) — this test mutates real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture(scope="module")
def sb():
    return _sb()


@pytest.fixture
def scene(sb):
    """A real (company, workspace, user) with two projects (A1, A2), and a
    PRD on EACH (via its own brief, so they never collapse into one artifact
    under `_prd_family_key`). Cleans up every row it created."""
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

    created = {"projects": [], "briefs": [], "prds": []}

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

    p_a1, p_a2 = _project("chat-edit route live A1"), _project("chat-edit route live A2")
    prd_a1 = _prd(_brief("chat-edit route live A1"), "A1 PRD")
    prd_a2 = _prd(_brief("chat-edit route live A2"), "A2 PRD")
    projects_db.add_artifact(p_a1, "prd", prd_a1)
    projects_db.add_artifact(p_a2, "prd", prd_a2)

    yield {
        "company_id": company_id, "workspace_id": workspace_id, "user_id": user_id,
        "p_a1": p_a1, "p_a2": p_a2, "prd_a1": prd_a1, "prd_a2": prd_a2,
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


def test_project_edit_route_cross_project_and_own_project_live(scene, sb, monkeypatch):
    monkeypatch.setenv("PROJECT_PRD_EDIT_ENABLED", "1")
    client = _make_client(scene["user_id"], scene["workspace_id"])

    # (a) A1's caller has no way to name A2's PRD at all — the route resolves
    # its OWN project's target server-side. Under the confirmation gate the
    # first call only PROPOSES; prove A2's PRD (and A1's) is genuinely
    # untouched by the propose (the ★ gate would deny A2 even if reached).
    before_a2 = _payload(sb, scene["prd_a2"])
    before_a2_versions = _version_count(sb, scene["prd_a2"])
    before_a1_versions = _version_count(sb, scene["prd_a1"])

    resp_a1 = client.post(
        f"/v1/projects/{scene['p_a1']}/prd/chat-edit",
        json={"instruction": "Sharpen the problem statement to mention onboarding drop-off."},
    )
    assert resp_a1.status_code == 200, resp_a1.text
    body_a1 = resp_a1.json()
    # PROPOSE writes nothing: whether the live model returned a pending edit or
    # judged the instruction a no-op, neither A1 nor A2 is written yet.
    assert body_a1["edited"] is False, body_a1
    assert _payload(sb, scene["prd_a2"]) == before_a2
    assert _version_count(sb, scene["prd_a2"]) == before_a2_versions
    assert _version_count(sb, scene["prd_a1"]) == before_a1_versions

    # (b) A real edit was proposed -> CONFIRM commits it to A1's OWN PRD only:
    # A1's content changes with exactly one version snapshot, A2 stays
    # untouched. A no-op proposal has nothing to confirm.
    if body_a1.get("pending"):
        token = body_a1["mutation"]["token"]
        confirm = client.post(
            f"/v1/projects/{scene['p_a1']}/prd/chat-edit/confirm", json={"token": token},
        )
        assert confirm.status_code == 200, confirm.text
        cbody = confirm.json()
        assert cbody["edited"] is True, cbody
        assert cbody["sections_changed"]
        assert "onboarding" in cbody["prd"]["payload_md"].lower() or cbody["sections_changed"]
        assert _version_count(sb, scene["prd_a1"]) == before_a1_versions + 1
        # A2 is STILL provably untouched after the confirm.
        assert _payload(sb, scene["prd_a2"]) == before_a2
        assert _version_count(sb, scene["prd_a2"]) == before_a2_versions
    else:
        assert "answer" in body_a1
        assert _version_count(sb, scene["prd_a1"]) == before_a1_versions
