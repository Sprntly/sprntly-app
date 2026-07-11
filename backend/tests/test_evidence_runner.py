"""Tests for app.evidence_runner._run_sync — the CORPUS-FALLBACK generation
worker (the path app.evidence_kg defers to when an insight has no KG backing).

It now emits the `evidence-brief` skill's self-contained HTML visual brief
(variant v3), grounded on the corpus instead of the KG evidence trail. We test
the sync function directly to avoid asyncio plumbing; the async wrapper
`generate_evidence` is a thin `asyncio.to_thread(_run_sync, …)` shim plus an
exception → fail_evidence path.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app import evidence_runner


def _seed_corpus(data_dir, dataset="asurion", body="some corpus body"):
    ds = data_dir / dataset
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text(body)


def _seed_brief(db_mod, dataset="asurion", insights=None):
    if insights is None:
        insights = [{"title": "Insight A", "subtitle": "behaviour"}]
    payload = {
        "summary_headline": "stub",
        "insights": insights,
        "_schema_version": 1,
    }
    return db_mod.save_brief(
        dataset=dataset, week_label="Week of stub", payload=payload, schema_version=1
    )


def _patch_gateway(monkeypatch, html='<div class="wrap"><h1>Corpus brief</h1></div>'):
    """Patch the gateway + company resolution so _run_sync emits `html`.
    Returns the dict of captured llm_call kwargs."""
    monkeypatch.setattr(evidence_runner, "resolve_company",
                        lambda ds: ("ent-1", ds))
    captured: dict = {}

    def _llm(**kw):
        captured.update(kw)
        return SimpleNamespace(output=html)

    monkeypatch.setattr(evidence_runner, "llm_call", _llm)
    return captured


# ---- happy path -------------------------------------------------------------

def test_run_sync_happy_path_completes_with_html_brief(
    isolated_settings, monkeypatch
):
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    evidence_id = db_mod.start_evidence(
        brief_id=brief_id, insight_index=0, title="t",
        template_version=4, variant="v3",
    )
    _patch_gateway(monkeypatch, '<div class="wrap"><h1>Corpus brief</h1></div>')

    evidence_runner._run_sync(evidence_id, brief_id, 0)

    row = db_mod.get_evidence(evidence_id)
    assert row["status"] == "ready"
    # Model body preserved; canonical stylesheet injected server-side (Phase 2).
    assert '<div class="wrap"><h1>Corpus brief</h1></div>' in row["payload_md"]
    assert "--problem:#dd4b32" in row["payload_md"]
    assert row["payload_md"].count("<style>") == 1
    assert row["title"] == "Insight A"
    assert ":::" not in row["payload_md"]   # HTML, not the retired :::block doc


def test_run_sync_binds_skill_and_grounds_on_corpus(
    isolated_settings, monkeypatch
):
    """The fallback binds the `evidence-brief` skill (METHOD + HTML contract)
    and feeds the corpus as the grounding data — no :::block template."""
    _seed_corpus(isolated_settings["data_dir"], body="distinct corpus marker")
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    evidence_id = db_mod.start_evidence(
        brief_id=brief_id, insight_index=0, title="t",
        template_version=4, variant="v3",
    )
    captured = _patch_gateway(monkeypatch)

    evidence_runner._run_sync(evidence_id, brief_id, 0)

    assert captured["skill"] == "evidence-brief"
    assert captured["agent"] == "evidence"
    # Corpus is the grounding; the HTML system prompt steers self-contained HTML.
    assert "distinct corpus marker" in captured["input"]
    assert ":::hero" not in captured["input"]
    assert "HTML" in captured["system"] and "self-contained" in captured["system"]


def test_run_sync_missing_brief_raises(isolated_settings):
    with pytest.raises(RuntimeError):
        evidence_runner._run_sync(1, brief_id=9999, insight_index=0)


def test_run_sync_out_of_range_insight_raises(isolated_settings):
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod, insights=[{"title": "only-one"}])
    with pytest.raises(RuntimeError):
        evidence_runner._run_sync(1, brief_id=brief_id, insight_index=5)


def test_generate_evidence_records_failure_in_db(
    isolated_settings, monkeypatch
):
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    evidence_id = db_mod.start_evidence(
        brief_id=brief_id, insight_index=0, title="t",
        template_version=4, variant="v3",
    )
    monkeypatch.setattr(evidence_runner, "resolve_company",
                        lambda ds: ("ent-1", ds))

    def _boom(**_kw):
        raise ValueError("LLM exploded")

    monkeypatch.setattr(evidence_runner, "llm_call", _boom)
    asyncio.run(evidence_runner.generate_evidence(evidence_id, brief_id, 0))

    row = db_mod.get_evidence(evidence_id)
    assert row["status"] == "failed"
    assert "ValueError" in (row["error"] or "")
