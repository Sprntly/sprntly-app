"""Tests for the onboarding-website design-source fallback.

Two layers are covered:

  * ``app.db.products.get_company_website`` — the read-only helper that returns a
    company's product website (primary-first, then most-recent non-empty),
    scoped to the caller's company, identifiers-only in logs.
  * The generate route's design-source precedence — Figma → typed website →
    onboarding product website → none — and the threading of the resolved value
    into the prototype snapshot row + background generation task.

Harness mirrors test_design_agent_scenario_b.py: ``isolated_settings`` resets the
in-memory FakeSupabaseClient (whose base schema already seeds a ``products``
table), and the route layer reloads app.db.prototypes → app.routes.design_agent →
app.main in dependency order with ``generate_prototype`` stubbed so no real
LLM/Playwright call fires.

Note on ordering under the fake: the production query orders by ``is_primary``
descending and then ``created_at`` descending. The in-memory fake applies a
single ORDER BY (the last one wins), so each ordering test below seeds
``created_at`` values that keep the asserted row first under BOTH a primary-first
order (real Postgres) and a most-recent order (the fake).
"""
from __future__ import annotations

import asyncio
import importlib
import logging
from types import SimpleNamespace

import pytest

from app.auth import CompanyContext
from tests.conftest import _TEST_COMPANY_ID, _TEST_USER_ID

# SQLite-compatible translation of the prototypes migration (identical to the
# other Design Agent route suites — the fake exercises SQL semantics, not PG DDL).
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
    created_by_user_id     TEXT,
    bundle_url             TEXT,
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


# ─── seeding helpers ──────────────────────────────────────────────────────────


def _seed_company(db, company_id: str) -> None:
    """Insert a companies row so the products FK is satisfied (existence-guarded)."""
    existing = db.table("companies").select("id").eq("id", company_id).execute().data
    if not existing:
        db.table("companies").insert(
            {
                "id": company_id,
                "slug": f"slug-{company_id}",
                "display_name": company_id.title(),
            }
        ).execute()


def _seed_product(
    db,
    company_id: str,
    *,
    pid: str,
    website: str | None = None,
    is_primary: bool = False,
    created_at: str | None = None,
    name: str = "Product",
) -> None:
    """Insert a products row for the given company. ``website`` omitted → NULL."""
    _seed_company(db, company_id)
    row: dict = {
        "id": pid,
        "company_id": company_id,
        "name": name,
        "is_primary": 1 if is_primary else 0,
    }
    if website is not None:
        row["website"] = website
    if created_at is not None:
        row["created_at"] = created_at
    db.table("products").insert(row).execute()


# ─── db helper fixture + tests ────────────────────────────────────────────────


@pytest.fixture
def products(isolated_settings):
    """Reload app.db.products against the freshly-reloaded db client so its
    require_client() resolves the patched in-memory fake, and expose the fake
    client for seeding. Returns (module, fake_client)."""
    import app.db.products as products_mod

    importlib.reload(products_mod)
    return SimpleNamespace(mod=products_mod, db=isolated_settings["supabase"])


def test_get_company_website_prefers_primary(products):
    """The primary product's website is returned over a non-primary's.

    The primary is also seeded as the most-recent row so the assertion holds
    under both a primary-first order and a most-recent order (see module note)."""
    db = products.db
    _seed_product(
        db,
        "co-x",
        pid="p-secondary",
        website="https://secondary.example",
        is_primary=False,
        created_at="2026-01-01T00:00:00",
    )
    _seed_product(
        db,
        "co-x",
        pid="p-primary",
        website="https://primary.example",
        is_primary=True,
        created_at="2026-03-01T00:00:00",
    )

    result = products.mod.get_company_website("co-x")
    assert result == "https://primary.example"
    assert result != "https://secondary.example"


