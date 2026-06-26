"""Tests for app.evidence_runner._run_sync — the inner generation worker.

We test the sync function directly to avoid asyncio plumbing. The async
wrapper `generate_evidence` is a thin `asyncio.to_thread(_run_sync, …)`
shim plus an exception → fail_evidence path.
"""
from __future__ import annotations

import asyncio

import pytest

from app import evidence_runner
from app.graph.gateway import LLMResult


def _llm_result(output, model="claude-sonnet-4-6", prompt_version="evidence-html-v1"):
    return LLMResult(
        output=output, model=model, prompt_version=prompt_version,
        input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.001, latency_ms=5,
        stop_reason="end_turn",
    )


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


# ---- happy path -------------------------------------------------------------

def test_run_sync_happy_path_completes_evidence(
    isolated_settings, fake_llm, monkeypatch
):
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    evidence_id = db_mod.start_evidence(
        brief_id=brief_id,
        insight_index=0,
        title="t",
        template_version=1,
        variant="v2",
    )
    monkeypatch.setattr(
        evidence_runner, "llm_call",
        lambda **kw: _llm_result('<p class="eyebrow">Evidence Brief</p><h1>X</h1>'),
    )

    evidence_runner._run_sync(evidence_id, brief_id, 0)

    row = db_mod.get_evidence(evidence_id)
    assert row["status"] == "ready"
    assert row["payload_md"] == '<p class="eyebrow">Evidence Brief</p><h1>X</h1>'
    assert row["title"] == "Insight A"
    assert row["variant"] == "v2"


def test_run_sync_binds_evidence_brief_skill_and_emits_html(
    isolated_settings, fake_llm, monkeypatch
):
    """The fallback runner generates through the gateway with the evidence-brief
    skill bound, on the HTML system prompt (its native visual output)."""
    _seed_corpus(isolated_settings["data_dir"], body="UNIQUE_CORPUS_MARK")
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    evidence_id = db_mod.start_evidence(
        brief_id=brief_id,
        insight_index=0,
        title="t",
        template_version=1,
        variant="v2",
    )

    captured: dict = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return _llm_result("<h1>brief</h1>")

    monkeypatch.setattr(evidence_runner, "llm_call", _capture)
    evidence_runner._run_sync(evidence_id, brief_id, 0)

    assert captured["skill"] == "evidence-brief"
    assert captured["agent"] == "evidence"
    # HTML system prompt: emit body HTML, not `:::` blocks.
    assert "visual HTML brief" in captured["system"]
    assert ":::hero" not in captured["system"]
    # The corpus grounding is inlined into the input.
    assert "UNIQUE_CORPUS_MARK" in captured["input"]


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
        brief_id=brief_id,
        insight_index=0,
        title="t",
        template_version=1,
        variant="v2",
    )

    def _boom(**_kw):
        raise ValueError("LLM exploded")

    monkeypatch.setattr(evidence_runner, "llm_call", _boom)
    asyncio.run(evidence_runner.generate_evidence(evidence_id, brief_id, 0))

    row = db_mod.get_evidence(evidence_id)
    assert row["status"] == "failed"
    assert "ValueError" in (row["error"] or "")
