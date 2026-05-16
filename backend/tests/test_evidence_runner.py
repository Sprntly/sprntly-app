"""Tests for app.evidence_runner._run_sync — the inner generation worker.

We test the sync function directly to avoid asyncio plumbing. The async
wrapper `generate_evidence` is a thin `asyncio.to_thread(_run_sync, …)`
shim plus an exception → fail_evidence path.
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
    payload = {"summary_headline": "stub", "insights": insights, "_schema_version": 1}
    return db_mod.save_brief(
        dataset=dataset, week_label="Week of stub", payload=payload, schema_version=1
    )


# ---- happy path -------------------------------------------------------------

def test_run_sync_happy_path_completes_evidence(
    isolated_settings, fake_llm, monkeypatch
):
    """_run_sync should call the LLM, then mark the row 'ready' with the body."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    evidence_id = db_mod.start_evidence(
        brief_id=brief_id, insight_index=0, title="t", template_version=1
    )
    # The runner uses call_md (markdown), not call_json — patch it explicitly.
    monkeypatch.setattr(evidence_runner, "call_md", lambda **kw: "# Final markdown")

    evidence_runner._run_sync(evidence_id, brief_id, 0)

    row = db_mod.get_evidence(evidence_id)
    assert row["status"] == "ready"
    assert row["payload_md"] == "# Final markdown"
    assert row["title"] == "Insight A"


def test_run_sync_uses_fallback_title_when_insight_has_none(
    isolated_settings, monkeypatch
):
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod, insights=[{}])  # no title key
    evidence_id = db_mod.start_evidence(
        brief_id=brief_id, insight_index=0, title="placeholder", template_version=1
    )
    monkeypatch.setattr(evidence_runner, "call_md", lambda **kw: "# md")

    evidence_runner._run_sync(evidence_id, brief_id, 0)
    row = db_mod.get_evidence(evidence_id)
    assert row["title"] == "Insight #1"


# ---- error paths ------------------------------------------------------------

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
    """The async wrapper catches exceptions and marks the row 'failed'."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    evidence_id = db_mod.start_evidence(
        brief_id=brief_id, insight_index=0, title="t", template_version=1
    )

    def _boom(**_kw):
        raise ValueError("LLM exploded")

    monkeypatch.setattr(evidence_runner, "call_md", _boom)
    asyncio.run(evidence_runner.generate_evidence(evidence_id, brief_id, 0))

    row = db_mod.get_evidence(evidence_id)
    assert row["status"] == "failed"
    assert "ValueError" in (row["error"] or "")