def test_get_company_website_falls_back_to_recent(products):
    """When the primary product has no website, the most-recent product with a
    non-empty website is returned."""
    db = products.db
    _seed_product(
        db,
        "co-y",
        pid="p-primary-empty",
        website=None,
        is_primary=True,
        created_at="2026-01-01T00:00:00",
    )
    _seed_product(
        db,
        "co-y",
        pid="p-older",
        website="https://older.example",
        is_primary=False,
        created_at="2026-02-01T00:00:00",
    )
    _seed_product(
        db,
        "co-y",
        pid="p-newer",
        website="https://newer.example",
        is_primary=False,
        created_at="2026-03-01T00:00:00",
    )

    assert products.mod.get_company_website("co-y") == "https://newer.example"


def test_get_company_website_returns_none_when_no_product(products):
    """A company with no usable product website yields None (no exception)."""
    _seed_company(products.db, "co-empty")
    assert products.mod.get_company_website("co-empty") is None
    # Empty/missing company id short-circuits to None as well.
    assert products.mod.get_company_website("") is None


def test_get_company_website_filters_by_company_id(products):
    """The read is scoped to the caller's company — it never returns another
    company's product website."""
    db = products.db
    _seed_product(
        db, "co-a", pid="p-a", website="https://a-co.example", is_primary=True
    )
    _seed_product(
        db, "co-b", pid="p-b", website="https://b-co.example", is_primary=True
    )

    assert products.mod.get_company_website("co-a") == "https://a-co.example"
    assert products.mod.get_company_website("co-b") == "https://b-co.example"
    # Company A's read must never surface company B's website.
    assert products.mod.get_company_website("co-a") != "https://b-co.example"


