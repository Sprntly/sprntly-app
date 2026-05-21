"""Tests for app.brief_runner.

Focus on the DB-driven auto_generate_all behavior (the old AUTO_DATASETS
tuple was hardcoded). Brief generation itself is end-to-end async — we test
that it picks up registered slugs and that the brief lands in the DB.
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_auto_generate_all_processes_registered_slugs(isolated_settings, fake_llm):
    import importlib
    import app.brief_runner as br
    importlib.reload(br)

    db = isolated_settings["db"]
    db.insert_dataset("acme", "Acme")
    # corpus.load_corpus expects at least one .md in the dataset dir
    (isolated_settings["data_dir"] / "acme").mkdir(exist_ok=True)
    (isolated_settings["data_dir"] / "acme" / "ctx.md").write_text("# ctx")

    fake_llm["payload"] = {
        "week_label": "Demo Week",
        "_schema_version": br.BRIEF_SCHEMA_VERSION,
        "insights": [],
    }
    await br.auto_generate_all()

    brief = db.get_current_brief("acme")
    assert brief is not None
    assert brief["week_label"] == "Demo Week"


@pytest.mark.asyncio
async def test_auto_generate_all_skips_existing(isolated_settings, fake_llm):
    import importlib
    import app.brief_runner as br
    importlib.reload(br)

    db = isolated_settings["db"]
    db.insert_dataset("acme", "Acme")
    (isolated_settings["data_dir"] / "acme").mkdir(exist_ok=True)
    (isolated_settings["data_dir"] / "acme" / "ctx.md").write_text("# ctx")

    # Pre-seed an existing brief so the runner short-circuits.
    db.save_brief("acme", "Pre", {"insights": []}, schema_version=br.BRIEF_SCHEMA_VERSION)
    fake_llm["calls"] = []
    await br.auto_generate_all()
    # No LLM call was needed.
    assert fake_llm["calls"] == []


@pytest.mark.asyncio
async def test_auto_generate_all_no_datasets_no_calls(isolated_settings, fake_llm):
    import importlib
    import app.brief_runner as br
    importlib.reload(br)
    fake_llm["calls"] = []
    await br.auto_generate_all()
    assert fake_llm["calls"] == []


def test_get_status_empty_for_unknown(isolated_settings):
    import importlib
    import app.brief_runner as br
    importlib.reload(br)
    assert br.get_status("ghost") == {"status": "empty"}


@pytest.mark.asyncio
async def test_warm_prd_skips_when_v2_row_exists(isolated_settings, fake_llm, monkeypatch):
    """Regression: _warm_prd must dedupe on variant='v2', not 'v1'.

    Without the variant pin, a legacy v1 row in the prds table fools the
    warmer into thinking a v2 PRD is cached and it silently skips the
    fan-out — the user then pays the full LLM cost when they click
    Generate PRD on the v2 route.
    """
    import importlib
    import app.brief_runner as br
    importlib.reload(br)
    db = isolated_settings["db"]

    db.insert_dataset("acme", "Acme")
    brief_id = db.save_brief(
        "acme", "Test Week", {"insights": [{"title": "Insight A"}]},
        schema_version=br.BRIEF_SCHEMA_VERSION,
    )
    # Seed only a v1 row — leaves v2 missing on purpose.
    db.start_prd(brief_id=brief_id, insight_index=0, title="A", variant="v1")
    calls: list[tuple] = []

    async def fake_generate_prd(prd_id, b_id, idx):
        calls.append(("gen", prd_id, b_id, idx))

    monkeypatch.setattr(br, "generate_prd", fake_generate_prd)
    await br._warm_prd(brief_id, 0, "A")

    # v2 row was missing, so warmer must have created one and dispatched.
    assert len(calls) == 1, "expected _warm_prd to dispatch when only v1 exists"
    prd = db.find_existing_prd(brief_id, 0, variant="v2")
    assert prd is not None and prd["variant"] == "v2"


@pytest.mark.asyncio
async def test_warm_prd_skips_when_v2_row_already_exists(isolated_settings, fake_llm, monkeypatch):
    """Idempotency: when a ready v2 row already exists, warmer must skip."""
    import importlib
    import app.brief_runner as br
    importlib.reload(br)
    db = isolated_settings["db"]

    db.insert_dataset("acme", "Acme")
    brief_id = db.save_brief(
        "acme", "Test Week", {"insights": [{"title": "Insight A"}]},
        schema_version=br.BRIEF_SCHEMA_VERSION,
    )
    prd_id = db.start_prd(
        brief_id=brief_id, insight_index=0, title="A",
        template_version=br.PRD_TEMPLATE_VERSION, variant="v2",
    )
    db.complete_prd(prd_id=prd_id, title="A", md="# done")
    calls: list[tuple] = []

    async def fake_generate_prd(prd_id, b_id, idx):
        calls.append(("gen", prd_id, b_id, idx))

    monkeypatch.setattr(br, "generate_prd", fake_generate_prd)
    await br._warm_prd(brief_id, 0, "A")

    assert calls == [], "warmer must not re-dispatch when a v2 row is ready"
