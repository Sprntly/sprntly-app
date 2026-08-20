"""Per-project free-text instructions for the Sprntly agent — persist
(migration + `db.projects.get_instructions`/`set_instructions`), the
GET/PUT route pair (membership-gated, existence-non-disclosure), and the
fold into BOTH project surfaces' system prompt via a single-sourced
`project_group_context._instructions_block`.

Four test tiers, in file order:

  1. Migration / storage round-trip — needs the REAL local Supabase (a
     `FakeSupabaseClient` has no SQL engine and cannot apply a raw `ALTER
     TABLE`). Gated behind `RUN_PROJECT_INSTRUCTIONS_LIVE=1` + docker, same
     shape as `test_delegation_followup_sends.py`. A builder-lane static
     proxy (`test_migration_is_idempotent_by_construction`, mirrors
     `test_artifact_shares_db.py`) proves the SQL is guarded without
     touching any Supabase instance.
  2. Route / IDOR — through the real route + `_require_project_member`
     gate, against the in-memory fake Supabase (`isolated_settings`/
     `company_client`) — fast lane, runs on every PR.
  3. Context fold — `_build_private_scope` (`ask_job_runner.py`) and the
     group scope build (`routes.projects._respond_as_group_agent`, via the
     real `/group/turns` route with `qa_agent.answer` patched to capture
     the assembled system text, mirroring `test_group_trigger_and_no_
     fabrication.py::_fake_loop_capturing`) — deterministic, no DB SQL
     engine required beyond the fake client.
  4. Main-chat isolation (mutation-proofed) — `qa_agent.answer()` for
     `scope is None` / `SurfaceScope(surface=Surface.main)` never reaches
     an instructions block even when `db.projects.get_instructions` is
     patched to return one for ANY project id (the strongest form of "even
     when a same-company project has instructions set" — no DB seeding
     ambiguity). Manually mutation-proofed: temporarily forcing `_fold_
     project_context`/the main path to call `_instructions_block(...)`
     unconditionally turns this test RED; reverting turns it GREEN — the
     proof used to author this test is NOT itself shipped here (it would
     be a no-op assertion against real code).

  5. Live (ship-gate, real LLM) — the two "instructions actually reach the
     model's reply" tests, gated behind `RUN_PROJECT_INSTRUCTIONS_LIVE=1`
     PLUS a real `ANTHROPIC_API_KEY`, deferred to the ship-gate per PI12
     (a stubbed loop cannot prove the model actually honors the
     instruction).
"""
from __future__ import annotations

import logging
import os
import pathlib
import re
import shutil
import subprocess
import uuid

import pytest

import app.routes.projects as projects_route
from app.surface_scope import Surface, SurfaceScope
from tests._company_helpers import company_client
from tests._project_helpers import seed_same_tenant_non_member

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"
_MIGRATION_FILE = "20260815160000_projects_instructions.sql"
_DB_CONTAINER = os.getenv("PROJECTS_SCHEMA_TEST_DB_CONTAINER", "supabase_db_Sprntly")

_RUN_LIVE = os.getenv("RUN_PROJECT_INSTRUCTIONS_LIVE") == "1"
_RUN_LIVE_LLM = _RUN_LIVE and bool(os.getenv("ANTHROPIC_API_KEY"))

_LIVE_SKIP_REASON = (
    "needs a real local Supabase — set RUN_PROJECT_INSTRUCTIONS_LIVE=1 with "
    "SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY pointed at the local rig and the "
    "projects_instructions migration applied"
)
_LIVE_LLM_SKIP_REASON = (
    "needs a real local Supabase + a real ANTHROPIC_API_KEY — set "
    "RUN_PROJECT_INSTRUCTIONS_LIVE=1 with SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY/"
    "SUPABASE_JWT_SECRET/ANTHROPIC_API_KEY pointed at the local rig"
)


