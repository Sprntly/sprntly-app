"""Tests for the Design Agent lifecycle routes + helpers (P2-07).

Covers the three new routes appended to `app.routes.design_agent`:
    POST /v1/design-agent/{id}/complete   (F14 — lock)
    POST /v1/design-agent/{id}/resume     (F15 — unlock + flag stale handoff)
    POST /v1/design-agent/{id}/share      (F6  — share config)
plus the four new helpers in `app.db.prototypes`:
    mark_complete / resume_iteration / flag_stale_handoff / record_export_at_complete.

Sibling to `test_design_agent_routes.py` (same in-memory FakeSupabase env/client/
unauth fixture pattern), kept separate so the P2-07 diff stays focused.

THE KNOT (see the ticket's Implementation Notes): the `prototype_exports` table
does NOT exist at this HEAD — P2-09's migration creates it. So `flag_stale_handoff`
is exercised against a TEST-LOCAL `prototype_exports` table seeded into the fake
below. This ticket ships ZERO migrations; the DDL here is a test mirror of P2-09's
future table, not the source of truth. When the P2-07/08/09 trio batch-merges,
P2-09's real migration lands the column before the helper runs against a real DB.

AUTH NOTE (P6-10, mirrors test_design_agent_routes.py): the routes now gate on
`require_company` (Supabase Bearer JWT → company membership), so the client
fixture delegates to conftest's bearer-authed `company_client`; authed calls
resolve workspace_id to `_TEST_COMPANY_ID`. Cross-workspace isolation is proven
by seeding a row under a FOREIGN workspace ('demo') and asserting the company
call returns 404.
"""
from __future__ import annotations

import importlib
import logging
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.conftest import _TEST_COMPANY_ID

# SQLite-compatible end-state of `prototypes` AFTER P1-06 + the P2-06 sharing
# migration (base columns + share_mode/share_token/share_passcode_hash/
# is_complete/complete_checkpoint_id), plus prototype_checkpoints and a
# TEST-LOCAL `prototype_exports` mirror of P2-09's future table. P2-07 itself
# ships no migration — this DDL only feeds the in-memory fake.
_PROTOTYPE_DDL = """
CREATE TABLE prototypes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id                 INTEGER,
    workspace_id           TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'generating',
    variant                TEXT NOT NULL DEFAULT 'v1',
    template_version       INTEGER NOT NULL,
    instructions           TEXT,
    target_platform        TEXT NOT NULL DEFAULT 'both',
    figma_file_key         TEXT,
    website_url            TEXT,
    github_installation_id INTEGER,
    bundle_url             TEXT,
    preview_image_url      TEXT,
    current_checkpoint_id  INTEGER,
    error                  TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at           TEXT,
    share_mode             TEXT NOT NULL DEFAULT 'private'
                           CHECK (share_mode IN ('private', 'public', 'passcode')),
    share_token            TEXT UNIQUE,
    share_passcode_hash    TEXT,
    is_complete            INTEGER NOT NULL DEFAULT 0,
    complete_checkpoint_id INTEGER
);
CREATE TABLE prototype_checkpoints (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    prototype_id      INTEGER NOT NULL,
    workspace_id      TEXT NOT NULL,
    bundle_url        TEXT,
    prd_revision_hash TEXT,
    figma_frame_hash  TEXT,
    prompt_history    TEXT NOT NULL DEFAULT '[]',
    comment_state     TEXT NOT NULL DEFAULT '[]',
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE prototype_exports (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    prototype_id           INTEGER NOT NULL,
    workspace_id           TEXT NOT NULL,
    complete_checkpoint_id INTEGER,
    export_markdown        TEXT,
    is_stale               INTEGER NOT NULL DEFAULT 0,
    created_at             TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """isolated_settings + prototype tables + feature flag ON, with the design
    agent module stack reloaded in dependency order. Returns the live modules."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)

    # Gate ON by default; gate tests flip/clear it. Read at request time, so no
    # reload needed when a test changes it.
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)            # rebind require_client -> reloaded client
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)           # rebind its `from app.db.prototypes import ...`
    import app.main as main_mod
    importlib.reload(main_mod)             # rebuild the app with the reloaded router

    import app.db as db_mod
    return SimpleNamespace(proto=proto_mod, routes=routes_mod, main=main_mod, db=db_mod)


@pytest.fixture
def client(company_client) -> TestClient:
    """Bearer-authed TestClient (require_company) — see conftest.company_client."""
    return company_client


