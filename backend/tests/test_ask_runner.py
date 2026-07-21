"""Tests for app.ask_runner — the predefined-prompt cache warmer.

We test `_generate_one_sync` (the synchronous core) and `_warm_one`
(the async cache-row lifecycle) directly. The fan-out helpers
(`warm_predefined_asks`, `warm_brief_dynamic_asks`) are thin
`asyncio.create_task(_warm_one(…))` loops; their semantics are covered
indirectly by `_warm_one` tests.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app import ask_runner


def _seed_corpus(data_dir, dataset="asurion", body="corpus body"):
    ds = data_dir / dataset
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text(body)


# ---- _generate_one_sync -----------------------------------------------------

def test_generate_one_sync_calls_llm_and_returns_payload(
    isolated_settings, fake_llm
):
    _seed_corpus(isolated_settings["data_dir"])
    expected = {
        "answer": "stub",
        "key_points": ["a"],
        "citations": [],
        "confidence": 0.8,
        "unanswered": "",
    }
    fake_llm["payload"] = expected
    out = ask_runner._generate_one_sync("asurion", "Some question?")
    assert out == expected
    assert len(fake_llm["calls"]) == 1


def test_generate_one_sync_runs_in_background_lane(isolated_settings, fake_llm):
    """Ask warming is pre-computation, never a user waiting — the call must ride
    the LLM gate's low-priority background lane so the post-brief warm storm
    can't queue a user's own generation behind it."""
    _seed_corpus(isolated_settings["data_dir"])
    ask_runner._generate_one_sync("asurion", "Some question?")
    assert fake_llm["calls"][0]["kwargs"]["background"] is True


def test_generate_one_sync_passes_question_into_user_prompt(
    isolated_settings, fake_llm
):
    _seed_corpus(isolated_settings["data_dir"])
    fake_llm["payload"] = {
        "answer": "x",
        "key_points": [],
        "citations": [],
        "confidence": 0.5,
        "unanswered": "",
    }
    ask_runner._generate_one_sync("asurion", "A very unique probe question?")
    assert "A very unique probe question?" in fake_llm["calls"][0]["user"]


# ---- _warm_one --------------------------------------------------------------

def test_warm_one_is_idempotent_when_row_exists(
    isolated_settings, fake_llm
):
    """If a row already exists (ready or generating) for the same question,
    `_warm_one` no-ops without calling the LLM."""
    db_mod = isolated_settings["db"]
    cache_id = db_mod.start_cached_ask(
        dataset="asurion", question="Tell me more about: X", cache_version=1
    )
    db_mod.complete_cached_ask(cache_id, json.dumps({"answer": "warm"}))

    sema = asyncio.Semaphore(2)
    fake_llm["calls"].clear()
    asyncio.run(ask_runner._warm_one("asurion", "Tell me more about: X", sema))

    # LLM is never invoked when a row exists.
    assert fake_llm["calls"] == []


def test_warm_one_happy_path_completes_row(
    isolated_settings, fake_llm
):
    _seed_corpus(isolated_settings["data_dir"])
    fake_llm["payload"] = {
        "answer": "warmed answer",
        "key_points": [],
        "citations": [],
        "confidence": 0.9,
        "unanswered": "",
    }
    db_mod = isolated_settings["db"]
    sema = asyncio.Semaphore(2)
    question = "What are the biggest revenue drivers"

    asyncio.run(ask_runner._warm_one("asurion", question, sema))

    row = db_mod.find_cached_ask("asurion", question)
    assert row is not None
    assert row["status"] == "ready"
    payload = json.loads(row["response_json"])
    assert payload["answer"] == "warmed answer"


def test_warm_one_records_failure_when_llm_raises(
    isolated_settings, monkeypatch
):
    """If `_generate_one_sync` raises, the cache row is marked 'failed' with
    a truncated error string."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]

    def _boom(dataset, question):
        raise ValueError("LLM exploded")

    monkeypatch.setattr(ask_runner, "_generate_one_sync", _boom)
    sema = asyncio.Semaphore(2)
    question = "Fail this question please"

    asyncio.run(ask_runner._warm_one("asurion", question, sema))

    row = db_mod.find_cached_ask("asurion", question)
    # find_cached_ask only returns ready/generating rows — a failed row is filtered out.
    assert row is None
