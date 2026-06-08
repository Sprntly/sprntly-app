"""Tests for the BRIEF_ENGINE rewire: weekly brief on KG synthesis.

Covers the synthesis-engine write path (/generate, /regenerate), the
seed-if-empty trigger, the legacy fallback flag, the scheduler synthesis cycle
with per-company error isolation, and the unchanged UI read path
(/current, /status, /{id}). LLM/gateway/run_synthesis are all mocked — no test
hits Anthropic or Supabase.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

import app.routes.brief as brief_routes
import app.scheduler as scheduler_mod
import app.synthesis_brief as sb


# ── helpers ───────────────────────────────────────────────────────────────

def _seed_company(db, *, company_id: str, slug: str) -> None:
    """Insert a companies row so slug↔company_id resolution works."""
    existing = db.table("companies").select("id").eq("id", company_id).execute().data
    if not existing:
        db.table("companies").insert(
            {"id": company_id, "slug": slug, "display_name": slug.title()}
        ).execute()


def _set_engine(monkeypatch, value: str) -> None:
    """Flip BRIEF_ENGINE on the live settings object the route + scheduler close
    over (no module reload needed — same pattern as the bearer-secret seam)."""
    monkeypatch.setattr(brief_routes.settings, "brief_engine", value, raising=False)
    monkeypatch.setattr(scheduler_mod.settings, "brief_engine", value, raising=False)


def _fake_synthesis_payload(dataset_slug: str) -> dict:
    return {
        "week_label": "Week of June 8, 2026",
        "summary_headline": "synthesis headline",
        "company": dataset_slug,
        "insights": [{"title": "X", "tag": "something_broken", "theme_id": "t1"}],
        "_generated_by": "synthesis_agent",
        "_schema_version": 1,
    }


# ── /generate — synthesis engine path ───────────────────────────────────────

def test_generate_synthesis_path_runs_run_synthesis(app_client, isolated_settings, monkeypatch):
    db = isolated_settings["supabase"]
    _seed_company(db, company_id="co-1", slug="acme")
    _set_engine(monkeypatch, "synthesis")

    def _fake_gen(slug):
        # mirror run_synthesis: persist into briefs, return payload
        payload = _fake_synthesis_payload("acme")
        isolated_settings["db"].save_brief("acme", payload["week_label"], payload,
                                           schema_version=1)
        return payload

    with patch.object(brief_routes, "generate_brief_for", side_effect=_fake_gen) as gen:
        r = app_client.post("/v1/brief/generate?dataset=acme")

    assert r.status_code == 200, r.text
    gen.assert_called_once_with("acme")
    body = r.json()
    assert body["summary_headline"] == "synthesis headline"
    # response preserves the {brief_id, **payload} contract
    assert isinstance(body["brief_id"], int)


def test_generate_synthesis_then_current_reads_back(app_client, isolated_settings, monkeypatch):
    db = isolated_settings["supabase"]
    _seed_company(db, company_id="co-1", slug="acme")
    _set_engine(monkeypatch, "synthesis")

    def _fake_gen(slug):
        payload = _fake_synthesis_payload("acme")
        isolated_settings["db"].save_brief("acme", payload["week_label"], payload, 1)
        return payload

    with patch.object(brief_routes, "generate_brief_for", side_effect=_fake_gen):
        app_client.post("/v1/brief/generate?dataset=acme")

    # UI read path unchanged: /current returns the saved synthesis brief
    r = app_client.get("/v1/brief/current?dataset=acme")
    assert r.status_code == 200
    assert r.json()["_generated_by"] == "synthesis_agent"


def test_generate_synthesis_unknown_company_returns_409(app_client, monkeypatch):
    _set_engine(monkeypatch, "synthesis")

    def _raise(slug):
        raise ValueError("No company for slug 'ghost'")

    with patch.object(brief_routes, "generate_brief_for", side_effect=_raise):
        r = app_client.post("/v1/brief/generate?dataset=ghost")
    assert r.status_code == 409


# ── /regenerate — synthesis engine path ─────────────────────────────────────

def test_regenerate_synthesis_path_starts_synthesis_bg(app_client, isolated_settings, monkeypatch):
    _seed_company(isolated_settings["supabase"], company_id="co-1", slug="acme")
    _set_engine(monkeypatch, "synthesis")
    seen: list[str] = []

    async def _fake_bg(dataset):
        seen.append(dataset)

    # patch the synthesis bg runner; assert /regenerate routes to it (not legacy)
    with patch.object(brief_routes, "_synthesis_generate_bg", side_effect=_fake_bg), \
         patch.object(brief_routes, "auto_generate_brief") as legacy:
        r = app_client.post("/v1/brief/regenerate?dataset=acme")

    assert r.status_code == 200
    assert r.json() == {"started": True, "dataset": "acme"}
    legacy.assert_not_called()


def test_synthesis_bg_runner_invokes_generate_brief_for(isolated_settings, monkeypatch):
    _set_engine(monkeypatch, "synthesis")
    with patch.object(brief_routes, "generate_brief_for") as gen:
        asyncio.run(brief_routes._synthesis_generate_bg("acme"))
    gen.assert_called_once_with("acme")


def test_synthesis_bg_runner_swallows_errors(isolated_settings, monkeypatch):
    # fire-and-forget: a failing synthesis must not raise out of the bg task
    with patch.object(brief_routes, "generate_brief_for", side_effect=RuntimeError("boom")):
        asyncio.run(brief_routes._synthesis_generate_bg("acme"))  # no raise


# ── seed-if-empty logic ─────────────────────────────────────────────────────

def test_seed_if_empty_triggers_only_when_kg_empty(isolated_settings):
    from app.graph.facade import GraphFacade

    facade = GraphFacade()
    # KG empty → seed runs (corpus + connectors invoked)
    with patch.object(facade, "active_signals", return_value=[]), \
         patch.object(sb, "_seed_from_corpus", return_value={"docs": 1}) as corpus, \
         patch.object(sb, "_seed_from_connectors", return_value={"providers": 0}) as conn:
        out = sb.seed_if_empty(facade, "co-1", "acme")
    assert out is not None
    corpus.assert_called_once()
    conn.assert_called_once()


def test_seed_if_empty_skips_when_kg_populated(isolated_settings):
    from app.graph.facade import GraphFacade
    from app.graph.types import Signal

    facade = GraphFacade()
    sig = Signal(enterprise_id="co-1", source_type="revenue", kind="x", content="y")
    with patch.object(facade, "active_signals", return_value=[sig]), \
         patch.object(sb, "_seed_from_corpus") as corpus, \
         patch.object(sb, "_seed_from_connectors") as conn:
        out = sb.seed_if_empty(facade, "co-1", "acme")
    assert out is None
    corpus.assert_not_called()
    conn.assert_not_called()


def test_generate_brief_for_seeds_then_runs_synthesis(isolated_settings):
    _seed_company(isolated_settings["supabase"], company_id="co-1", slug="acme")
    with patch.object(sb, "seed_if_empty", return_value={"corpus": {}}) as seed, \
         patch.object(sb, "run_synthesis", return_value={"summary_headline": "ok"}) as run:
        out = sb.generate_brief_for("acme")
    assert out["summary_headline"] == "ok"
    seed.assert_called_once()
    # run_synthesis called with the resolved company_id + slug
    _, kwargs = run.call_args[0], run.call_args[1]
    assert run.call_args[0][1] == "co-1"
    assert kwargs["dataset_slug"] == "acme"


def test_generate_brief_for_resolves_slug_to_company_id(isolated_settings):
    _seed_company(isolated_settings["supabase"], company_id="co-xyz", slug="globex")
    cid, slug = sb.resolve_company("globex")
    assert cid == "co-xyz" and slug == "globex"


def test_generate_brief_for_unknown_slug_raises(isolated_settings):
    with pytest.raises(ValueError):
        sb.resolve_company("nope-not-here")


def test_seed_from_corpus_is_bounded(isolated_settings, monkeypatch):
    """Seeding caps corpus docs at MAX_SEED_DOCS so it can't hang the request."""
    from app.graph.facade import GraphFacade

    class _Doc:
        def __init__(self, n):
            self.name, self.text = f"d{n}", "x"

    class _Corpus:
        docs = [_Doc(i) for i in range(sb.MAX_SEED_DOCS + 10)]

    facade = GraphFacade()
    calls: list[str] = []
    with patch.object(sb, "load_corpus", return_value=_Corpus()), \
         patch.object(sb, "extract_document",
                      side_effect=lambda *a, **k: calls.append(k["doc_name"]) or
                      {"signals": 1, "themes": 0, "skipped": 0}):
        sb._seed_from_corpus(facade, "co-1", "acme")
    assert len(calls) == sb.MAX_SEED_DOCS


