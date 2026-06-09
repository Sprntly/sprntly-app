"""Contract tests for centralized apply-on-read of the PRD read-path (P3-17, F11).

Closes the F11 render-on-read loop: `apply_patches_to_prd_md` (P3-09) was DEFINED
but never called, so accepted (`status='applied'`) prd_patches persisted yet never
rendered into the PRD a reader sees. P3-17 routes every RENDER read through the new
`get_prd_rendered`, which folds applied patches at read time WITHOUT ever altering
`prds.payload_md` in the DB (derive-at-read; no denormalised rendered column).

These tests LOCK THAT IN:
  - the fold is applied (AC1) and ordered by created_at (AC1/AC10);
  - pending/rejected patches are NOT folded (AC1);
  - zero applied patches → byte-identical to raw get_prd (AC2 zero-blast-radius);
  - an applied patch is VISIBLE via get_prd_rendered but ABSENT via raw get_prd
    (AC6 lock-in — a future read-site that reverts to raw get_prd fails CI);
  - the underlying prds row is NEVER mutated by the render (AC5 never-ALTER-prds);
  - the export (AC7) and the agent's iterate user-message (AC8) both embed the
    rendered body via the same centralized fold.

`prd_patches` is seeded in the BASE conftest `_FAKE_SCHEMA` (test-harness only, NOT
a migration — the real migration ships from P3-09), so `list_applied_patches`
resolves under the base harness. We reload `app.db.prd_patches` + the consumer
modules in dependency order so their helpers bind to the fake-wired client.
"""
from __future__ import annotations

import asyncio
import importlib

import pytest

from tests.conftest import (
    _bearer_header,
    _enable_supabase_bearer,
    _seed_company_membership,
)

_UPDATES_HEADING = "## Design Agent updates"

# The company slug `_seed_company_membership` seeds for the default test user;
# the GET /v1/prd/{id} ownership chain (prd → brief → brief.dataset slug →
# company) resolves to this company when the brief's dataset equals this slug.
_OWNED_DATASET = "slug-co-test"


# ─── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def rendered_env(isolated_settings):
    """isolated_settings + the P3-17 consumer modules reloaded in dependency order
    so get_prd_rendered + its lazy prd_patches import bind to the fake-wired client.

    Returns the reloaded (app.db, app.db.prds, app.db.prd_patches, export, routes)
    modules. `prd_patches` is NOT in conftest's _RELOAD_ORDER, so we reload it here;
    the consumers re-bind their module-level get_prd_rendered against the reload.
    """
    import app.db.prd_patches as patches_mod
    importlib.reload(patches_mod)
    import app.db.prds as prds_mod
    importlib.reload(prds_mod)
    import app.db as db_mod
    importlib.reload(db_mod)
    import app.design_agent.export as export_mod
    importlib.reload(export_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    return db_mod, prds_mod, patches_mod, export_mod, routes_mod


@pytest.fixture
def owned_prd_client(rendered_env, isolated_settings, monkeypatch):
    """A Supabase-bearer-authed TestClient whose company OWNS the brief the test
    PRD hangs off, for the GET /v1/prd/{id} integration tests after the
    tenant-isolation fix (the route now gates on require_company + ownership via
    prd → brief → dataset-slug → company).

    Seeds the default company membership (slug `_OWNED_DATASET`) and a brief in
    that dataset, exposes the brief_id so the test can attach its PRD to it, and
    returns a client carrying the owning user's bearer token.
    """
    from types import SimpleNamespace

    from fastapi.testclient import TestClient
    import app.main as main_mod

    _enable_supabase_bearer(monkeypatch)
    _seed_company_membership(isolated_settings["supabase"])

    db_mod = rendered_env[0]
    brief_id = db_mod.save_brief(
        dataset=_OWNED_DATASET,
        week_label="Week 1",
        payload={"insights": [], "_schema_version": 1},
        schema_version=1,
    )
    client = TestClient(main_mod.app)
    client.headers.update(_bearer_header())
    return SimpleNamespace(client=client, brief_id=brief_id)


def _make_prd(db_mod, *, body: str = "# PRD body", brief_id: int = 1) -> int:
    """Create a ready v2 PRD with the given raw payload_md; return its id.

    `brief_id` defaults to 1 for the direct get_prd_rendered unit tests (which
    never hit the route gate). The route-integration tests pass a brief_id that
    is owned by the caller's company so require_owned_prd resolves.
    """
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=0, title="t", template_version=1, variant="v2"
    )
    db_mod.complete_prd(prd_id, title="t", md=body)
    return prd_id


