"""Tests for the pipeline KG-refresh + brief stages on the synthesis engine.

The bug: a "Run pipeline" run drove the LEGACY engine (stage 4 →
``app.knowledge_graph.refresh_graph`` building a throwaway networkx graph;
stage 5 → ``app.brief_runner.auto_generate_brief``), while live serving uses
``BRIEF_ENGINE=synthesis`` — so a pipeline run never ingested newly-uploaded
docs into the synthesis KG nor regenerated the synthesis brief the UI reads.

These tests pin the fix: under ``synthesis`` stage 4 runs ``seed_incremental``
(NOT ``refresh_graph``) and stage 5 runs ``generate_brief_for`` (NOT
``auto_generate_brief``), with ``EmptyKnowledgeGraphError`` mapping to a benign
``skipped`` status; under ``legacy`` the old behaviour is preserved. All
LLM/synthesis/seed work is patched out — no test hits Anthropic or Supabase.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

import app.pipeline as pipeline_mod
from app.synthesis.agent import EmptyKnowledgeGraphError


def _set_engine(monkeypatch, value: str) -> None:
    """Flip BRIEF_ENGINE on the live settings object pipeline closes over."""
    monkeypatch.setattr(pipeline_mod.settings, "brief_engine", value, raising=False)


_SEED_SUMMARY = {
    "corpus": {"docs": 2, "signals": 5, "themes": 1, "skipped": 0, "unchanged": 3},
    "connectors": None,
    "was_empty": False,
}


# ── Stage 4: knowledge graph ─────────────────────────────────────────────────


def test_stage_kg_synthesis_calls_seed_incremental_not_refresh(monkeypatch):
    """Under synthesis: stage 4 resolves company_id+slug and calls
    seed_incremental, never the legacy refresh_graph."""
    _set_engine(monkeypatch, "synthesis")
    captured: dict = {}

    def _fake_seed(facade, company_id, slug):
        captured["company_id"] = company_id
        captured["slug"] = slug
        return _SEED_SUMMARY

    with patch("app.graph.facade.GraphFacade"), \
         patch("app.synthesis_brief.resolve_company",
               return_value=("co-abc", "acme")), \
         patch("app.synthesis_brief.seed_incremental",
               side_effect=_fake_seed) as seed, \
         patch("app.knowledge_graph.refresh_graph") as legacy:
        result = asyncio.run(pipeline_mod._stage_knowledge_graph("acme"))

    seed.assert_called_once()
    legacy.assert_not_called()
    assert captured == {"company_id": "co-abc", "slug": "acme"}
    assert result["status"] == "completed"
    assert result["engine"] == "synthesis"
    assert result["seed"] == _SEED_SUMMARY
    assert "duration_s" in result


def test_stage_kg_synthesis_error_wrapped(monkeypatch):
    """A real failure in the synthesis seed still surfaces as status=error
    (the existing try/except wrapper is preserved)."""
    _set_engine(monkeypatch, "synthesis")
    with patch("app.synthesis_brief.resolve_company",
               side_effect=ValueError("No company for slug 'ghost'")):
        result = asyncio.run(pipeline_mod._stage_knowledge_graph("ghost"))

    assert result["status"] == "error"
    assert "ghost" in result["error"]


def test_stage_kg_legacy_calls_refresh_graph(monkeypatch):
    """Under legacy: stage 4 keeps calling refresh_graph, never the synthesis
    seed."""
    _set_engine(monkeypatch, "legacy")
    with patch("app.knowledge_graph.refresh_graph",
               return_value={"entities": 9}) as legacy, \
         patch("app.synthesis_brief.seed_incremental") as seed:
        result = asyncio.run(pipeline_mod._stage_knowledge_graph("acme"))

    legacy.assert_called_once_with("acme")
    seed.assert_not_called()
    assert result["status"] == "completed"
    assert result["entities"] == 9
    assert "engine" not in result  # legacy path doesn't tag an engine


# ── Stage 5: brief generation ────────────────────────────────────────────────


def test_stage_brief_synthesis_calls_generate_brief_for_not_legacy(monkeypatch):
    """Under synthesis: stage 5 calls generate_brief_for, never the legacy
    auto_generate_brief."""
    _set_engine(monkeypatch, "synthesis")
    seen: list[str] = []

    with patch("app.synthesis_brief.generate_brief_for",
               side_effect=lambda d: seen.append(d) or {"summary_headline": "ok"}) as gen, \
         patch("app.brief_runner.auto_generate_brief") as legacy:
        result = asyncio.run(pipeline_mod._stage_brief_generation("acme"))

    gen.assert_called_once_with("acme")
    legacy.assert_not_called()
    assert seen == ["acme"]
    assert result["status"] == "completed"
    assert result["engine"] == "synthesis"
    assert "duration_s" in result


def test_stage_brief_synthesis_empty_kg_maps_to_skipped(monkeypatch):
    """A company with no data after seeding is NOT a pipeline failure:
    EmptyKnowledgeGraphError → status=skipped (not error), no propagation."""
    _set_engine(monkeypatch, "synthesis")

    def _boom(dataset):
        raise EmptyKnowledgeGraphError("no themes with signals yet")

    with patch("app.synthesis_brief.generate_brief_for", side_effect=_boom):
        result = asyncio.run(pipeline_mod._stage_brief_generation("acme"))

    assert result["status"] == "skipped"
    assert result["engine"] == "synthesis"
    assert "connect a source or upload files" in result["reason"]
    assert "error" not in result


def test_stage_brief_synthesis_real_failure_is_error(monkeypatch):
    """A genuine (non-empty-KG) failure still surfaces as status=error."""
    _set_engine(monkeypatch, "synthesis")
    with patch("app.synthesis_brief.generate_brief_for",
               side_effect=RuntimeError("synthesis blew up")):
        result = asyncio.run(pipeline_mod._stage_brief_generation("acme"))

    assert result["status"] == "error"
    assert "synthesis blew up" in result["error"]


def test_stage_brief_legacy_calls_auto_generate_brief(monkeypatch):
    """Under legacy: stage 5 keeps calling auto_generate_brief, never the
    synthesis path."""
    _set_engine(monkeypatch, "legacy")

    async def _noop(dataset):
        return None

    with patch("app.brief_runner.auto_generate_brief",
               side_effect=_noop) as legacy, \
         patch("app.synthesis_brief.generate_brief_for") as gen:
        result = asyncio.run(pipeline_mod._stage_brief_generation("acme"))

    legacy.assert_called_once_with("acme")
    gen.assert_not_called()
    assert result["status"] == "completed"
    assert "engine" not in result


# ── run_full_pipeline-level dispatch ─────────────────────────────────────────


def test_full_pipeline_dispatches_synthesis_callables(monkeypatch):
    """End-to-end: under synthesis, run_full_pipeline's stages 4/5 reach the
    synthesis callables and NOT the legacy ones. Stages 1-3 and the audit
    helpers are stubbed so the test stays fast and offline."""
    _set_engine(monkeypatch, "synthesis")

    # Neutralise the audit-log writes + earlier stages.
    monkeypatch.setattr(pipeline_mod, "create_run", lambda *a, **k: 0)
    monkeypatch.setattr(pipeline_mod, "update_run_stage", lambda *a, **k: None)
    monkeypatch.setattr(pipeline_mod, "complete_run", lambda *a, **k: None)
    monkeypatch.setattr(pipeline_mod, "fail_run", lambda *a, **k: None)

    async def _ok(dataset):
        return {"status": "completed"}

    monkeypatch.setattr(pipeline_mod, "_stage_sync_connectors", _ok)
    monkeypatch.setattr(pipeline_mod, "_stage_agents", _ok)
    monkeypatch.setattr(pipeline_mod, "_stage_ds_agent", _ok)

    with patch("app.graph.facade.GraphFacade"), \
         patch("app.synthesis_brief.resolve_company",
               return_value=("co-1", "acme")), \
         patch("app.synthesis_brief.seed_incremental",
               return_value=_SEED_SUMMARY) as seed, \
         patch("app.synthesis_brief.generate_brief_for",
               return_value={"summary_headline": "ok"}) as gen, \
         patch("app.knowledge_graph.refresh_graph") as legacy_kg, \
         patch("app.brief_runner.auto_generate_brief") as legacy_brief:
        result = asyncio.run(pipeline_mod.run_full_pipeline("acme"))

    assert result["status"] == "completed"
    seed.assert_called_once()
    gen.assert_called_once_with("acme")
    legacy_kg.assert_not_called()
    legacy_brief.assert_not_called()
    assert result["stages"]["knowledge_graph"]["engine"] == "synthesis"
    assert result["stages"]["brief"]["engine"] == "synthesis"
