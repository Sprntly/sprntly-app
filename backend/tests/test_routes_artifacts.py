"""Tests for GET /v1/artifacts — the All-Chats "Artifacts" tab listing.

Covers:
  - tenant scoping (caller sees only their own dataset; 404 on an unowned slug)
  - unified shape across all three artifact types (prd / prototype / evidence)
  - recency sort (newest first)
  - prototype title derived from the parent PRD
  - empty result for a company with no artifacts

Mirrors the fixture style of test_routes_connectors.py: `company_client`
gives a JWT-authed TestClient with a seeded company + membership; we add the
`prototypes` table on top of conftest's base fake schema (which already has
briefs/prds/evidences) and seed rows directly through the fake Supabase client.
"""
from __future__ import annotations

import json

import pytest

from tests import _fake_supabase
from tests._company_helpers import company_client, seed_company, supabase_bearer

# SQLite translation of supabase/migrations/20260528000000_design_agent_prototypes.sql
# (the columns this route reads + the workspace_id scope). Only `prototypes` is
# needed here; the route never touches prototype_checkpoints.
_PROTOTYPE_DDL = """
CREATE TABLE IF NOT EXISTS prototypes (
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
    current_checkpoint_id  INTEGER,
    error                  TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at           TEXT
);
"""


@pytest.fixture
def artifacts_env(isolated_settings):
    """Add the prototypes table to conftest's already-reset fake DB. briefs /
    prds / evidences are present in the base schema, so no extra DDL for them."""
    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    yield


def _client(monkeypatch, *, slug: str = "acme"):
    """A company-scoped TestClient for `slug`. Returns the ctx namespace."""
    return company_client(monkeypatch)


# ─── Seed helpers (write directly through the fake Supabase client) ──────────


def _seed_brief(*, dataset: str, week_label: str) -> int:
    from app.db.client import require_client
    resp = require_client().table("briefs").insert({
        "dataset": dataset,
        "week_label": week_label,
        "payload": json.dumps({}),
        "is_current": True,
    }).execute()
    return resp.data[0]["id"]


def _seed_prd(*, brief_id: int, title: str, insight_index: int = 0,
              status: str = "ready", generated_at: str | None = None) -> int:
    from app.db.client import require_client
    row = {
        "brief_id": brief_id,
        "insight_index": insight_index,
        "title": title,
        "status": status,
    }
    if generated_at is not None:
        row["generated_at"] = generated_at
    resp = require_client().table("prds").insert(row).execute()
    return resp.data[0]["id"]


def _seed_evidence(*, brief_id: int, title: str, insight_index: int = 0,
                   status: str = "ready", generated_at: str | None = None) -> int:
    from app.db.client import require_client
    row = {
        "brief_id": brief_id,
        "insight_index": insight_index,
        "title": title,
        "status": status,
    }
    if generated_at is not None:
        row["generated_at"] = generated_at
    resp = require_client().table("evidences").insert(row).execute()
    return resp.data[0]["id"]


def _seed_prototype(*, prd_id: int, workspace_id: str, status: str = "ready",
                    created_at: str | None = None) -> int:
    from app.db.client import require_client
    row = {
        "prd_id": prd_id,
        "workspace_id": workspace_id,
        "status": status,
        "template_version": 1,
    }
    if created_at is not None:
        row["created_at"] = created_at
    resp = require_client().table("prototypes").insert(row).execute()
    return resp.data[0]["id"]


# ─── Tests ───────────────────────────────────────────────────────────────────


def test_requires_auth(unauth_client, artifacts_env):
    r = unauth_client.get("/v1/artifacts", params={"dataset": "acme"})
    assert r.status_code == 401


def test_empty_for_company_with_no_artifacts(artifacts_env, monkeypatch):
    ctx = _client(monkeypatch)
    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    assert r.status_code == 200
    assert r.json() == {"artifacts": []}


def test_404_on_unowned_dataset(artifacts_env, monkeypatch):
    # The caller owns "acme" (seeded by company_client). A different slug that
    # maps to no company (or another company) must 404 — never leak.
    ctx = _client(monkeypatch)
    r = ctx.client.get("/v1/artifacts", params={"dataset": "someone-else"})
    assert r.status_code == 404


def test_tenant_scoping_only_own_artifacts(artifacts_env, monkeypatch):
    ctx = _client(monkeypatch)  # owns "acme", workspace_id == ctx.company_id

    # A second, foreign company with its own brief + PRD + prototype.
    other_company_id = seed_company(user_id="intruder", slug="rival")
    other_brief = _seed_brief(dataset="rival", week_label="Wk Rival")
    _seed_prd(brief_id=other_brief, title="Rival PRD")
    _seed_prototype(prd_id=999, workspace_id=other_company_id)

    # The caller's own artifacts under "acme".
    my_brief = _seed_brief(dataset="acme", week_label="Wk 24")
    _seed_prd(brief_id=my_brief, title="My PRD")

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    assert r.status_code == 200
    titles = [a["title"] for a in r.json()["artifacts"]]
    assert "My PRD" in titles
    assert "Rival PRD" not in titles
    # The rival prototype (scoped by the rival's company UUID) is excluded.
    assert all(a["type"] != "prototype" for a in r.json()["artifacts"])

    # And the rival cannot read acme's artifacts.
    rival_headers = supabase_bearer("intruder")
    rr = ctx.client.get(
        "/v1/artifacts", params={"dataset": "acme"}, headers=rival_headers,
    )
    assert rr.status_code == 404