def _sb():
    from app.config import settings
    from supabase import create_client

    url = settings.supabase_url
    assert "127.0.0.1" in url or "localhost" in url, (
        "refusing to run the live project-instructions round-trip against a "
        f"non-loopback SUPABASE_URL ({url!r}) — this test mutates real rows"
    )
    assert settings.supabase_service_role_key, "SUPABASE_SERVICE_ROLE_KEY is not set"
    return create_client(url, settings.supabase_service_role_key)


@pytest.fixture(scope="module")
def sb():
    if not _RUN_LIVE:
        pytest.skip("live tier disabled")
    return _sb()


@pytest.fixture(scope="module")
def fixture_ids(sb):
    companies = sb.table("companies").select("id").limit(1).execute().data
    assert companies, "no company row in the local rig — seed one before running this test"
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


@pytest.fixture
def live_project(sb, fixture_ids):
    row = (
        sb.table("projects")
        .insert(
            {
                "company_id": fixture_ids["company_id"],
                "workspace_id": fixture_ids["workspace_id"],
                "name": f"instructions-roundtrip-{uuid.uuid4().hex[:8]}",
                "created_by": fixture_ids["user_id"],
            }
        )
        .execute()
        .data[0]
    )
    yield row
    sb.table("projects").delete().eq("id", row["id"]).execute()


# ── Migration / storage (real local Supabase — round-trip) — AC1/AC4/AC5 ──


def test_migration_is_idempotent_by_construction():
    """Builder-lane proxy: the `ALTER TABLE ... ADD COLUMN` in the new
    migration is guarded with `IF NOT EXISTS`, so a second apply is a no-op
    rather than an error. The literal "apply twice against a live instance"
    proof is the live test below — this environment has no docker
    guarantee and a builder unit test must not touch a potentially-shared
    local Supabase instance."""
    path = _MIGRATIONS_DIR / _MIGRATION_FILE
    assert path.is_file(), f"migration file missing: {path}"
    sql = path.read_text()
    alters = re.findall(
        r"alter\s+table\s+projects\s+add\s+column\s+(if not exists)?\s*instructions",
        sql,
        re.IGNORECASE,
    )
    assert alters, "migration defines no ALTER TABLE ... ADD COLUMN instructions"
    for guard in alters:
        assert guard, "ALTER TABLE ADD COLUMN instructions without IF NOT EXISTS"
    # No DEFAULT — absent means unset, never a hollow default value.
    assert not re.search(r"instructions\s+text\s+default", sql, re.IGNORECASE)


@pytest.mark.integration
@pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON)
def test_instructions_migration_idempotent():
    if shutil.which("docker") is None:
        pytest.skip("docker not on PATH — cannot re-apply the migration for the idempotency proof")
    path = _MIGRATIONS_DIR / _MIGRATION_FILE
    assert path.is_file(), f"migration file missing: {path}"
    for _ in range(2):
        with path.open("rb") as f:
            result = subprocess.run(
                [
                    "docker", "exec", "-i", _DB_CONTAINER,
                    "psql", "-U", "postgres", "-d", "postgres", "-v", "ON_ERROR_STOP=1",
                ],
                stdin=f, capture_output=True, timeout=30,
            )
        assert result.returncode == 0, (
            f"applying {_MIGRATION_FILE} was not idempotent:\n"
            f"stdout: {result.stdout.decode(errors='replace')}\n"
            f"stderr: {result.stderr.decode(errors='replace')}"
        )
    col = subprocess.run(
        [
            "docker", "exec", _DB_CONTAINER,
            "psql", "-U", "postgres", "-d", "postgres", "-tAc",
            "select count(*) from information_schema.columns "
            "where table_name='projects' and column_name='instructions';",
        ],
        capture_output=True, timeout=15,
    )
    assert col.stdout.decode().strip() == "1", "expected exactly one instructions column"


