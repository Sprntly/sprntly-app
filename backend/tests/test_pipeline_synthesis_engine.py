"""Tests for the pipeline KG-refresh + brief stages — synthesis is the only engine.

History: a "Run pipeline" run once drove a LEGACY engine (stage 4 →
``app.knowledge_graph.refresh_graph`` building a throwaway networkx graph;
stage 5 → ``app.brief_runner.auto_generate_brief``), while live serving used
the synthesis engine — so a pipeline run never ingested newly-uploaded docs
into the synthesis KG nor regenerated the synthesis brief the UI reads. The
legacy brief/KG engine has since been retired: synthesis is now the ONLY path.

These tests pin that synthesis is unconditional: stage 4 always runs
``seed_incremental`` and stage 5 always runs ``generate_brief_for``, with
``EmptyKnowledgeGraphError`` mapping to a benign ``skipped`` status. All
LLM/synthesis/seed work is patched out — no test hits Anthropic or Supabase.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import app.pipeline as pipeline_mod
from app.synthesis.agent import EmptyKnowledgeGraphError


_SEED_SUMMARY = {
    "corpus": {"docs": 2, "signals": 5, "themes": 1, "skipped": 0, "unchanged": 3},
    "connectors": None,
    "was_empty": False,
}


# ── Stage 4: knowledge graph ─────────────────────────────────────────────────


def test_stage_kg_calls_seed_incremental():
    """Stage 4 resolves company_id+slug and always calls seed_incremental."""
    captured: dict = {}

    def _fake_seed(facade, company_id, slug):
        captured["company_id"] = company_id
        captured["slug"] = slug
        return _SEED_SUMMARY

    with patch("app.graph.facade.GraphFacade"), \
         patch("app.synthesis_brief.resolve_company",
               return_value=("co-abc", "acme")), \
         patch("app.synthesis_brief.seed_incremental",
               side_effect=_fake_seed) as seed:
        result = asyncio.run(pipeline_mod._stage_knowledge_graph("acme"))

    seed.assert_called_once()
    assert captured == {"company_id": "co-abc", "slug": "acme"}
    assert result["status"] == "completed"
    assert result["engine"] == "synthesis"
    assert result["seed"] == _SEED_SUMMARY
    assert "duration_s" in result


def test_stage_kg_error_wrapped():
    """A real failure in the synthesis seed still surfaces as status=error
    (the existing try/except wrapper is preserved)."""
    with patch("app.synthesis_brief.resolve_company",
               side_effect=ValueError("No company for slug 'ghost'")):
        result = asyncio.run(pipeline_mod._stage_knowledge_graph("ghost"))

    assert result["status"] == "error"
    assert "ghost" in result["error"]


# ── Stage 5: brief generation ────────────────────────────────────────────────


def test_stage_brief_calls_generate_brief_for():
    """Stage 5 always calls generate_brief_for (the synthesis path)."""
    seen: list[str] = []

    with patch("app.synthesis_brief.generate_brief_for",
               side_effect=lambda d: seen.append(d) or {"summary_headline": "ok"}) as gen, \
         patch("app.brief_runner.warm_synthesis_drilldowns"):
        result = asyncio.run(pipeline_mod._stage_brief_generation("acme"))

    gen.assert_called_once_with("acme")
    assert seen == ["acme"]
    assert result["status"] == "completed"
    assert result["engine"] == "synthesis"
    assert "duration_s" in result


def test_stage_brief_warms_drilldowns_after_success():
    """A pipeline-generated brief must auto-warm its drill-downs (PRDs for all 3
    insights + evidence + Ask) — same as the regenerate route — so the
    "Run pipeline" flow also auto-generates the brief's PRDs."""
    with patch("app.synthesis_brief.generate_brief_for",
               return_value={"summary_headline": "ok"}), \
         patch("app.brief_runner.warm_synthesis_drilldowns") as warm:
        result = asyncio.run(pipeline_mod._stage_brief_generation("acme"))

    assert result["status"] == "completed"
    warm.assert_called_once_with("acme")


def test_stage_brief_does_not_warm_on_empty_kg():
    """No brief → no warming (don't warm a brief that wasn't generated)."""
    from app.synthesis.agent import EmptyKnowledgeGraphError

    with patch("app.synthesis_brief.generate_brief_for",
               side_effect=EmptyKnowledgeGraphError("empty")), \
         patch("app.brief_runner.warm_synthesis_drilldowns") as warm:
        result = asyncio.run(pipeline_mod._stage_brief_generation("acme"))

    assert result["status"] == "skipped"
    warm.assert_not_called()


def test_stage_brief_empty_kg_maps_to_skipped():
    """A company with no data after seeding is NOT a pipeline failure:
    EmptyKnowledgeGraphError → status=skipped (not error), no propagation."""

    def _boom(dataset):
        raise EmptyKnowledgeGraphError("no themes with signals yet")

    with patch("app.synthesis_brief.generate_brief_for", side_effect=_boom):
        result = asyncio.run(pipeline_mod._stage_brief_generation("acme"))

    assert result["status"] == "skipped"
    assert result["engine"] == "synthesis"
    assert "connect a source or upload files" in result["reason"]
    assert "error" not in result


def test_stage_brief_real_failure_is_error():
    """A genuine (non-empty-KG) failure still surfaces as status=error."""
    with patch("app.synthesis_brief.generate_brief_for",
               side_effect=RuntimeError("synthesis blew up")):
        result = asyncio.run(pipeline_mod._stage_brief_generation("acme"))

    assert result["status"] == "error"
    assert "synthesis blew up" in result["error"]


# ── run_full_pipeline-level dispatch ─────────────────────────────────────────


def test_full_pipeline_dispatches_synthesis_callables(monkeypatch):
    """End-to-end: run_full_pipeline's stages 4/5 reach the synthesis callables.
    Stages 1-3 and the audit helpers are stubbed so the test stays fast and
    offline."""
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
               return_value={"summary_headline": "ok"}) as gen:
        result = asyncio.run(pipeline_mod.run_full_pipeline("acme"))

    assert result["status"] == "completed"
    seed.assert_called_once()
    gen.assert_called_once_with("acme")
    assert result["stages"]["knowledge_graph"]["engine"] == "synthesis"
    assert result["stages"]["brief"]["engine"] == "synthesis"