def test_seed_from_corpus_isolates_bad_doc(isolated_settings):
    """One doc that raises during extraction must not abort the whole seed."""
    from app.graph.facade import GraphFacade

    class _Doc:
        def __init__(self, n):
            self.name, self.text = f"d{n}", "x"

    class _Corpus:
        docs = [_Doc(0), _Doc(1)]

    def _extract(*a, **k):
        if k["doc_name"] == "d0":
            raise RuntimeError("bad doc")
        return {"signals": 2, "themes": 1, "skipped": 0}

    facade = GraphFacade()
    with patch.object(sb, "load_corpus", return_value=_Corpus()), \
         patch.object(sb, "extract_document", side_effect=_extract):
        out = sb._seed_from_corpus(facade, "co-1", "acme")
    assert out["docs"] == 1 and out["signals"] == 2  # only the good doc counted


# ── legacy fallback flag ─────────────────────────────────────────────────────

def test_generate_legacy_path_calls_auto_generate_brief_engine(app_client, isolated_settings, monkeypatch):
    # /generate legacy path goes through call_json (the placeholder brief).
    _set_engine(monkeypatch, "legacy")
    # legacy /generate needs a corpus on disk
    data_dir = isolated_settings["data_dir"]
    (data_dir / "acme").mkdir()
    (data_dir / "acme" / "notes.md").write_text("some corpus", encoding="utf-8")

    with patch.object(brief_routes, "generate_brief_for") as synth_gen:
        r = app_client.post("/v1/brief/generate?dataset=acme")

    assert r.status_code == 200, r.text
    synth_gen.assert_not_called()  # legacy path must NOT touch synthesis engine