@pytest.mark.integration
@pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON)
def test_instructions_column_nullable_default_null(live_project):
    from app.db import projects as projects_db

    assert projects_db.get_instructions(live_project["id"]) is None


@pytest.mark.integration
@pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON)
def test_set_then_get_roundtrip(sb, live_project):
    from app.db import projects as projects_db

    before = sb.table("projects").select("updated_at").eq("id", live_project["id"]).execute().data[0]
    projects_db.set_instructions(live_project["id"], "Ship pricing under 60s.")
    assert projects_db.get_instructions(live_project["id"]) == "Ship pricing under 60s."
    after = sb.table("projects").select("updated_at").eq("id", live_project["id"]).execute().data[0]
    assert after["updated_at"] >= before["updated_at"]


@pytest.mark.integration
@pytest.mark.skipif(not _RUN_LIVE, reason=_LIVE_SKIP_REASON)
def test_set_empty_clears(live_project):
    from app.db import projects as projects_db

    projects_db.set_instructions(live_project["id"], "something")
    assert projects_db.get_instructions(live_project["id"]) == "something"
    projects_db.set_instructions(live_project["id"], "   ")
    assert projects_db.get_instructions(live_project["id"]) is None


# ── Route / IDOR (through the real route + gate) — AC2/AC3/AC4/AC6/AC13 ───