@pytest.fixture
def unauth(env) -> TestClient:
    """TestClient without any session cookie."""
    return TestClient(env.main.app)


# ─── helpers ──────────────────────────────────────────────────────────────


def _seed_ready(env, *, workspace_id=_TEST_COMPANY_ID, checkpoint_id=None) -> int:
    """Insert a prototype and complete it to status='ready' (optionally with a
    current_checkpoint_id), so /complete's status gate passes."""
    pid = env.proto.start_prototype(
        prd_id=1, workspace_id=workspace_id, template_version=1
    )
    env.proto.complete_prototype(
        prototype_id=pid,
        workspace_id=workspace_id,
        bundle_url="https://example.invalid/bundle",
        current_checkpoint_id=checkpoint_id,
    )
    return pid


def _seed_export(prototype_id: int, *, workspace_id=_TEST_COMPANY_ID, is_stale=0) -> int:
    """Insert a TEST-LOCAL prototype_exports row (the handoff record) and return
    its id."""
    from tests import _fake_supabase

    db = _fake_supabase.get_fake_db()
    cur = db.execute(
        "INSERT INTO prototype_exports (prototype_id, workspace_id, is_stale) "
        "VALUES (?, ?, ?)",
        [prototype_id, workspace_id, is_stale],
    )
    db.commit()
    return cur.lastrowid


def _set_checkpoint(prototype_id: int, checkpoint_id: int) -> None:
    """Directly mutate current_checkpoint_id (simulates a later Apply checkpoint)."""
    from tests import _fake_supabase

    db = _fake_supabase.get_fake_db()
    db.execute(
        "UPDATE prototypes SET current_checkpoint_id = ? WHERE id = ?",
        [checkpoint_id, prototype_id],
    )
    db.commit()


def _export_row(export_id: int) -> dict:
    from tests import _fake_supabase

    db = _fake_supabase.get_fake_db()
    r = db.execute(
        "SELECT * FROM prototype_exports WHERE id = ?", [export_id]
    ).fetchone()
    return dict(r)


# ═══════════════════════════════════════════════════════════════════════════
# Creation — POST /complete (AC #1, #12)
# ═══════════════════════════════════════════════════════════════════════════


def test_complete_locks_prototype_and_promotes_checkpoint(env, client):
    # AC #1
    pid = _seed_ready(env, checkpoint_id=7)
    resp = client.post(f"/v1/design-agent/{pid}/complete")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["prototype_id"] == pid
    assert body["is_complete"] is True
    assert body["complete_checkpoint_id"] == 7


def test_complete_is_idempotent_preserving_first_checkpoint(env, client):
    # AC #1 — first complete locks checkpoint 7; a later current_checkpoint_id
    # change + re-complete must NOT move the canonical lock point.
    pid = _seed_ready(env, checkpoint_id=7)
    first = client.post(f"/v1/design-agent/{pid}/complete")
    assert first.status_code == 200, first.text
    assert first.json()["complete_checkpoint_id"] == 7

    _set_checkpoint(pid, 99)
    second = client.post(f"/v1/design-agent/{pid}/complete")
    assert second.status_code == 200, second.text
    assert second.json()["complete_checkpoint_id"] == 7  # unchanged
    assert second.json()["is_complete"] is True


def test_complete_invokes_record_export_at_complete_once(env, client, monkeypatch):
    # AC #12 — the export hook is called exactly once with the right args.
    # P2-09 made record_export_at_complete async and the /complete handler now
    # `await`s it, so the spy MUST be an async function (a sync spy returns None
    # and `await None` raises TypeError). This is the required AsyncMock-style
    # adjustment called out in P2-09's Implementation Notes, not an AC15 break.
    calls: list[dict] = []

    async def _spy(*, prototype_id, workspace_id):
        calls.append({"prototype_id": prototype_id, "workspace_id": workspace_id})

    monkeypatch.setattr(env.routes, "record_export_at_complete", _spy)
    pid = _seed_ready(env, checkpoint_id=3)
    resp = client.post(f"/v1/design-agent/{pid}/complete")
    assert resp.status_code == 200, resp.text
    assert calls == [{"prototype_id": pid, "workspace_id": _TEST_COMPANY_ID}]


# ─── Error handling — POST /complete (AC #2, #3, #10, #11) ──────────────────


