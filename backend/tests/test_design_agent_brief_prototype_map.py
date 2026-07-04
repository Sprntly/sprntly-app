"""Tests for GET /v1/design-agent/brief-prototype-map?brief_id=<int>.

The endpoint is a batch read-only route that, for a given brief, returns which
insights have a PRD (variant=PRD_VARIANT, status=ready) and whether each PRD
has a ready prototype (with preview_image_url when set).

Fixture shape mirrors test_design_agent_prd_patch_routes.py:
  - isolated_settings (conftest) — in-memory FakeSupabaseClient + env reset.
  - suite-local env fixture — seeds the prototypes table DDL + feature flag ON,
    then reloads app.db.prds → app.db.prototypes → app.routes.design_agent →
    app.main in dependency order so the route binds to the fake-wired helpers.
  - company_client (conftest) — bearer-authed TestClient whose calls resolve
    workspace_id == _TEST_COMPANY_ID (the same pattern all DA route suites use).

The prds table and prd_patches table are already in conftest's _FAKE_SCHEMA, so
only the prototypes + prototype_checkpoints tables need to be added here (same
_PROTOTYPE_DDL pattern as test_design_agent_routes.py, including preview_image_url
from test_design_agent_screenshot.py).
"""
from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.conftest import _TEST_COMPANY_ID

# SQLite-compatible prototypes DDL with preview_image_url column (mirrors
# test_design_agent_screenshot._PROTOTYPE_DDL — the column that find_ready_prototype_by_prd
# returns and that PrototypeReadiness exposes to callers).
_PROTOTYPE_DDL = """
CREATE TABLE prototypes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id                 INTEGER,
    workspace_id           TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'generating',
    variant                TEXT NOT NULL DEFAULT 'v1',
    template_version       INTEGER NOT NULL DEFAULT 1,
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
    share_passcode_hash    TEXT
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
"""

_OTHER_WS = "other-workspace"

# PRD_VARIANT as defined in prd_runner.py — the variant the new endpoint filters
# on. Imported so the test tracks the constant instead of hardcoding it.
from app.prd_runner import PRD_VARIANT as _PRD_VARIANT


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """isolated_settings + prototypes tables + feature flag ON.

    Reloads app.db.prds → app.db.prototypes → app.routes.design_agent → app.main
    in dependency order so the route's list_prds_by_brief + find_ready_prototype_by_prd
    bind to the fake-Supabase-wired helpers. The prds table is already in conftest's
    _FAKE_SCHEMA; only prototypes + prototype_checkpoints need to be added here.
    """
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    import app.db.prds as prds_mod
    importlib.reload(prds_mod)
    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)

    return SimpleNamespace(prds=prds_mod, proto=proto_mod, routes=routes_mod, main=main_mod)


@pytest.fixture
def client(company_client) -> TestClient:
    """Bearer-authed TestClient (require_company) — resolves workspace_id to _TEST_COMPANY_ID."""
    return company_client


@pytest.fixture
def unauth(env) -> TestClient:
    """TestClient with NO auth header — proves the route requires authentication."""
    return TestClient(env.main.app)


# ─── seeding helpers ──────────────────────────────────────────────────────────


def _seed_prd(
    *,
    brief_id: int = 1,
    insight_index: int = 0,
    variant: str = _PRD_VARIANT,
    status: str = "ready",
    title: str = "Test PRD",
) -> int:
    """Insert a PRD row directly into the fake DB and return its id."""
    from tests import _fake_supabase

    cur = _fake_supabase.get_fake_db().execute(
        "INSERT INTO prds (brief_id, insight_index, title, payload_md, status, variant) "
        "VALUES (?, ?, ?, '', ?, ?)",
        [brief_id, insight_index, title, status, variant],
    )
    return cur.lastrowid


def _seed_prototype(
    *,
    prd_id: int,
    workspace_id: str = _TEST_COMPANY_ID,
    status: str = "ready",
    preview_image_url: str | None = None,
) -> int:
    """Insert a prototype row directly into the fake DB and return its id."""
    from tests import _fake_supabase

    cur = _fake_supabase.get_fake_db().execute(
        "INSERT INTO prototypes (prd_id, workspace_id, status, preview_image_url) "
        "VALUES (?, ?, ?, ?)",
        [prd_id, workspace_id, status, preview_image_url],
    )
    return cur.lastrowid