def test_regenerate_legacy_path_calls_auto_generate_brief(app_client, monkeypatch):
    _set_engine(monkeypatch, "legacy")

    async def _noop(dataset):
        return None

    with patch.object(brief_routes, "auto_generate_brief", side_effect=_noop) as legacy, \
         patch.object(brief_routes, "_synthesis_generate_bg") as synth_bg:
        r = app_client.post("/v1/brief/regenerate?dataset=acme")

    assert r.status_code == 200
    legacy.assert_called_once_with("acme")
    synth_bg.assert_not_called()


# ── scheduler synthesis cycle ────────────────────────────────────────────────

def test_scheduler_synthesis_iterates_companies(isolated_settings, monkeypatch):
    db = isolated_settings["supabase"]
    _seed_company(db, company_id="co-1", slug="acme")
    _seed_company(db, company_id="co-2", slug="globex")
    _set_engine(monkeypatch, "synthesis")

    seen: list[str] = []
    with patch.object(scheduler_mod, "_run_pipeline_for_all_datasets") as legacy:
        with patch("app.synthesis_brief.generate_brief_for",
                   side_effect=lambda slug: seen.append(slug)):
            asyncio.run(scheduler_mod._run_scheduled_cycle())
    assert sorted(seen) == ["acme", "globex"]
    legacy.assert_not_called()


def test_scheduler_synthesis_isolates_per_company_failure(isolated_settings, monkeypatch):
    db = isolated_settings["supabase"]
    _seed_company(db, company_id="co-1", slug="acme")
    _seed_company(db, company_id="co-2", slug="globex")
    _seed_company(db, company_id="co-3", slug="initech")
    _set_engine(monkeypatch, "synthesis")

    seen: list[str] = []

    def _gen(slug):
        if slug == "globex":
            raise RuntimeError("synthesis blew up for globex")
        seen.append(slug)

    with patch("app.synthesis_brief.generate_brief_for", side_effect=_gen):
        asyncio.run(scheduler_mod._run_synthesis_for_all_companies())  # no raise
    # the failing company is skipped; the others still ran
    assert sorted(seen) == ["acme", "initech"]


def test_scheduler_legacy_path_runs_pipeline(isolated_settings, monkeypatch):
    _set_engine(monkeypatch, "legacy")
    with patch.object(scheduler_mod, "_run_synthesis_for_all_companies") as synth, \
         patch.object(scheduler_mod, "_run_pipeline_for_all_datasets") as legacy:
        asyncio.run(scheduler_mod._run_scheduled_cycle())
    legacy.assert_called_once()
    synth.assert_not_called()


def test_scheduler_synthesis_no_companies_is_noop(isolated_settings, monkeypatch):
    _set_engine(monkeypatch, "synthesis")
    with patch("app.synthesis_brief.generate_brief_for") as gen:
        asyncio.run(scheduler_mod._run_synthesis_for_all_companies())
    gen.assert_not_called()


# ── UI read path unchanged ───────────────────────────────────────────────────

def test_read_endpoints_unchanged_under_synthesis(app_client, isolated_settings, monkeypatch):
    _set_engine(monkeypatch, "synthesis")
    db = isolated_settings["db"]
    brief_id = db.save_brief("acme", "Week 1", {"insights": [], "_generated_by": "synthesis_agent"},
                             schema_version=1)

    # /current
    r = app_client.get("/v1/brief/current?dataset=acme")
    assert r.status_code == 200 and r.json()["id"] == brief_id
    # /{id}
    r = app_client.get(f"/v1/brief/{brief_id}")
    assert r.status_code == 200 and r.json()["id"] == brief_id
    # /status — ready once a brief exists
    r = app_client.get("/v1/brief/status?dataset=acme")
    assert r.status_code == 200 and r.json()["status"] == "ready"
