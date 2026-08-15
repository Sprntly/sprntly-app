"""`POST /v1/projects/{project_id}/documents` — upload a document (pdf/docx/
pptx/xlsx/txt/md), extract its text, and attach it to the project as a
`custom_artifact` (the existing "team documents" library).

The fake in-memory Supabase this suite composes on (`isolated_settings`)
replicates the PRE-migration `project_artifacts.artifact_type` CHECK
(`prd|evidence|prototype|report|ticket_set` — see conftest.py's schema),
same as production before this ticket's migration. A real
`add_artifact(project_id, "custom_artifact", id)` write against that
constraint raises — which is exactly the bug this ticket's migration fixes.
So every test below monkeypatches `projects_db.add_artifact` to a recording
stub and asserts on the CALL SHAPE (orchestration order/args), never a real
row landing through the (deliberately unwidened, here) fake schema — the
real-DB round-trip proving the migration itself is the ship-gate's job
(Evidence: Behaviour, TICKET_STANDARD_ADDENDUM.md §2).

`create_artifact` (writes into `custom_artifacts`, a table with no such
constraint) runs for REAL against the fake DB, so AC2's "a custom_artifacts
row exists" is proven by an actual read, not a mock assertion.
"""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

import app.routes.projects as projects_route
from app.db.client import require_client
from app.db.custom_artifacts import BodyTooLarge
from tests._company_helpers import company_client, seed_company, supabase_bearer
from tests._project_helpers import seed_same_tenant_non_member

# ── Live (ship-gate, real LLM — authored, deferred execution) — AC14 ────────
_RUN_LIVE_LLM = os.getenv("RUN_LIVE_LLM") == "1" and bool(os.getenv("ANTHROPIC_API_KEY"))
_LIVE_LLM_SKIP_REASON = (
    "needs a real local Supabase + a real ANTHROPIC_API_KEY — set "
    "RUN_LIVE_LLM=1 with SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY/"
    "SUPABASE_JWT_SECRET/ANTHROPIC_API_KEY pointed at the local rig "
    "(ship-gate verifier's job, not the builder's)"
)


