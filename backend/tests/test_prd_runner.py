"""Tests for app.prd_runner._run_sync — same shape as evidence_runner tests.

New rows are written with variant='v2' by the route; the runner itself
doesn't touch variant — it just produces markdown and calls
`complete_prd` against the existing row.
"""
from __future__ import annotations

import asyncio

import pytest

from app import prd_runner


def _seed_corpus(data_dir, dataset="asurion", body="corpus body"):
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
    # Datasets are now validated by the PRD route; register the slug so
    # the runner has a real company row to attribute the generation to.
    db_mod.insert_dataset(slug=dataset, display_name=dataset.title())
    return db_mod.save_brief(
        dataset=dataset, week_label="Week of stub", payload=payload, schema_version=1
    )


# Minimal v2 PRD that satisfies _validate_required_blocks. Tests that
# don't care about content use this as the `call_md` return value.
_VALID_PRD_MD = (
    "# Stub PRD\n\n"
    ':::problem\n{"user_story": "A user tries x", "impact": []}\n:::\n\n'
    ':::requirements\n[{"behavior": "x"}]\n:::\n\n'
    ':::acceptance-criteria\n[{"id": "AC1"}]\n:::\n'
)


def test_run_sync_happy_path_completes_prd(
    isolated_settings, fake_llm, monkeypatch
):
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = db_mod.start_prd(
        brief_id=brief_id,
        insight_index=0,
        title="t",
        template_version=1,
        variant="v2",
    )
    monkeypatch.setattr(prd_runner, "call_md", lambda **kw: _VALID_PRD_MD)

    prd_runner._run_sync(prd_id, brief_id, 0)

    row = db_mod.get_prd(prd_id)
    assert row["status"] == "ready"
    assert row["payload_md"] == _VALID_PRD_MD
    assert row["title"] == "Insight A"
    assert row["variant"] == "v2"


def test_run_sync_passes_canonical_prompt_and_template(
    isolated_settings, fake_llm, monkeypatch
):
    """The canonical runner must use the semantic-block prompt + template
    that was promoted from v2 — `:::tldr` is in the template and
    'semantic blocks' is in the system prompt."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = db_mod.start_prd(
        brief_id=brief_id,
        insight_index=0,
        title="t",
        template_version=1,
        variant="v2",
    )

    captured: dict = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return _VALID_PRD_MD

    monkeypatch.setattr(prd_runner, "call_md", _capture)
    prd_runner._run_sync(prd_id, brief_id, 0)

    assert "semantic blocks" in captured["system"]
    assert ":::tldr" in captured["user"]


def test_run_sync_uses_fallback_title(isolated_settings, fake_llm, monkeypatch):
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod, insights=[{}])
    prd_id = db_mod.start_prd(
        brief_id=brief_id,
        insight_index=0,
        title="placeholder",
        template_version=1,
        variant="v2",
    )
    monkeypatch.setattr(prd_runner, "call_md", lambda **kw: _VALID_PRD_MD)

    prd_runner._run_sync(prd_id, brief_id, 0)
    row = db_mod.get_prd(prd_id)
    assert row["title"] == "Insight #1"


def test_run_sync_missing_brief_raises(isolated_settings):
    with pytest.raises(RuntimeError):
        prd_runner._run_sync(1, brief_id=9999, insight_index=0)


def test_run_sync_out_of_range_insight_raises(isolated_settings):
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod, insights=[{"title": "only-one"}])
    with pytest.raises(RuntimeError):
        prd_runner._run_sync(1, brief_id=brief_id, insight_index=5)


def test_generate_prd_records_failure_in_db(isolated_settings, monkeypatch):
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = db_mod.start_prd(
        brief_id=brief_id,
        insight_index=0,
        title="t",
        template_version=1,
        variant="v2",
    )

    def _boom(**_kw):
        raise ValueError("LLM exploded")

    monkeypatch.setattr(prd_runner, "call_md", _boom)
    asyncio.run(prd_runner.generate_prd(prd_id, brief_id, 0))

    row = db_mod.get_prd(prd_id)
    assert row["status"] == "failed"
    assert "ValueError" in (row["error"] or "")