def _create_project(ctx, *, name: str = "Instructions project") -> dict:
    r = ctx.client.post("/v1/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def test_get_instructions_member_ok(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    r = ctx.client.get(f"/v1/projects/{project['id']}/instructions")
    assert r.status_code == 200
    assert r.json() == {"instructions": None}


def test_instructions_foreign_tenant_404(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    from app.db import projects as projects_db

    foreign = projects_db.create_project(
        company_id="foreign-co", workspace_id="foreign-ws",
        name="Not mine", created_by="someone-else",
    )

    r_get = ctx.client.get(f"/v1/projects/{foreign['id']}/instructions")
    assert r_get.status_code == 404

    r_put = ctx.client.put(f"/v1/projects/{foreign['id']}/instructions", json={"instructions": "x"})
    assert r_put.status_code == 404


def test_instructions_same_tenant_nonmember_403(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    _, headers = seed_same_tenant_non_member(ctx)

    r_get = ctx.client.get(f"/v1/projects/{project['id']}/instructions", headers=headers)
    assert r_get.status_code == 403

    r_put = ctx.client.put(
        f"/v1/projects/{project['id']}/instructions", json={"instructions": "x"}, headers=headers,
    )
    assert r_put.status_code == 403


def test_put_instructions_persists_and_returns(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)

    r = ctx.client.put(
        f"/v1/projects/{project['id']}/instructions",
        json={"instructions": "Priced quotes must return in under 60s."},
    )
    assert r.status_code == 200
    assert r.json() == {"instructions": "Priced quotes must return in under 60s."}

    r2 = ctx.client.get(f"/v1/projects/{project['id']}/instructions")
    assert r2.json() == {"instructions": "Priced quotes must return in under 60s."}


def test_put_empty_clears(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    ctx.client.put(f"/v1/projects/{project['id']}/instructions", json={"instructions": "set it"})

    r = ctx.client.put(f"/v1/projects/{project['id']}/instructions", json={"instructions": "   "})
    assert r.status_code == 200
    assert r.json() == {"instructions": None}


def test_put_over_cap_422(isolated_settings, monkeypatch):
    # NOTE (landmark drift vs the ticket): AC6 names HTTP 400 for the
    # Pydantic `max_length` violation, but this codebase never installs a
    # custom RequestValidationError handler — every other `max_length`-
    # gated route (`test_routes_ask.py`, `test_chat_suggestions.py`,
    # `test_routes_prd_chat_edit.py`, etc.) asserts FastAPI's actual
    # default, 422. The code (and the rest of this codebase) wins; this
    # test proves the real over-cap behavior instead of the ticket's stale
    # number.
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    ctx.client.put(f"/v1/projects/{project['id']}/instructions", json={"instructions": "baseline"})

    r = ctx.client.put(
        f"/v1/projects/{project['id']}/instructions", json={"instructions": "x" * 2001},
    )
    assert r.status_code == 422

    r2 = ctx.client.get(f"/v1/projects/{project['id']}/instructions")
    assert r2.json() == {"instructions": "baseline"}


def test_put_logs_id_only(isolated_settings, monkeypatch, caplog):
    ctx = company_client(monkeypatch)
    project = _create_project(ctx)
    secret = "SECRET_INSTRUCTIONS_DO_NOT_LOG the launch plan"

    with caplog.at_level(logging.INFO):
        r = ctx.client.put(f"/v1/projects/{project['id']}/instructions", json={"instructions": secret})
    assert r.status_code == 200

    lines = [rec.getMessage() for rec in caplog.records if "project_instructions_set" in rec.getMessage()]
    assert len(lines) == 1
    assert f"project_id={project['id']}" in lines[0]
    joined = "\n".join(rec.getMessage() for rec in caplog.records)
    assert secret not in joined


# ── Context fold — AC7/AC8/AC9/AC11 ────────────────────────────────────────


# Retargeted from the deleted `ask_job_runner._build_private_scope`: the
# project scope-building (roster + INSTRUCTIONS fold, both surfaces) relocated
# into `ProjectContextAssembler.assemble` (`context_assembler_project.py`). The
# invariant is unchanged — the assembled `SurfaceScope.system_addendum` carries
# the roster block, then the PROJECT INSTRUCTIONS block — so the tests now drive
# the real assembler over a real (membership-gated) project.
def _assemble_scope(ctx, project_id: int, *, surface: str, dataset: str = ""):
    from app.context_assembler import AssembleRequest
    from app.context_assembler_project import ProjectContextAssembler
    from app.db.workspaces import ensure_default_workspace

    ws_id = ensure_default_workspace(ctx.company_id)["id"]
    req = AssembleRequest(
        user_id=ctx.user_id, company_id=ctx.company_id, dataset=dataset,
        conversation_id=None, question="status?", workspace_id=ws_id,
        params={"project_id": project_id, "surface": surface},
    )
    return ProjectContextAssembler().assemble(req)


def test_private_scope_includes_instructions_after_roster(isolated_settings, monkeypatch):
    from app.ask_job_runner import _private_roster_block
    from app.db import projects as projects_db

    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "Instr private"}).json()
    projects_db.set_instructions(project["id"], "Always cite sources.")

    scope = _assemble_scope(ctx, project["id"], surface="private")

    roster_block = _private_roster_block(projects_db.list_members(project["id"]))
    assert roster_block in scope.system_addendum
    assert "PROJECT INSTRUCTIONS" in scope.system_addendum
    assert "Always cite sources." in scope.system_addendum
    assert scope.system_addendum.index(roster_block) < scope.system_addendum.index(
        "PROJECT INSTRUCTIONS"
    )


def test_private_scope_no_block_when_empty(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "No instr private"}).json()

    scope = _assemble_scope(ctx, project["id"], surface="private")
    assert "PROJECT INSTRUCTIONS" not in scope.system_addendum


def test_scope_build_survives_instructions_read_failure(isolated_settings, monkeypatch):
    from app.db import projects as projects_db

    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "Instr fail private"}).json()

    def _boom(pid):
        raise RuntimeError("read failed")

    monkeypatch.setattr(projects_db, "get_instructions", _boom)

    scope = _assemble_scope(ctx, project["id"], surface="private")
    assert "PROJECT INSTRUCTIONS" not in scope.system_addendum
    assert scope.system_addendum  # the rest of the addendum still built