def _seed_patch(
    *,
    prd_id: int,
    patch_md: str,
    status: str = "applied",
    rationale: str = "tighten the metric",
    prototype_id: int = 1,
    workspace_id: str = "app",
    created_at: str = "2026-01-01 00:00:00",
) -> int:
    """Insert one prd_patches row directly into the fake DB; return its id.

    Direct insert (mirrors test_design_agent_prd_patch_routes._seed_patch) — the
    same singleton connection backs the FakeSupabaseClient, so the row is visible
    to get_prd_rendered without a commit.
    """
    from tests import _fake_supabase

    cur = _fake_supabase.get_fake_db().execute(
        "INSERT INTO prd_patches "
        "(prd_id, prototype_id, workspace_id, rationale, patch_md, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [prd_id, prototype_id, workspace_id, rationale, patch_md, status, created_at],
    )
    return cur.lastrowid


# ─── fold / creation (AC1) ─────────────────────────────────────────────────


def test_get_prd_rendered_folds_applied_patch(rendered_env):
    # AC1 — an applied patch is folded under "## Design Agent updates".
    db_mod, *_ = rendered_env
    prd_id = _make_prd(db_mod, body="# PRD body")
    _seed_patch(prd_id=prd_id, patch_md="Activation within 7 days, not 30.")

    rendered = db_mod.get_prd_rendered(prd_id)
    assert _UPDATES_HEADING in rendered["payload_md"]
    assert "Activation within 7 days, not 30." in rendered["payload_md"]


def test_get_prd_rendered_ignores_pending_and_rejected(rendered_env):
    # AC1 — only 'applied' folds; pending/rejected are excluded.
    db_mod, *_ = rendered_env
    prd_id = _make_prd(db_mod, body="# PRD body")
    _seed_patch(prd_id=prd_id, patch_md="PENDING text", status="pending")
    _seed_patch(prd_id=prd_id, patch_md="REJECTED text", status="rejected")

    rendered = db_mod.get_prd_rendered(prd_id)
    assert rendered["payload_md"] == "# PRD body"          # nothing folded
    assert _UPDATES_HEADING not in rendered["payload_md"]


def test_get_prd_rendered_orders_patches_by_created_at(rendered_env):
    # AC1/AC10 — two applied patches fold in created_at order.
    db_mod, *_ = rendered_env
    prd_id = _make_prd(db_mod, body="# PRD body")
    _seed_patch(prd_id=prd_id, patch_md="SECOND", created_at="2026-01-02 00:00:00")
    _seed_patch(prd_id=prd_id, patch_md="FIRST", created_at="2026-01-01 00:00:00")

    body = db_mod.get_prd_rendered(prd_id)["payload_md"]
    assert body.index("FIRST") < body.index("SECOND")


# ─── zero blast radius (AC2) ───────────────────────────────────────────────


def test_get_prd_rendered_no_patches_byte_identical(rendered_env):
    # AC2 — no applied patches → payload_md byte-identical to raw get_prd.
    db_mod, *_ = rendered_env
    prd_id = _make_prd(db_mod, body="# PRD body")

    raw = db_mod.get_prd(prd_id)
    rendered = db_mod.get_prd_rendered(prd_id)
    assert rendered["payload_md"] == raw["payload_md"] == "# PRD body"


def test_get_prd_rendered_fast_path_returns_same_dict_shape(rendered_env):
    # AC2 — with no patches the fast path returns the raw row object unchanged;
    # same keys as get_prd.
    db_mod, *_ = rendered_env
    prd_id = _make_prd(db_mod, body="# PRD body")

    raw = db_mod.get_prd(prd_id)
    rendered = db_mod.get_prd_rendered(prd_id)
    assert set(rendered.keys()) == set(raw.keys())