def test_complete_returns_409_on_generating(env, client):
    # AC #2
    pid = env.proto.start_prototype(prd_id=1, workspace_id=_TEST_COMPANY_ID, template_version=1)
    resp = client.post(f"/v1/design-agent/{pid}/complete")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Cannot complete: status=generating"


def test_complete_returns_409_on_failed(env, client):
    pid = env.proto.start_prototype(prd_id=1, workspace_id=_TEST_COMPANY_ID, template_version=1)
    env.proto.fail_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID, error="boom")
    resp = client.post(f"/v1/design-agent/{pid}/complete")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "Cannot complete: status=failed"


def test_complete_returns_404_when_wrong_workspace(env, client):
    # AC #3 — row under FOREIGN workspace 'demo'; app-session caller can't see it.
    pid = _seed_ready(env, workspace_id="demo", checkpoint_id=1)
    resp = client.post(f"/v1/design-agent/{pid}/complete")
    assert resp.status_code == 404


def test_complete_returns_404_on_missing(env, client):
    assert client.post("/v1/design-agent/999999/complete").status_code == 404


def test_complete_returns_404_when_flag_off(env, client, monkeypatch):
    # AC #10
    pid = _seed_ready(env, checkpoint_id=1)
    monkeypatch.delenv("DESIGN_AGENT_ENABLED", raising=False)
    assert client.post(f"/v1/design-agent/{pid}/complete").status_code == 404


def test_complete_returns_401_when_unauthenticated(env, unauth):
    # AC #11
    assert unauth.post("/v1/design-agent/1/complete").status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# Creation — POST /resume (AC #4, #5, #6)
# ═══════════════════════════════════════════════════════════════════════════


def test_resume_unlocks_prototype(env, client):
    pid = _seed_ready(env, checkpoint_id=2)
    client.post(f"/v1/design-agent/{pid}/complete")
    resp = client.post(f"/v1/design-agent/{pid}/resume")
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_complete"] is False


def test_resume_preserves_complete_checkpoint_id(env, client):
    pid = _seed_ready(env, checkpoint_id=5)
    client.post(f"/v1/design-agent/{pid}/complete")
    resp = client.post(f"/v1/design-agent/{pid}/resume")
    assert resp.status_code == 200, resp.text
    row = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert row["complete_checkpoint_id"] == 5  # historical lock point retained


def test_resume_is_idempotent_on_wip(env, client):
    # AC #6 — resume on a never-completed (is_complete=false) prototype is a no-op 200.
    pid = _seed_ready(env, checkpoint_id=1)
    resp = client.post(f"/v1/design-agent/{pid}/resume")
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_complete"] is False


def test_resume_flags_recent_export_stale_when_present(env, client):
    # AC #4 (positive) — a previous export row is flagged stale; count == 1.
    pid = _seed_ready(env, checkpoint_id=4)
    client.post(f"/v1/design-agent/{pid}/complete")
    export_id = _seed_export(pid, is_stale=0)
    resp = client.post(f"/v1/design-agent/{pid}/resume")
    assert resp.status_code == 200, resp.text
    assert resp.json()["handoffs_flagged_stale"] == 1
    assert _export_row(export_id)["is_stale"] == 1


def test_resume_returns_handoffs_flagged_zero_when_no_export(env, client):
    # AC #4 (negative) — never-completed prototype, no export row → count 0.
    pid = _seed_ready(env, checkpoint_id=1)
    resp = client.post(f"/v1/design-agent/{pid}/resume")
    assert resp.status_code == 200, resp.text
    assert resp.json()["handoffs_flagged_stale"] == 0


def test_resume_already_stale_export_returns_zero(env, client):
    # AC #5 — most-recent export already stale → only newly-flagged counted (0).
    pid = _seed_ready(env, checkpoint_id=4)
    _seed_export(pid, is_stale=1)
    resp = client.post(f"/v1/design-agent/{pid}/resume")
    assert resp.status_code == 200, resp.text
    assert resp.json()["handoffs_flagged_stale"] == 0


# ─── Error handling — POST /resume (AC #10, #11) ────────────────────────────


def test_resume_returns_404_on_missing(env, client):
    assert client.post("/v1/design-agent/999999/resume").status_code == 404


def test_resume_returns_404_when_wrong_workspace(env, client):
    pid = _seed_ready(env, workspace_id="demo", checkpoint_id=1)
    assert client.post(f"/v1/design-agent/{pid}/resume").status_code == 404