def test_instructions_truncated_at_cap(monkeypatch):
    from app.project_group_context import _INSTRUCTIONS_CHARS, _instructions_block

    long_text = "x" * (_INSTRUCTIONS_CHARS + 500)
    block = _instructions_block(long_text)
    assert block.endswith("…")
    assert len(block) <= _INSTRUCTIONS_CHARS + len("PROJECT INSTRUCTIONS (set by the team — follow these):\n") + 1


# Retargeted from the deleted in-band group-reply path (the group POST no longer
# calls `qa_agent.answer`; the reply runs on the `/v1/ask` mount). The invariant
# — the GROUP surface's assembled scope folds in the PROJECT INSTRUCTIONS block —
# is tested directly on the relocated assembler, the single seam qa_agent then
# consumes as `scope.system_addendum`.
def test_group_scope_includes_instructions(isolated_settings, monkeypatch):
    from app.db import projects as projects_db

    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "Instr group"}).json()
    projects_db.set_instructions(project["id"], "Always answer in bullet points.")

    scope = _assemble_scope(ctx, project["id"], surface="group")
    assert "PROJECT INSTRUCTIONS" in scope.system_addendum
    assert "Always answer in bullet points." in scope.system_addendum


def test_group_scope_no_block_when_empty(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    project = ctx.client.post("/v1/projects", json={"name": "No instr group"}).json()

    scope = _assemble_scope(ctx, project["id"], surface="group")
    assert "PROJECT INSTRUCTIONS" not in scope.system_addendum


# ── Main-chat isolation (mutation-proofed) — AC10 ──────────────────────────


def _route_out():
    from types import SimpleNamespace

    return SimpleNamespace(output={"skill_id": None, "confidence": 0.0, "action": None})


def test_main_chat_scope_none_no_instructions_block(monkeypatch):
    """Main chat's assembled system prompt never carries a folded PROJECT
    INSTRUCTIONS block, for BOTH `scope is None` and `SurfaceScope(surface
    =Surface.main)` — even when `db.projects.get_instructions` would
    return an instructions block for ANY project id (the strongest form of
    "even when a same-company project has instructions set": no code on
    the main path calls `get_instructions`/`_instructions_block` at all,
    so patching it to always return a sentinel and asserting the sentinel
    never reaches the LLM call directly proves the seam, not just a DB
    fixture's absence of data).

    Mutation-proofed manually (not shipped as a second producer in this
    file): temporarily forcing `qa_agent._fold_project_context` to append
    `_instructions_block(...)` unconditionally — bypassing its `scope is
    None or scope.surface == Surface.main` guard — turns this test RED;
    reverting turns it back GREEN."""
    import app.qa_agent as qa
    from app.db import projects as projects_db

    sentinel = "PROJECT INSTRUCTIONS (set by the team — follow these):\nDO-NOT-LEAK-SENTINEL"
    monkeypatch.setattr(projects_db, "get_instructions", lambda pid: sentinel)

    systems: list[str] = []

    def _fake_llm_call(**k):
        from types import SimpleNamespace

        if k.get("purpose") == "route":
            return _route_out()
        systems.append(k.get("system") or "")
        return SimpleNamespace(
            output={"answer": "ok", "key_points": [], "citations": [], "confidence": 0.9, "unanswered": ""}
        )

    monkeypatch.setattr(qa, "llm_call", _fake_llm_call)
    monkeypatch.setattr(qa, "route", lambda *a, **k: qa.RouteDecision("call-digest-like", 0.0, "none"))

    common = dict(
        enterprise_id="ent", question="anything at all", dataset="acme", pinned_skill="__builtin_none__",
    )
    qa.answer(**common)
    qa.answer(**common, scope=None)
    qa.answer(**common, scope=SurfaceScope(surface=Surface.main))

    assert len(systems) == 3
    for system in systems:
        assert "PROJECT INSTRUCTIONS" not in system
        assert "DO-NOT-LEAK-SENTINEL" not in system


# ── Live (ship-gate, real LLM — authored, deferred execution) — AC14 ──────


@pytest.fixture(scope="module")
def live_llm_fixture_ids(sb):
    if not _RUN_LIVE_LLM:
        pytest.skip("live-LLM tier disabled")
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


def _bearer(user_id: str) -> dict[str, str]:
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
def live_llm_client(live_llm_fixture_ids):
    import app.main as main_mod
    from app.config import settings
    from fastapi.testclient import TestClient

    headers = _bearer(live_llm_fixture_ids["user_id"])
    headers["X-Workspace-Id"] = live_llm_fixture_ids["workspace_id"]
    headers["Origin"] = settings.origins_list[0]
    return TestClient(main_mod.app, headers=headers)


@pytest.fixture
def live_llm_project(sb, live_llm_fixture_ids):
    row = (
        sb.table("projects")
        .insert(
            {
                "company_id": live_llm_fixture_ids["company_id"],
                "workspace_id": live_llm_fixture_ids["workspace_id"],
                "name": f"instructions-live-{uuid.uuid4().hex[:8]}",
                "created_by": live_llm_fixture_ids["user_id"],
            }
        )
        .execute()
        .data[0]
    )
    sb.table("project_members").insert(
        {"project_id": row["id"], "user_id": live_llm_fixture_ids["user_id"]}
    ).execute()
    yield row
    sb.table("projects").delete().eq("id", row["id"]).execute()


@pytest.mark.integration
@pytest.mark.skipif(not _RUN_LIVE_LLM, reason=_LIVE_LLM_SKIP_REASON)
def test_instructions_change_private_reply_live(sb, live_llm_client, live_llm_project):
    """A REAL private-chat reply, with instructions set to force an exact
    leading token, begins with that token; clearing the instructions makes
    the same question NOT begin with it (AC14)."""
    from app.db import projects as projects_db

    pid = live_llm_project["id"]
    projects_db.set_instructions(pid, "Begin every reply with the exact token BLUEPRINT:")

    r = live_llm_client.post(f"/v1/projects/{pid}/individual")
    assert r.status_code == 200, r.text
    conv_id = r.json()["id"]

    r2 = live_llm_client.post(
        "/v1/ask", json={"question": "What is this project about?", "conversation_id": conv_id, "project_id": pid},
    )
    assert r2.status_code == 200, r2.text
    answer = (r2.json().get("answer") or "").strip()
    assert answer.startswith("BLUEPRINT:"), answer

    projects_db.set_instructions(pid, "")
    r3 = live_llm_client.post(
        "/v1/ask", json={"question": "What is this project about, again?", "conversation_id": conv_id, "project_id": pid},
    )
    assert r3.status_code == 200, r3.text
    answer2 = (r3.json().get("answer") or "").strip()
    assert not answer2.startswith("BLUEPRINT:"), answer2


@pytest.mark.integration
@pytest.mark.skipif(not _RUN_LIVE_LLM, reason=_LIVE_LLM_SKIP_REASON)
def test_instructions_change_group_reply_live(sb, live_llm_client, live_llm_project):
    """A REAL @Sprntly group reply, with the same forced-token instruction
    set, begins with that token (AC14)."""
    from app.db import projects as projects_db

    pid = live_llm_project["id"]
    projects_db.set_instructions(pid, "Begin every reply with the exact token BLUEPRINT:")

    r = live_llm_client.post(
        f"/v1/projects/{pid}/group/turns", json={"content": "@Sprntly what is this project about?"},
    )
    assert r.status_code == 200, r.text

    turns = live_llm_client.get(f"/v1/projects/{pid}/group/turns").json()["turns"]
    assistant_turns = [t for t in turns if t.get("role") == "assistant"]
    assert assistant_turns, "expected at least one assistant reply"
    assert (assistant_turns[-1].get("content") or "").strip().startswith("BLUEPRINT:")
