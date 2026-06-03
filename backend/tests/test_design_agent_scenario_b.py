"""Tests for P5-02 — Scenario B detection in F4 + the manual color/font floor.

Covers:
  * request-body wiring (`website_url` / `manual_design` persisted + threaded)
  * scenario derivation at the route boundary (B / 0 / A) via the label passed
    to `generate_prototype` (the same value the runner emits as `scenario=…` in
    the cost-summary log line)
  * `_website_context_block` precedence (extracted > manual > url-only > None)
  * the transparent / zero-alpha color floor (`_is_usable_color`) — the P5-01
    verifier finding that Stripe/Linear return `rgba(0,0,0,0)` as their primary

Harness mirrors test_design_agent_routes.py: isolated_settings + the prototypes
tables on the in-memory FakeSupabaseClient, with app.db.prototypes →
app.routes.design_agent → app.main reloaded in dependency order. `runner.py` is
NOT reloaded (it is scenario-agnostic and reloading it pollutes RunResult
isinstance under the full suite); `generate_prototype` is stubbed on the routes
module so no real LLM/Playwright call fires.
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

# SQLite-compatible translation of the P1-06 prototypes migration (identical to
# test_design_agent_routes.py — the fake exercises SQL semantics, not PG DDL).
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
    current_checkpoint_id  INTEGER,
    error                  TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at           TEXT
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

_WEBSITE_MOD = "app.design_agent.scenarios.website"


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """isolated_settings + prototypes tables + feature flag ON, with the design
    agent module stack reloaded in dependency order. Returns the live modules."""
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
    return SimpleNamespace(proto=proto_mod, routes=routes_mod, main=main_mod, db=db_mod)


# ─── helpers ────────────────────────────────────────────────────────────────


def _seed_prd(db_mod, body: str = "# PRD body") -> int:
    prd_id = db_mod.start_prd(
        brief_id=1, insight_index=0, title="t", template_version=1, variant="v2"
    )
    db_mod.complete_prd(prd_id, title="t", md=body)
    return prd_id


def _stub_generate(monkeypatch, routes_mod, *, status="complete", iters=1, virtual_fs=None):
    """Patch routes.generate_prototype; return the captured-kwargs list so a test
    can assert the `scenario` label the runner would log."""
    calls: list[dict] = []

    async def _fake(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status=status, iters=iters), (virtual_fs or {})

    monkeypatch.setattr(routes_mod, "generate_prototype", _fake)
    return calls


def _extractor_returns(value):
    async def _fake(url):  # noqa: ARG001 — signature-compatible stub
        return value
    return _fake


def _extractor_raises():
    async def _fake(url):  # noqa: ARG001
        raise AssertionError("extractor must not be called when Figma is present")
    return _fake


async def _drain_inflight(routes_mod) -> None:
    for _ in range(1000):
        if not routes_mod._inflight_tasks:
            break
        await asyncio.sleep(0)


# A representative extractor result with a transparent primary color but valid
# typography/logo — exactly the Stripe/Linear shape the P5-01 verifier found.
def _transparent_color_ds() -> dict:
    return {
        "primary_color": "rgba(0,0,0,0)",
        "background_color": "rgba(0,0,0,0)",
        "heading_font_family": "Inter",
        "heading_size_scale": "48px",
        "body_font_family": "Roboto",
        "border_radius_convention": "8px",
        "spacing_scale_samples": ["16px 24px"],
        "logo_url": "https://cdn.example.com/logo.png",
    }


def _good_ds() -> dict:
    return {
        "primary_color": "#3b82f6",
        "background_color": "#ffffff",
        "heading_font_family": "Inter",
        "heading_size_scale": "48px",
        "body_font_family": "Inter",
        "border_radius_convention": "8px",
        "spacing_scale_samples": ["16px"],
        "logo_url": "https://cdn.example.com/logo.png",
    }


# ─── Scenario derivation at the route boundary (AC1, AC2, AC3, AC10) ─────────


async def test_generate_with_website_url_derives_scenario_b(env, monkeypatch):
    """AC1 — website URL + no Figma persists website_url and derives scenario B."""
    calls = _stub_generate(monkeypatch, env.routes)
    monkeypatch.setattr(_WEBSITE_MOD + ".extract_website_design_system", _extractor_returns(None))
    prd_id = _seed_prd(env.db)

    req = env.routes.GenerateRequest(prd_id=prd_id, website_url="https://example.com")
    resp = await env.routes.generate(body=req, session={"aud": "app"})
    await _drain_inflight(env.routes)

    row = env.proto.get_prototype(prototype_id=resp.prototype_id, workspace_id="app")
    assert row["website_url"] == "https://example.com"
    assert calls[0]["scenario"] == "B"


async def test_generate_with_no_source_derives_scenario_0(env, monkeypatch):
    """AC2 — neither Figma nor website leaves website_url NULL and derives 0."""
    calls = _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db)

    req = env.routes.GenerateRequest(prd_id=prd_id)
    resp = await env.routes.generate(body=req, session={"aud": "app"})
    await _drain_inflight(env.routes)

    row = env.proto.get_prototype(prototype_id=resp.prototype_id, workspace_id="app")
    assert row["website_url"] is None
    assert calls[0]["scenario"] == "0"


async def test_generate_with_figma_derives_a_even_with_website(env, monkeypatch):
    """AC3 — Figma wins over a co-supplied website; the extractor is never called."""
    calls = _stub_generate(monkeypatch, env.routes)
    monkeypatch.setattr(_WEBSITE_MOD + ".extract_website_design_system", _extractor_raises())
    prd_id = _seed_prd(env.db)

    req = env.routes.GenerateRequest(
        prd_id=prd_id, figma_file_key="FILEKEY", website_url="https://example.com"
    )
    resp = await env.routes.generate(body=req, session={"aud": "app"})
    await _drain_inflight(env.routes)

    assert calls[0]["scenario"] == "A"  # extractor would have raised if consulted


async def test_manual_only_no_url_is_scenario_0_with_hints(env, monkeypatch):
    """AC10 — manual_design + no website + no Figma → scenario 0, but the hints
    STILL reach the scaffold (decision 2026-06-02)."""
    calls = _stub_generate(monkeypatch, env.routes)
    prd_id = _seed_prd(env.db)
    md = env.routes.ManualDesignInput(primary_color="#ff8800", font_family="Poppins")

    req = env.routes.GenerateRequest(prd_id=prd_id, manual_design=md)
    resp = await env.routes.generate(body=req, session={"aud": "app"})
    await _drain_inflight(env.routes)

    assert calls[0]["scenario"] == "0"
    block = await env.routes._website_context_block(None, md)
    assert block is not None
    assert "#ff8800" in block
    assert "Poppins" in block


# ─── _website_context_block precedence (AC4, AC10) ───────────────────────────


async def test_website_context_block_uses_extractor_result(env, monkeypatch):
    """AC4 — extractor returns a usable dict → its fields appear in the prose."""
    monkeypatch.setattr(_WEBSITE_MOD + ".extract_website_design_system", _extractor_returns(_good_ds()))
    block = await env.routes._website_context_block("https://example.com", None)
    assert block is not None
    assert "#3b82f6" in block
    assert "Inter" in block
    assert "https://cdn.example.com/logo.png" in block


async def test_website_context_block_falls_back_to_manual(env, monkeypatch):
    """AC4 — extractor None + manual present → manual color/font prose."""
    monkeypatch.setattr(_WEBSITE_MOD + ".extract_website_design_system", _extractor_returns(None))
    md = env.routes.ManualDesignInput(primary_color="#abcdef", font_family="Lato")
    block = await env.routes._website_context_block("https://example.com", md)
    assert block is not None
    assert "#abcdef" in block
    assert "Lato" in block


async def test_website_context_block_url_only_neutral(env, monkeypatch):
    """AC4 — extractor None, no manual, URL given → neutral instruction + host."""
    monkeypatch.setattr(_WEBSITE_MOD + ".extract_website_design_system", _extractor_returns(None))
    block = await env.routes._website_context_block("https://example.com/pricing", None)
    assert block is not None
    assert "neutral" in block.lower()
    assert "example.com" in block


async def test_website_context_block_none_when_no_url(env):
    """AC4 — neither website_url nor manual_design → None (caller falls back)."""
    assert await env.routes._website_context_block(None, None) is None


async def test_website_context_block_manual_only_no_url(env):
    """AC4/AC10 — no website_url but manual present → manual prose, not None."""
    md = env.routes.ManualDesignInput(primary_color="#123456", font_family="Roboto")
    block = await env.routes._website_context_block(None, md)
    assert block is not None
    assert "#123456" in block
    assert "Roboto" in block


# ─── Manual-floor independence from P5-01 (AC5) ──────────────────────────────


async def test_manual_floor_survives_missing_p5_01(env, monkeypatch):
    """AC5 — if P5-01 has not merged, the lazy `from … import
    extract_website_design_system` raises ImportError; the helper catches it and
    falls through to the manual hints (no crash)."""
    # A stand-in module that lacks the symbol → `from … import X` is ImportError.
    fake_mod = types.ModuleType(_WEBSITE_MOD)
    monkeypatch.setitem(sys.modules, _WEBSITE_MOD, fake_mod)
    md = env.routes.ManualDesignInput(primary_color="#0f0f0f", font_family="Mono")

    block = await env.routes._website_context_block("https://example.com", md)
    assert block is not None
    assert "#0f0f0f" in block
    assert "Mono" in block


# ─── Non-breakage (AC8) ──────────────────────────────────────────────────────


def test_generate_routes_still_compile_unchanged(env):
    """AC8 — module py_compiles and the new request fields default to None."""
    import py_compile

    py_compile.compile(env.routes.__file__, doraise=True)
    req = env.routes.GenerateRequest(prd_id=1)
    assert req.website_url is None
    assert req.manual_design is None


# ─── Transparent / zero-alpha color floor (AC11) ─────────────────────────────


def test_is_usable_color_rejects_transparent(env):
    """AC11 — transparent / zero-alpha / empty / None are unusable; real colors usable."""
    u = env.routes._is_usable_color
    for bad in ["rgba(0,0,0,0)", "rgba(0, 0, 0, 0)", "hsla(0,0%,0%,0)", "transparent", "", "   "]:
        assert u(bad) is False, bad
    assert u(None) is False
    for good in ["#3b82f6", "rgb(8,9,10)", "rgba(0,0,0,0.5)", "hsla(120,50%,50%,1)"]:
        assert u(good) is True, good


async def test_context_block_transparent_extracted_color_falls_to_manual(env, monkeypatch):
    """AC11 — transparent extracted primary + valid font + manual present: prose
    uses the manual color, KEEPS the extracted font/logo, no transparent value."""
    monkeypatch.setattr(
        _WEBSITE_MOD + ".extract_website_design_system",
        _extractor_returns(_transparent_color_ds()),
    )
    md = env.routes.ManualDesignInput(primary_color="#ff0000", font_family="Lato")
    block = await env.routes._website_context_block("https://example.com", md)

    assert "rgba(0,0,0,0)" not in block
    assert "transparent" not in block.lower()
    assert "#ff0000" in block                       # manual color used
    assert "Inter" in block                         # extracted heading font KEPT
    assert "https://cdn.example.com/logo.png" in block  # extracted logo KEPT


async def test_context_block_transparent_extracted_color_neutral_when_no_manual(env, monkeypatch):
    """AC11 — transparent extracted primary + valid font + NO manual: neutral-color
    instruction + extracted font, no transparent value in the prose."""
    monkeypatch.setattr(
        _WEBSITE_MOD + ".extract_website_design_system",
        _extractor_returns(_transparent_color_ds()),
    )
    block = await env.routes._website_context_block("https://example.com", None)

    assert "rgba(0,0,0,0)" not in block
    assert "transparent" not in block.lower()
    assert "neutral" in block.lower()
    assert "Inter" in block  # extracted heading font KEPT