def test_prd_list_dedups_to_latest_generation(artifacts_env, monkeypatch):
    # Each PRD regeneration is a new prds row sharing (brief_id, insight_index).
    # The artifacts list must show only the LATEST generation per logical PRD.
    ctx = _client(monkeypatch)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 24")
    _seed_prd(brief_id=brief_id, title="KG Timeout PRD", insight_index=0,
              generated_at="2026-06-15T01:00:00+00:00")
    _seed_prd(brief_id=brief_id, title="KG Timeout PRD", insight_index=0,
              generated_at="2026-06-15T02:00:00+00:00")
    latest = _seed_prd(brief_id=brief_id, title="KG Timeout PRD", insight_index=0,
                       generated_at="2026-06-15T03:00:00+00:00")
    # A different insight is a different logical PRD → its own entry.
    _seed_prd(brief_id=brief_id, title="Pricing PRD", insight_index=1,
              generated_at="2026-06-14T00:00:00+00:00")

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    assert r.status_code == 200
    prds = [a for a in r.json()["artifacts"] if a["type"] == "prd"]
    assert len(prds) == 2  # latest KG Timeout + Pricing — not all 4 rows
    kg = [a for a in prds if a["title"] == "KG Timeout PRD"]
    assert len(kg) == 1
    assert kg[0]["id"] == latest  # the newest generation wins


def test_unified_shape_all_three_types(artifacts_env, monkeypatch):
    ctx = _client(monkeypatch)
    brief_id = _seed_brief(dataset="acme", week_label="Week of May 20")
    prd_id = _seed_prd(brief_id=brief_id, title="Handoff PRD", insight_index=2)
    _seed_evidence(brief_id=brief_id, title="Retention Evidence", insight_index=1)
    _seed_prototype(prd_id=prd_id, workspace_id=ctx.company_id)

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    assert r.status_code == 200
    items = r.json()["artifacts"]
    by_type = {it["type"]: it for it in items}
    assert set(by_type) == {"prd", "prototype", "evidence"}

    prd = by_type["prd"]
    assert prd["id"] == prd_id
    assert prd["title"] == "Handoff PRD"
    assert prd["status"] == "ready"
    assert prd["created_at"]
    assert prd["source"] == {
        "brief_id": brief_id, "week_label": "Week of May 20", "insight_index": 2,
    }
    assert prd["open"] == {
        "brief_id": brief_id, "insight_index": 2, "prd_id": prd_id,
    }

    ev = by_type["evidence"]
    assert ev["title"] == "Retention Evidence"
    assert ev["source"]["week_label"] == "Week of May 20"
    assert ev["open"]["evidence_id"] == ev["id"]
    assert ev["open"]["brief_id"] == brief_id

    proto = by_type["prototype"]
    assert proto["open"]["prd_id"] == prd_id
    assert proto["open"]["prototype_id"] == proto["id"]
    assert proto["source"]["prd_id"] == prd_id


def test_prototype_title_derived_from_parent_prd(artifacts_env, monkeypatch):
    ctx = _client(monkeypatch)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 24")
    prd_id = _seed_prd(brief_id=brief_id, title="Parent PRD Title")
    _seed_prototype(prd_id=prd_id, workspace_id=ctx.company_id)

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    proto = next(a for a in r.json()["artifacts"] if a["type"] == "prototype")
    assert proto["title"] == "Parent PRD Title"
    assert proto["source"]["prd_title"] == "Parent PRD Title"


def test_recency_sort_newest_first(artifacts_env, monkeypatch):
    ctx = _client(monkeypatch)
    brief_id = _seed_brief(dataset="acme", week_label="Wk 24")
    # Three artifacts with explicit, distinct timestamps.
    _seed_prd(brief_id=brief_id, title="Oldest",
              generated_at="2026-05-01T00:00:00+00:00")
    _seed_evidence(brief_id=brief_id, title="Middle",
                   generated_at="2026-05-15T00:00:00+00:00")
    prd_newest = _seed_prd(brief_id=brief_id, title="Newest",
                           generated_at="2026-06-01T00:00:00+00:00")
    _seed_prototype(prd_id=prd_newest, workspace_id=ctx.company_id,
                    created_at="2026-06-10T00:00:00+00:00")

    r = ctx.client.get("/v1/artifacts", params={"dataset": "acme"})
    titles = [a["title"] for a in r.json()["artifacts"]]
    # Prototype (Jun 10) → Newest PRD (Jun 1) → Middle (May 15) → Oldest (May 1).
    assert titles == ["Newest", "Newest", "Middle", "Oldest"]
    # First item is the prototype (newest created_at).
    assert r.json()["artifacts"][0]["type"] == "prototype"