# ─── retrieval / not-found ─────────────────────────────────────────────────


def test_get_prd_rendered_returns_none_when_missing(rendered_env):
    # Same contract as get_prd: non-existent id → None.
    db_mod, *_ = rendered_env
    assert db_mod.get_prd_rendered(999_999) is None


def test_list_applied_patches_excludes_pending_and_rejected(rendered_env):
    _, _, patches_mod, *_ = rendered_env
    db_mod = rendered_env[0]
    prd_id = _make_prd(db_mod)
    _seed_patch(prd_id=prd_id, patch_md="A", status="applied",
                created_at="2026-01-01 00:00:00")
    _seed_patch(prd_id=prd_id, patch_md="B", status="applied",
                created_at="2026-01-02 00:00:00")
    _seed_patch(prd_id=prd_id, patch_md="P", status="pending")
    _seed_patch(prd_id=prd_id, patch_md="R", status="rejected")

    rows = patches_mod.list_applied_patches(prd_id=prd_id)
    assert [r["patch_md"] for r in rows] == ["A", "B"]      # applied, created_at-asc
    assert all(r["status"] == "applied" for r in rows)


# ─── contract / property lock-in (AC5, AC6) ────────────────────────────────


def test_applied_patch_visible_through_rendered_not_raw(rendered_env):
    # AC6 — an applied patch IS visible via get_prd_rendered and ABSENT from raw
    # get_prd. The two reads are provably distinct when patches exist, so a
    # regression that reverts a render read-site to raw get_prd fails CI.
    db_mod, *_ = rendered_env
    prd_id = _make_prd(db_mod, body="# PRD body")
    _seed_patch(prd_id=prd_id, patch_md="FOLDED PATCH TEXT")

    raw = db_mod.get_prd(prd_id)
    rendered = db_mod.get_prd_rendered(prd_id)
    assert "FOLDED PATCH TEXT" in rendered["payload_md"]
    assert "FOLDED PATCH TEXT" not in raw["payload_md"]
    assert rendered["payload_md"] != raw["payload_md"]


def test_render_on_read_never_alters_prds(rendered_env):
    # AC5 — after get_prd_rendered, the underlying prds row's payload_md is
    # unchanged (a fresh raw re-read equals the original body).
    db_mod, *_ = rendered_env
    prd_id = _make_prd(db_mod, body="# PRD body")
    _seed_patch(prd_id=prd_id, patch_md="FOLDED PATCH TEXT")

    _ = db_mod.get_prd_rendered(prd_id)            # render once
    raw_after = db_mod.get_prd(prd_id)
    assert raw_after["payload_md"] == "# PRD body"  # canonical body untouched
    assert "FOLDED PATCH TEXT" not in raw_after["payload_md"]


def test_get_prd_rendered_does_not_mutate_returned_raw_on_fast_path(rendered_env):
    # AC5-adjacent — the fast path returns the raw row; a later applied patch must
    # not retroactively appear in a previously-returned no-patch render (no shared
    # mutation). Render before + after seeding a patch.
    db_mod, *_ = rendered_env
    prd_id = _make_prd(db_mod, body="# PRD body")
    before = db_mod.get_prd_rendered(prd_id)
    assert before["payload_md"] == "# PRD body"
    _seed_patch(prd_id=prd_id, patch_md="LATER PATCH")
    assert before["payload_md"] == "# PRD body"   # earlier result not mutated


# ─── determinism (AC10) ────────────────────────────────────────────────────


def test_get_prd_rendered_deterministic(rendered_env):
    # AC10 — two calls on the same PRD+patches return byte-identical payload_md.
    db_mod, *_ = rendered_env
    prd_id = _make_prd(db_mod, body="# PRD body")
    _seed_patch(prd_id=prd_id, patch_md="ONE", created_at="2026-01-01 00:00:00")
    _seed_patch(prd_id=prd_id, patch_md="TWO", created_at="2026-01-02 00:00:00")

    first = db_mod.get_prd_rendered(prd_id)["payload_md"]
    second = db_mod.get_prd_rendered(prd_id)["payload_md"]
    assert first == second