def test_resume_returns_404_when_flag_off(env, client, monkeypatch):
    # AC #10
    pid = _seed_ready(env, checkpoint_id=1)
    monkeypatch.delenv("DESIGN_AGENT_ENABLED", raising=False)
    assert client.post(f"/v1/design-agent/{pid}/resume").status_code == 404


def test_resume_returns_401_when_unauthenticated(env, unauth):
    # AC #11
    assert unauth.post("/v1/design-agent/1/resume").status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# Creation — POST /share (AC #7, #9)
# ═══════════════════════════════════════════════════════════════════════════


def test_share_public_returns_share_token(env, client):
    # AC #7
    pid = _seed_ready(env, checkpoint_id=1)
    resp = client.post(f"/v1/design-agent/{pid}/share", json={"mode": "public"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["share_mode"] == "public"
    token = body["share_token"]
    assert token is not None
    uuid.UUID(str(token))  # valid UUID
    # Round-trip: the token resolves back to this prototype.
    found = env.proto.find_prototype_by_share_token(token)
    assert found is not None and found["id"] == pid


def test_share_passcode_stores_argon2_hash(env, client):
    # AC #9
    pid = _seed_ready(env, checkpoint_id=1)
    resp = client.post(
        f"/v1/design-agent/{pid}/share",
        json={"mode": "passcode", "passcode": "hunter2"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["share_token"] is not None
    row = env.proto.get_prototype(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert env.proto.verify_share_passcode("hunter2", row["share_passcode_hash"]) is True


def test_share_private_preserves_token(env, client):
    # 66b04f1: setting a prototype private PRESERVES its share_token so the
    # /p/<slug>/<token> URL stays static across public↔private toggles — the
    # mode (not the token) gates visibility (the public resolver 404s private).
    pid = _seed_ready(env, checkpoint_id=1)
    public_resp = client.post(f"/v1/design-agent/{pid}/share", json={"mode": "public"})
    public_token = public_resp.json()["share_token"]
    assert public_token is not None
    resp = client.post(f"/v1/design-agent/{pid}/share", json={"mode": "private"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["share_mode"] == "private"
    # Token is preserved (not nulled) — same stable token across the toggle.
    assert resp.json()["share_token"] == public_token


# ─── Error handling — POST /share (AC #8, #10, #11) ─────────────────────────


def test_share_passcode_without_passcode_returns_400(env, client):
    # AC #8
    pid = _seed_ready(env, checkpoint_id=1)
    resp = client.post(f"/v1/design-agent/{pid}/share", json={"mode": "passcode"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "passcode-mode requires a passcode"


def test_share_invalid_mode_returns_422(env, client):
    pid = _seed_ready(env, checkpoint_id=1)
    resp = client.post(f"/v1/design-agent/{pid}/share", json={"mode": "broken"})
    assert resp.status_code == 422  # pydantic Literal rejection


def test_share_returns_404_when_wrong_workspace(env, client):
    pid = _seed_ready(env, workspace_id="demo", checkpoint_id=1)
    resp = client.post(f"/v1/design-agent/{pid}/share", json={"mode": "public"})
    assert resp.status_code == 404


def test_share_returns_404_when_flag_off(env, client, monkeypatch):
    # AC #10
    pid = _seed_ready(env, checkpoint_id=1)
    monkeypatch.delenv("DESIGN_AGENT_ENABLED", raising=False)
    resp = client.post(f"/v1/design-agent/{pid}/share", json={"mode": "public"})
    assert resp.status_code == 404


def test_share_returns_401_when_unauthenticated(env, unauth):
    # AC #11
    resp = unauth.post("/v1/design-agent/1/share", json={"mode": "public"})
    assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# Helpers (direct)
# ═══════════════════════════════════════════════════════════════════════════


def test_mark_complete_helper_idempotent(env):
    pid = _seed_ready(env, checkpoint_id=7)
    first = env.proto.mark_complete(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert first["is_complete"] in (1, True)
    assert first["complete_checkpoint_id"] == 7
    _set_checkpoint(pid, 42)
    second = env.proto.mark_complete(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert second["complete_checkpoint_id"] == 7  # not moved by the re-call


def test_mark_complete_helper_raises_when_missing(env):
    with pytest.raises(ValueError):
        env.proto.mark_complete(prototype_id=999999, workspace_id=_TEST_COMPANY_ID)


def test_resume_iteration_helper_idempotent(env):
    pid = _seed_ready(env, checkpoint_id=1)
    env.proto.mark_complete(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    once = env.proto.resume_iteration(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert once["is_complete"] in (0, False)
    twice = env.proto.resume_iteration(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    assert twice["is_complete"] in (0, False)  # no state change


def test_flag_stale_handoff_marks_recent_export_stale_when_present(env):
    pid = _seed_ready(env, checkpoint_id=1)
    export_id = _seed_export(pid, is_stale=0)
    assert env.proto.flag_stale_handoff(prototype_id=pid, workspace_id=_TEST_COMPANY_ID) == 1
    assert _export_row(export_id)["is_stale"] == 1


def test_flag_stale_handoff_returns_zero_when_no_export(env):
    pid = _seed_ready(env, checkpoint_id=1)
    assert env.proto.flag_stale_handoff(prototype_id=pid, workspace_id=_TEST_COMPANY_ID) == 0


def test_flag_stale_handoff_is_idempotent_on_already_stale(env):
    pid = _seed_ready(env, checkpoint_id=1)
    _seed_export(pid, is_stale=0)
    assert env.proto.flag_stale_handoff(prototype_id=pid, workspace_id=_TEST_COMPANY_ID) == 1
    # Second call: the only export is now stale → filter excludes it → 0.
    assert env.proto.flag_stale_handoff(prototype_id=pid, workspace_id=_TEST_COMPANY_ID) == 0


def test_flag_stale_handoff_targets_most_recent_export(env):
    # Two exports; the most recent (higher id) is the live handoff and gets flagged.
    pid = _seed_ready(env, checkpoint_id=1)
    older = _seed_export(pid, is_stale=0)
    newer = _seed_export(pid, is_stale=0)
    assert env.proto.flag_stale_handoff(prototype_id=pid, workspace_id=_TEST_COMPANY_ID) == 1
    assert _export_row(newer)["is_stale"] == 1
    assert _export_row(older)["is_stale"] == 0  # untouched


def test_flag_stale_handoff_respects_workspace(env):
    # An export under a foreign workspace is invisible to the app-workspace flag.
    pid = _seed_ready(env, checkpoint_id=1)
    _seed_export(pid, workspace_id="demo", is_stale=0)
    assert env.proto.flag_stale_handoff(prototype_id=pid, workspace_id=_TEST_COMPANY_ID) == 0


async def test_record_export_at_complete_is_noop_when_prototype_missing(env):
    # P2-09 made this async + real. With no prototype row (id=1 unseeded here)
    # it no-ops gracefully: returns None, raises nothing, inserts no export row.
    assert (
        await env.proto.record_export_at_complete(prototype_id=1, workspace_id=_TEST_COMPANY_ID)
        is None
    )


# ═══════════════════════════════════════════════════════════════════════════
# Observability (AC #15)
# ═══════════════════════════════════════════════════════════════════════════


def test_mark_complete_logs_completed_line_no_pii(env, caplog):
    # AC #15 — prototype_completed INFO line with the checkpoint id; no PRD/PII.
    pid = _seed_ready(env, checkpoint_id=11)
    with caplog.at_level(logging.INFO, logger="app.db.prototypes"):
        env.proto.mark_complete(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "prototype_completed" in blob
    assert f"prototype_id={pid}" in blob
    assert "complete_checkpoint_id=11" in blob


def test_resume_iteration_logs_resumed_line(env, caplog):
    # AC #15 — prototype_resumed INFO line with the id.
    pid = _seed_ready(env, checkpoint_id=1)
    with caplog.at_level(logging.INFO, logger="app.db.prototypes"):
        env.proto.resume_iteration(prototype_id=pid, workspace_id=_TEST_COMPANY_ID)
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "prototype_resumed" in blob
    assert f"prototype_id={pid}" in blob


# ═══════════════════════════════════════════════════════════════════════════
# Non-breakage (AC #13, #14) — import/compile smoke
# ═══════════════════════════════════════════════════════════════════════════


def test_routes_module_exposes_new_handlers(env):
    # AC #13 — the new handlers are bound on the reloaded routes module.
    for name in ("post_complete", "post_resume", "post_share"):
        assert hasattr(env.routes, name)


def test_prototypes_module_exposes_new_helpers(env):
    # AC #14 — the four new helpers are bound on the reloaded db module.
    for name in (
        "mark_complete", "resume_iteration",
        "flag_stale_handoff", "record_export_at_complete",
    ):
        assert hasattr(env.proto, name)