# ─── Core shape tests ─────────────────────────────────────────────────────────


def test_brief_prototype_map_full_shape(client):
    """Three insights seeded:
      A (insight_index=0) → PRD with a READY prototype (preview_image_url set).
      B (insight_index=1) → PRD with NO ready prototype.
      C (insight_index=2) → no PRD at all (absent from entries).
    """
    # Insight A: PRD + ready prototype with preview URL.
    prd_a = _seed_prd(brief_id=1, insight_index=0)
    _seed_prototype(prd_id=prd_a, workspace_id=_TEST_COMPANY_ID, status="ready",
                    preview_image_url="https://storage.example/preview-a.png")

    # Insight B: PRD with no ready prototype.
    prd_b = _seed_prd(brief_id=1, insight_index=1)
    # (no prototype row for prd_b)

    # Insight C: no PRD at all — must be absent from entries.
    # (nothing seeded for insight_index=2)

    resp = client.get("/v1/design-agent/brief-prototype-map", params={"brief_id": 1})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["brief_id"] == 1
    entries = body["entries"]

    # Only two entries — insight C (no PRD) is absent.
    assert len(entries) == 2

    # Entries ordered by insight_index ascending.
    assert entries[0]["insight_index"] == 0
    assert entries[1]["insight_index"] == 1

    # Entry A: PRD + prototype with preview URL.
    entry_a = entries[0]
    assert entry_a["prd_id"] == prd_a
    assert entry_a["prd_title"] == "Test PRD"
    assert entry_a["prototype"] is not None
    assert entry_a["prototype"]["ready"] is True
    assert entry_a["prototype"]["preview_image_url"] == "https://storage.example/preview-a.png"

    # Entry B: PRD but no prototype.
    entry_b = entries[1]
    assert entry_b["prd_id"] == prd_b
    assert entry_b["prd_title"] == "Test PRD"
    assert entry_b["prototype"] is None


def test_brief_prototype_map_empty_when_no_prds(client):
    """A brief with no PRDs returns an empty entries list (not a 404)."""
    resp = client.get("/v1/design-agent/brief-prototype-map", params={"brief_id": 999})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["brief_id"] == 999
    assert body["entries"] == []


def test_brief_prototype_map_prototype_null_when_no_preview(client):
    """A ready prototype with no preview_image_url still populates prototype.ready=True
    and preview_image_url=null (not absent)."""
    prd = _seed_prd(brief_id=2, insight_index=0)
    _seed_prototype(prd_id=prd, workspace_id=_TEST_COMPANY_ID, status="ready",
                    preview_image_url=None)

    resp = client.get("/v1/design-agent/brief-prototype-map", params={"brief_id": 2})
    assert resp.status_code == 200, resp.text
    entry = resp.json()["entries"][0]
    assert entry["prototype"] is not None
    assert entry["prototype"]["ready"] is True
    assert entry["prototype"]["preview_image_url"] is None


# ─── Variant + status filtering ──────────────────────────────────────────────


def test_brief_prototype_map_excludes_wrong_variant(client):
    """PRDs with variant != PRD_VARIANT ('v2') are excluded from entries."""
    # Seed a v1 PRD and a v2 PRD for the same brief.
    _seed_prd(brief_id=3, insight_index=0, variant="v1")   # wrong variant — excluded
    prd_v2 = _seed_prd(brief_id=3, insight_index=1, variant=_PRD_VARIANT)  # correct

    resp = client.get("/v1/design-agent/brief-prototype-map", params={"brief_id": 3})
    assert resp.status_code == 200, resp.text
    entries = resp.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["prd_id"] == prd_v2
    assert entries[0]["insight_index"] == 1


def test_brief_prototype_map_excludes_non_ready_prds(client):
    """PRDs with status != 'ready' (generating, failed, invalidated) are excluded."""
    _seed_prd(brief_id=4, insight_index=0, status="generating")
    _seed_prd(brief_id=4, insight_index=1, status="failed")
    _seed_prd(brief_id=4, insight_index=2, status="invalidated")
    prd_ready = _seed_prd(brief_id=4, insight_index=3, status="ready")

    resp = client.get("/v1/design-agent/brief-prototype-map", params={"brief_id": 4})
    assert resp.status_code == 200, resp.text
    entries = resp.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["prd_id"] == prd_ready


