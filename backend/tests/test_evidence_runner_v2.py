"""Tests for app.evidence_runner._run_sync_v2 — the v2 sample-build worker.

Mirrors test_evidence_runner.py; the async wrapper `generate_evidence_v2`
is the same `asyncio.to_thread + exception → fail_evidence` shim.
"""
from __future__ import annotations

import asyncio

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


# ---- happy path -------------------------------------------------------------

def test_run_sync_v2_happy_path_completes_evidence(
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
        evidence_runner, "call_md", lambda **kw: "# Final v2 markdown"
    )

    evidence_runner._run_sync_v2(evidence_id, brief_id, 0)

    row = db_mod.get_evidence(evidence_id)
    assert row["status"] == "ready"
    assert row["payload_md"] == "# Final v2 markdown"
    assert row["title"] == "Insight A"
    assert row["variant"] == "v2"


def test_run_sync_v2_passes_v2_template_and_system_prompt(
    isolated_settings, fake_llm, monkeypatch
):
    """Make sure v2 runs the v2 prompt + v2 template, not the v1 ones."""
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

    captured: dict = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return "# md"

    monkeypatch.setattr(evidence_runner, "call_md", _capture)
    evidence_runner._run_sync_v2(evidence_id, brief_id, 0)

    # v2 system prompt carries this exact phrase; v1 system prompt does not.
    assert "v2 format" in captured["system"]
    # v2 template carries this exact block; v1 template does not.
    assert ":::hero" in captured["user"]


def test_run_sync_v2_missing_brief_raises(isolated_settings):
    with pytest.raises(RuntimeError):
        evidence_runner._run_sync_v2(1, brief_id=9999, insight_index=0)


def test_run_sync_v2_out_of_range_insight_raises(isolated_settings):
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod, insights=[{"title": "only-one"}])
    with pytest.raises(RuntimeError):
        evidence_runner._run_sync_v2(1, brief_id=brief_id, insight_index=5)


def test_generate_evidence_v2_records_failure_in_db(
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

    monkeypatch.setattr(evidence_runner, "call_md", _boom)
    asyncio.run(evidence_runner.generate_evidence_v2(evidence_id, brief_id, 0))

    row = db_mod.get_evidence(evidence_id)
    assert row["status"] == "failed"
    assert "ValueError" in (row["error"] or "")