# ─── GET /v1/prd/{id} integration (AC3) ────────────────────────────────────


def test_get_prd_endpoint_returns_folded_payload(rendered_env, owned_prd_client):
    # AC3 — GET /v1/prd/{id} returns the folded payload when an applied patch
    # exists; response shape otherwise identical (same id/status/variant keys).
    # The PRD hangs off a brief owned by the caller's company so the route's
    # require_company + ownership gate resolves for the legitimate owner.
    db_mod, *_ = rendered_env
    prd_id = _make_prd(db_mod, body="# PRD body", brief_id=owned_prd_client.brief_id)
    _seed_patch(prd_id=prd_id, patch_md="ENDPOINT FOLDED PATCH")

    resp = owned_prd_client.client.get(f"/v1/prd/{prd_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == prd_id
    assert body["status"] == "ready"
    assert body["variant"] == "v2"
    assert _UPDATES_HEADING in body["payload_md"]
    assert "ENDPOINT FOLDED PATCH" in body["payload_md"]


def test_get_prd_endpoint_no_patches_returns_raw(rendered_env, owned_prd_client):
    # AC3 — no applied patches → raw body, shape unchanged.
    db_mod, *_ = rendered_env
    prd_id = _make_prd(db_mod, body="# PRD body", brief_id=owned_prd_client.brief_id)

    resp = owned_prd_client.client.get(f"/v1/prd/{prd_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["payload_md"] == "# PRD body"
    assert _UPDATES_HEADING not in body["payload_md"]


# ─── read-site routing: export (AC7) ───────────────────────────────────────


def test_export_embeds_rendered_prd(rendered_env, monkeypatch):
    # AC7 — render_export_markdown embeds the folded patch text (it now reads the
    # PRD via get_prd_rendered). Prototype/checkpoint/source reads are stubbed; the
    # PRD fold runs for real against the fake DB.
    db_mod, _prds, _patches, export_mod, _routes = rendered_env
    prd_id = _make_prd(db_mod, body="# PRD body")
    _seed_patch(prd_id=prd_id, patch_md="EXPORT FOLDED PATCH")

    monkeypatch.setattr(
        export_mod, "get_prototype",
        lambda **kw: {"id": 1, "prd_id": prd_id, "bundle_url": None},
    )
    monkeypatch.setattr(
        export_mod, "_get_checkpoint",
        lambda **kw: {"id": 1, "prototype_id": 1, "prompt_history": []},
    )

    async def _no_sources(prototype_id, checkpoint_id):  # noqa: ARG001
        return {}
    monkeypatch.setattr(export_mod, "_read_source_files", _no_sources)
    # P4-07: render_export_markdown now reads resolved comments (F16). Stub it out
    # here — this test asserts the PRD fold, not the new Resolved Feedback section.
    monkeypatch.setattr(export_mod, "list_resolved_comments", lambda **kw: [])

    md = asyncio.run(
        export_mod.render_export_markdown(1, 1, workspace_id="app")
    )
    assert "EXPORT FOLDED PATCH" in md
    assert "## PRD Reference" in md


# ─── read-site routing: iterate user-message (AC8) ─────────────────────────


def test_load_prd_body_returns_rendered(rendered_env):
    # AC8 — _load_prd_body returns the folded body (the agent's iterate
    # user-message reflects accepted patches).
    db_mod, _prds, _patches, _export, routes_mod = rendered_env
    prd_id = _make_prd(db_mod, body="# PRD body")
    _seed_patch(prd_id=prd_id, patch_md="ITERATE FOLDED PATCH")

    body = routes_mod._load_prd_body(prd_id)
    assert "ITERATE FOLDED PATCH" in body
    assert _UPDATES_HEADING in body


def test_load_prd_body_no_patches_returns_raw(rendered_env):
    # AC8 — no applied patches → raw body.
    db_mod, _prds, _patches, _export, routes_mod = rendered_env
    prd_id = _make_prd(db_mod, body="# PRD body")
    assert routes_mod._load_prd_body(prd_id) == "# PRD body"
