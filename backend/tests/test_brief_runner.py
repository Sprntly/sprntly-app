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