def _create_project(ctx, *, name: str = "Docs project") -> dict:
    r = ctx.client.post("/v1/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def _stub_add_artifact(monkeypatch):
    """Replace `projects_db.add_artifact` with a recording stub — see the
    module docstring for why the real one isn't hit here."""
    calls: list[tuple] = []

    def fake(project_id, artifact_type, artifact_id):
        calls.append((project_id, artifact_type, artifact_id))
        return {"project_id": project_id, "artifact_type": artifact_type, "artifact_id": artifact_id}

    monkeypatch.setattr(projects_route.projects_db, "add_artifact", fake)
    return calls


def _get_custom_artifact(artifact_id: int) -> dict | None:
    rows = require_client().table("custom_artifacts").select("*").eq("id", artifact_id).execute().data
    return rows[0] if rows else None


def _custom_artifact_count() -> int:
    return len(require_client().table("custom_artifacts").select("id").execute().data or [])


# ── Creation / happy-path (AC1-3,5) ─────────────────────────────────────────


def test_upload_document_creates_custom_artifact_and_attaches(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    add_calls = _stub_add_artifact(monkeypatch)

    resp = ctx.client.post(
        f"/v1/projects/{project['id']}/documents",
        files={"file": ("notes.txt", b"the Q3 pricing is $49/mo", "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["type"] == "custom_artifact"
    assert isinstance(body["id"], int)
    assert body["open"]["custom_artifact_id"] == body["id"]

    row = _get_custom_artifact(body["id"])
    assert row is not None
    assert row["kind"] == "document"
    assert row["title"] == "notes.txt"
    assert "the Q3 pricing is $49/mo" in row["body_html"]
    assert row["company_id"] == ctx.company_id

    assert add_calls == [(project["id"], "custom_artifact", body["id"])]


def test_upload_returns_fanout_shaped_item(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    _stub_add_artifact(monkeypatch)

    resp = ctx.client.post(
        f"/v1/projects/{project['id']}/documents",
        files={"file": ("plan.txt", b"launch plan body", "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) == {
        "type", "id", "title", "status", "kind", "created_at", "updated_at",
        "born_at", "source", "open",
    }
    assert body["title"] == "plan.txt"
    assert body["kind"] == "document"


def test_upload_stamps_workspace_from_ctx_not_default(isolated_settings, monkeypatch):
    """Two DIFFERENT ctxs (each its own company, so a distinct `aud`/default
    workspace) stamp DIFFERENT `workspace_id`s onto the created row — never a
    hardcoded/baked value (AC2). Reuses ONE TestClient/app instance — the
    header override on each call is what selects which company's identity a
    request carries (the convention `test_by_conversation_...`'s
    cross-tenant tests already use), so no second `company_client()` reload
    is needed."""
    ctx = company_client(monkeypatch)
    project1 = _create_project(ctx)
    _stub_add_artifact(monkeypatch)

    resp1 = ctx.client.post(
        f"/v1/projects/{project1['id']}/documents",
        files={"file": ("a.txt", b"body a", "text/plain")},
    )
    assert resp1.status_code == 200, resp1.text
    row1 = _get_custom_artifact(resp1.json()["id"])

    other_user = "other-owner-" + uuid.uuid4().hex[:8]
    seed_company(user_id=other_user, slug="rival-" + uuid.uuid4().hex[:8])
    other_headers = supabase_bearer(other_user)
    project2 = ctx.client.post("/v1/projects", json={"name": "Rival project"}, headers=other_headers).json()
    resp2 = ctx.client.post(
        f"/v1/projects/{project2['id']}/documents",
        files={"file": ("b.txt", b"body b", "text/plain")},
        headers=other_headers,
    )
    assert resp2.status_code == 200, resp2.text
    row2 = _get_custom_artifact(resp2.json()["id"])

    assert row1["workspace_id"] is not None
    assert row2["workspace_id"] is not None
    assert row1["workspace_id"] != row2["workspace_id"]


# ── Orchestration order / atomicity ─────────────────────────────────────────


def test_upload_converts_before_persisting(isolated_settings, monkeypatch):
    """`convert`'s output is what lands in `body_html` — the real
    passthrough converter for `.txt` proves this without mocking it."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    _stub_add_artifact(monkeypatch)

    resp = ctx.client.post(
        f"/v1/projects/{project['id']}/documents",
        files={"file": ("notes.txt", b"a distinctive fact: the sky is teal", "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    row = _get_custom_artifact(resp.json()["id"])
    assert "a distinctive fact: the sky is teal" in row["body_html"]


def test_upload_no_rows_on_convert_empty(isolated_settings, monkeypatch):
    """`convert` yielding no text (e.g. an unreadable/scanned doc) 422s
    BEFORE any row is written — no orphan `custom_artifacts` row, no
    `add_artifact` call."""
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    add_calls = _stub_add_artifact(monkeypatch)
    monkeypatch.setattr(projects_route, "convert", lambda filename, data: "   ")

    before = _custom_artifact_count()
    resp = ctx.client.post(
        f"/v1/projects/{project['id']}/documents",
        files={"file": ("scan.pdf", b"%PDF-fake-bytes", "application/pdf")},
    )
    assert resp.status_code == 422, resp.text
    assert "export to PDF or .pptx" in resp.json()["detail"]
    assert _custom_artifact_count() == before
    assert add_calls == []


# ── Error handling / validation ─────────────────────────────────────────────


def test_upload_empty_file_400_no_rows(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    add_calls = _stub_add_artifact(monkeypatch)

    resp = ctx.client.post(
        f"/v1/projects/{project['id']}/documents",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert resp.status_code == 400, resp.text
    assert add_calls == []


def test_upload_oversize_413_no_rows(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    add_calls = _stub_add_artifact(monkeypatch)
    monkeypatch.setattr(projects_route, "_MAX_DOC_BYTES", 10)

    resp = ctx.client.post(
        f"/v1/projects/{project['id']}/documents",
        files={"file": ("big.txt", b"x" * 11, "text/plain")},
    )
    assert resp.status_code == 413, resp.text
    assert add_calls == []


def test_upload_scanned_pdf_422_message(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    add_calls = _stub_add_artifact(monkeypatch)
    monkeypatch.setattr(projects_route, "convert", lambda filename, data: "")

    resp = ctx.client.post(
        f"/v1/projects/{project['id']}/documents",
        files={"file": ("scan.pdf", b"%PDF-fake-bytes", "application/pdf")},
    )
    assert resp.status_code == 422, resp.text
    assert "Scanned/image-only PDFs" in resp.json()["detail"]
    assert add_calls == []


def test_upload_body_too_large_maps_to_413(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    add_calls = _stub_add_artifact(monkeypatch)

    def _boom(*a, **kw):
        raise BodyTooLarge("body is 400001 chars (max 400000)")

    monkeypatch.setattr(projects_route, "create_artifact", _boom)

    resp = ctx.client.post(
        f"/v1/projects/{project['id']}/documents",
        files={"file": ("huge.txt", b"x" * 100, "text/plain")},
    )
    assert resp.status_code == 413, resp.text
    assert add_calls == []


# ── Tenancy / IDOR (mutation-proofed at ship gate) ──────────────────────────


def test_upload_non_member_same_tenant_403(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    add_calls = _stub_add_artifact(monkeypatch)
    _, headers = seed_same_tenant_non_member(SimpleNamespace(company_id=ctx.company_id))

    resp = ctx.client.post(
        f"/v1/projects/{project['id']}/documents",
        files={"file": ("notes.txt", b"body", "text/plain")},
        headers=headers,
    )
    assert resp.status_code == 403, resp.text
    assert add_calls == []


def test_upload_foreign_tenant_404(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    add_calls = _stub_add_artifact(monkeypatch)

    outsider = "outsider-" + uuid.uuid4().hex[:8]
    seed_company(user_id=outsider, slug="rival-" + uuid.uuid4().hex[:8])
    outsider_headers = supabase_bearer(outsider)

    resp = ctx.client.post(
        f"/v1/projects/{project['id']}/documents",
        files={"file": ("notes.txt", b"body", "text/plain")},
        headers=outsider_headers,
    )
    assert resp.status_code == 404, resp.text
    assert add_calls == []


# ── Migration idempotency — structural unit (real round-trip is ship-gate) ──


def test_migration_widens_check_and_is_idempotent():
    import pathlib
    import re

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    path = repo_root / "supabase" / "migrations" / "20260815170000_project_artifacts_custom_artifact.sql"
    assert path.is_file(), f"migration file missing: {path}"
    sql = path.read_text()

    assert re.search(
        r"drop\s+constraint\s+if\s+exists\s+project_artifacts_artifact_type_check",
        sql, re.IGNORECASE,
    ), "migration must drop the existing constraint IF EXISTS before re-adding it"
    assert re.search(
        r"add\s+constraint\s+project_artifacts_artifact_type_check", sql, re.IGNORECASE
    ), "migration must re-add the named constraint"
    assert "custom_artifact" in sql
    for kind in ("prd", "evidence", "prototype", "report", "ticket_set"):
        assert f"'{kind}'" in sql, f"migration must still admit '{kind}'"


# ── Live (ship-gate, real LLM — authored, deferred execution) — AC14 ────────


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        "refusing to run the live document-read round-trip against a "
        f"non-loopback SUPABASE_URL ({url!r}) — this test mutates real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture(scope="module")
def _live_llm_fixture_ids():
    if not _RUN_LIVE_LLM:
        pytest.skip("live-LLM tier disabled")
    sb = _sb()
    companies = sb.table("companies").select("id").limit(1).execute().data
    assert companies, "no company row in the local rig"
    company_id = companies[0]["id"]
    workspaces = (
        sb.table("workspaces").select("id").eq("company_id", company_id).limit(1).execute().data
    )
    assert workspaces, f"no workspace for company {company_id}"
    members = sb.table("company_members").select("user_id").eq("company_id", company_id).limit(1).execute().data
    assert members, f"no company_members row for company {company_id}"
    return {
        "company_id": company_id,
        "workspace_id": workspaces[0]["id"],
        "user_id": members[0]["user_id"],
    }


def _live_bearer(user_id: str) -> dict[str, str]:
    import time

    import jwt as pyjwt

    from app.config import settings

    now = int(time.time())
    token = pyjwt.encode(
        {"sub": user_id, "aud": "authenticated", "exp": now + 3600},
        settings.supabase_jwt_secret,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def _live_llm_client(_live_llm_fixture_ids):
    import app.main as main_mod
    from app.config import settings
    from fastapi.testclient import TestClient

    headers = _live_bearer(_live_llm_fixture_ids["user_id"])
    headers["X-Workspace-Id"] = _live_llm_fixture_ids["workspace_id"]
    headers["Origin"] = settings.origins_list[0]
    return TestClient(main_mod.app, headers=headers)


@pytest.fixture
def _live_llm_project(_live_llm_fixture_ids):
    sb = _sb()
    row = (
        sb.table("projects")
        .insert(
            {
                "company_id": _live_llm_fixture_ids["company_id"],
                "workspace_id": _live_llm_fixture_ids["workspace_id"],
                "name": f"doc-upload-live-{uuid.uuid4().hex[:8]}",
                "created_by": _live_llm_fixture_ids["user_id"],
            }
        )
        .execute()
        .data[0]
    )
    sb.table("project_members").insert(
        {"project_id": row["id"], "user_id": _live_llm_fixture_ids["user_id"]}
    ).execute()
    yield row
    sb.table("custom_artifacts").delete().eq("workspace_id", row["workspace_id"]).eq(
        "conversation_id", None
    ).ilike("title", "distinctive-fact-%").execute()
    sb.table("projects").delete().eq("id", row["id"]).execute()


@pytest.mark.integration
@pytest.mark.skipif(not _RUN_LIVE_LLM, reason=_LIVE_LLM_SKIP_REASON)
def test_project_agent_reads_uploaded_document_live(_live_llm_client, _live_llm_project):
    """Upload a document carrying a distinctive fact, ask the REAL project
    group agent about it over a REAL Anthropic call, and assert the fact
    appears in the reply — proves the `custom_artifact` read branch live
    (AC14). Not run by the builder — RUN_LIVE_LLM-gated, the ship-gate
    verifier's job (PI12)."""
    pid = _live_llm_project["id"]
    distinctive = "the launch codename is ZEBRA-NINE-QUASAR"
    upload = _live_llm_client.post(
        f"/v1/projects/{pid}/documents",
        files={"file": (f"distinctive-fact-{uuid.uuid4().hex[:8]}.txt", distinctive.encode(), "text/plain")},
    )
    assert upload.status_code == 200, upload.text

    r = _live_llm_client.post(
        f"/v1/projects/{pid}/group/turns",
        json={"content": "@Sprntly what is the launch codename mentioned in our documents?"},
    )
    assert r.status_code == 200, r.text

    turns = _live_llm_client.get(f"/v1/projects/{pid}/group/turns").json()["turns"]
    agent_turns = [t for t in turns if t.get("role") == "assistant"]
    assert agent_turns, "no agent reply arrived"
    assert "ZEBRA-NINE-QUASAR" in agent_turns[-1]["content"], agent_turns[-1]["content"]