# ─── route precedence fixture + helpers ───────────────────────────────────────


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """isolated_settings + prototypes tables + feature flag ON, with the design
    agent module stack reloaded in dependency order. Returns the live modules
    plus the in-memory fake client for seeding."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)

    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    import app.db.prototypes as proto_mod

    importlib.reload(proto_mod)
    import app.routes.design_agent as routes_mod

    importlib.reload(routes_mod)
    import app.main as main_mod

    importlib.reload(main_mod)

    import app.db as db_mod

    return SimpleNamespace(
        proto=proto_mod,
        routes=routes_mod,
        main=main_mod,
        db=db_mod,
        supabase=isolated_settings["supabase"],
    )


def _seed_prd(db_mod, body: str = "# PRD body") -> int:
    prd_id = db_mod.start_prd(
        brief_id=1, insight_index=0, title="t", template_version=1, variant="v2"
    )
    db_mod.complete_prd(prd_id, title="t", md=body)
    return prd_id


_WEBSITE_MOD = "app.design_agent.scenarios.website"


def _stub_generate(monkeypatch, routes_mod):
    """Patch routes.generate_prototype; return the captured-kwargs list so a test
    can assert the scenario label the runner would derive."""
    calls: list[dict] = []

    async def _fake(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status="complete", iters=1), {}

    monkeypatch.setattr(routes_mod, "generate_prototype", _fake)
    return calls


def _stub_extractor(monkeypatch, value=None):
    """Stub the website design-system extractor so the background task never makes
    a real network/Playwright call when a website URL is in play."""

    async def _fake(url):  # noqa: ARG001 — signature-compatible stub
        return value

    monkeypatch.setattr(_WEBSITE_MOD + ".extract_website_design_system", _fake)


def _company_ctx() -> CompanyContext:
    return CompanyContext(
        company_id=_TEST_COMPANY_ID, role="owner", user_id=_TEST_USER_ID
    )


async def _drain_inflight(routes_mod) -> None:
    for _ in range(1000):
        if not routes_mod._inflight_tasks:
            break
        await asyncio.sleep(0)


# ─── route precedence tests ───────────────────────────────────────────────────


async def test_fallback_fires_when_no_figma_no_typed_url(env, monkeypatch):
    """No Figma + no typed website + a primary product website → the product
    website becomes the effective design source, threaded into the snapshot row
    and the generation task (which derives website-scenario from it)."""
    calls = _stub_generate(monkeypatch, env.routes)
    _stub_extractor(monkeypatch)
    _seed_product(
        env.supabase,
        _TEST_COMPANY_ID,
        pid="p-onboarding",
        website="https://onboarding.example",
        is_primary=True,
    )
    prd_id = _seed_prd(env.db)

    req = env.routes.GenerateRequest(prd_id=prd_id)
    resp = await env.routes.generate(body=req, company=_company_ctx())
    await _drain_inflight(env.routes)

    row = env.proto.get_prototype(
        prototype_id=resp.prototype_id, workspace_id=_TEST_COMPANY_ID
    )
    assert row["website_url"] == "https://onboarding.example"
    # website present, no figma → the generation task derives the website scenario.
    assert calls[0]["scenario"] == "B"


async def test_fallback_never_overrides_typed_url(env, monkeypatch):
    """A typed website URL takes precedence — the onboarding read is not even
    consulted, and the typed value is what gets threaded through."""
    calls = _stub_generate(monkeypatch, env.routes)
    _stub_extractor(monkeypatch)

    def _boom(*_a, **_k):
        raise AssertionError("get_company_website must not be consulted")

    monkeypatch.setattr(env.routes, "get_company_website", _boom)
    _seed_product(
        env.supabase,
        _TEST_COMPANY_ID,
        pid="p-onboarding",
        website="https://onboarding.example",
        is_primary=True,
    )
    prd_id = _seed_prd(env.db)

    req = env.routes.GenerateRequest(
        prd_id=prd_id, website_url="https://typed.example"
    )
    resp = await env.routes.generate(body=req, company=_company_ctx())
    await _drain_inflight(env.routes)

    row = env.proto.get_prototype(
        prototype_id=resp.prototype_id, workspace_id=_TEST_COMPANY_ID
    )
    assert row["website_url"] == "https://typed.example"
    assert calls[0]["scenario"] == "B"


async def test_fallback_skipped_when_figma_present(env, monkeypatch):
    """A connected Figma file wins — the onboarding read is not consulted and
    the fallback never fires."""
    calls = _stub_generate(monkeypatch, env.routes)
    _stub_extractor(monkeypatch)

    def _boom(*_a, **_k):
        raise AssertionError("get_company_website must not be consulted")

    monkeypatch.setattr(env.routes, "get_company_website", _boom)
    _seed_product(
        env.supabase,
        _TEST_COMPANY_ID,
        pid="p-onboarding",
        website="https://onboarding.example",
        is_primary=True,
    )
    prd_id = _seed_prd(env.db)

    req = env.routes.GenerateRequest(prd_id=prd_id, figma_file_key="FILEKEY")
    resp = await env.routes.generate(body=req, company=_company_ctx())
    await _drain_inflight(env.routes)

    row = env.proto.get_prototype(
        prototype_id=resp.prototype_id, workspace_id=_TEST_COMPANY_ID
    )
    assert row["website_url"] is None
    assert calls[0]["scenario"] == "A"


async def test_fallback_logs_host_only(env, monkeypatch, caplog):
    """The fallback log line carries identifiers only — company id, prd id, and
    the host — never the full URL (no path, no query string)."""
    _stub_generate(monkeypatch, env.routes)
    _stub_extractor(monkeypatch)
    _seed_product(
        env.supabase,
        _TEST_COMPANY_ID,
        pid="p-onboarding",
        website="https://plotline.studio/brand/guidelines?ref=secret",
        is_primary=True,
    )
    prd_id = _seed_prd(env.db)

    req = env.routes.GenerateRequest(prd_id=prd_id)
    with caplog.at_level(logging.INFO, logger="app.routes.design_agent"):
        await env.routes.generate(body=req, company=_company_ctx())
        await _drain_inflight(env.routes)

    fallback_lines = [
        r.getMessage()
        for r in caplog.records
        if "design_agent_website_fallback" in r.getMessage()
    ]
    assert len(fallback_lines) == 1
    line = fallback_lines[0]
    assert "plotline.studio" in line          # host only
    assert _TEST_COMPANY_ID in line           # company identifier
    assert f"prd_id={prd_id}" in line         # prd identifier
    assert "/brand/guidelines" not in line    # no path
    assert "secret" not in line               # no query string