# ─── Regeneration: newest ready PRD per insight ──────────────────────────────


def test_brief_prototype_map_returns_newest_prd_per_insight(client):
    """When an insight has MORE THAN ONE ready PRD (a regenerated / force-generated
    PRD leaves the prior one ready too), the map returns exactly ONE entry for that
    insight — the NEWEST (highest-id) one.

    Regression: list_prds_by_brief used to return every ready row ordered only by
    insight_index, so an insight emitted duplicate entries and the frontend's
    last-wins map could bind a stale prd_id — the freshly generated PRD never
    surfaced in the UI even though it existed.
    """
    # Insight 0: an OLD ready PRD, then a NEWER ready PRD (higher id).
    old_prd = _seed_prd(brief_id=7, insight_index=0, title="Old PRD")
    new_prd = _seed_prd(brief_id=7, insight_index=0, title="New PRD")
    assert new_prd > old_prd  # autoincrement guarantees newer == higher id

    # Insight 1: a single ready PRD — the simple case still works alongside.
    solo_prd = _seed_prd(brief_id=7, insight_index=1, title="Solo PRD")

    resp = client.get("/v1/design-agent/brief-prototype-map", params={"brief_id": 7})
    assert resp.status_code == 200, resp.text
    entries = resp.json()["entries"]

    # One entry per insight — NOT one per ready PRD row.
    assert len(entries) == 2
    by_insight = {e["insight_index"]: e for e in entries}

    # Insight 0 collapses to the newest PRD only.
    assert by_insight[0]["prd_id"] == new_prd
    assert by_insight[0]["prd_title"] == "New PRD"

    # Insight 1 unaffected.
    assert by_insight[1]["prd_id"] == solo_prd


# ─── Workspace isolation ──────────────────────────────────────────────────────


def test_brief_prototype_map_prototype_workspace_isolated(client):
    """A prototype belonging to a DIFFERENT workspace is invisible to the caller:
    entry.prototype is null even though the prd_id has a ready prototype row."""
    prd = _seed_prd(brief_id=5, insight_index=0)
    # Prototype is seeded under a foreign workspace — the caller (co-test) must not
    # see it; find_ready_prototype_by_prd filters by workspace_id.
    _seed_prototype(prd_id=prd, workspace_id=_OTHER_WS, status="ready",
                    preview_image_url="https://example.com/foreign.png")

    resp = client.get("/v1/design-agent/brief-prototype-map", params={"brief_id": 5})
    assert resp.status_code == 200, resp.text
    entries = resp.json()["entries"]
    # PRD is present (no workspace filter on PRDs by design — see route docstring flag).
    assert len(entries) == 1
    # But prototype is null — cross-workspace prototype not visible.
    assert entries[0]["prototype"] is None


# ─── Auth + feature gate ──────────────────────────────────────────────────────


def test_brief_prototype_map_requires_auth(unauth):
    """Unauthenticated request returns 401."""
    resp = unauth.get("/v1/design-agent/brief-prototype-map", params={"brief_id": 1})
    assert resp.status_code == 401


def test_brief_prototype_map_returns_404_when_flag_off(client, monkeypatch):
    """When DESIGN_AGENT_ENABLED is off, the endpoint returns 404 (feature invisible)."""
    monkeypatch.delenv("DESIGN_AGENT_ENABLED", raising=False)
    resp = client.get("/v1/design-agent/brief-prototype-map", params={"brief_id": 1})
    assert resp.status_code == 404


def test_brief_prototype_map_no_create_side_effects(client, monkeypatch, env):
    """Confirm no create/generate calls are made — the route is pure read.

    Patches start_prototype + generate_prototype to raise if called; a brief
    with a PRD (no prototype) must still return 200 with prototype=null, not
    500 from a side-effect call.
    """
    prd = _seed_prd(brief_id=6, insight_index=0)

    def _must_not_call(*a, **kw):
        raise AssertionError("create/generate must not be called from brief-prototype-map")

    monkeypatch.setattr(env.proto, "start_prototype", _must_not_call, raising=False)
    monkeypatch.setattr(env.routes, "generate_prototype", _must_not_call, raising=False)

    resp = client.get("/v1/design-agent/brief-prototype-map", params={"brief_id": 6})
    assert resp.status_code == 200, resp.text
    entries = resp.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["prd_id"] == prd
    assert entries[0]["prototype"] is None
