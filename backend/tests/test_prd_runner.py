"""Tests for app.prd_runner._run_sync — re-platformed onto the `prd-author`
skill (2-part output: Part A human-readable PRD + Part B LLM-readable
Implementation Spec, separated by a `---` horizontal rule).

The runner generates via `gateway.llm_call(skill="prd-author", ...)`. These
tests mock at the gateway/llm seam: most patch `prd_runner.llm_call` to assert
the skill binding + 2-part split + storage; a couple let the REAL gateway run
and patch `app.llm.call_md` to assert the prd-author METHOD reaches the prompt
and its content-hash reaches the decision log.

New rows are written with variant='v2' by the route; the runner itself
doesn't touch variant — it produces the 2-part document and calls
`complete_prd_2part` against the existing row.
"""
from __future__ import annotations

import asyncio

import pytest

from app import prd_runner
from app.graph.gateway import LLMResult


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
    return db_mod.save_brief(
        dataset=dataset, week_label="Week of stub", payload=payload, schema_version=1
    )


def _llm_result(output, model="claude-sonnet-4-6", prompt_version="prd-author-v1"):
    return LLMResult(
        output=output, model=model, prompt_version=prompt_version,
        input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.001, latency_ms=5,
        stop_reason="end_turn",
    )


# A minimal but realistic 2-part document: Part A, a `---` rule, then Part B.
_TWO_PART = (
    "# Surface — Ship the thing\n\n"
    "# Part A — Product Requirements Document (human-readable)\n"
    "## 1. Problem & evidence\nUsers can't X.\n"
    "\n---\n"
    "# Part B — Implementation Spec (LLM-readable / agent-executable)\n"
    "## B0. Available artifacts\nWHEN x THE SYSTEM SHALL y.\n"
)


def _start_prd(db_mod, brief_id, title="t", insight_index=0):
    return db_mod.start_prd(
        brief_id=brief_id, insight_index=insight_index, title=title,
        template_version=1, variant="v2",
    )


# ── gateway-seam tests (mock prd_runner.llm_call) ────────────────────────

def test_run_sync_binds_prd_author_skill(isolated_settings, monkeypatch):
    """The runner must invoke the gateway with skill='prd-author' (the METHOD
    binding) — the founder's explicit re-platform contract."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    captured: dict = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return _llm_result(_TWO_PART)

    monkeypatch.setattr(prd_runner, "llm_call", _capture)
    prd_runner._run_sync(prd_id, brief_id, 0)

    assert captured["skill"] == "prd-author"
    assert captured["agent"] == "prd"
    assert captured["purpose"] == "generate_prd"


def test_run_sync_input_carries_insight_and_corpus(isolated_settings, monkeypatch):
    """Agent-specific context (the insight + the corpus it came from) goes in
    the INPUT; the METHOD comes from the skill, not the input."""
    _seed_corpus(isolated_settings["data_dir"], body="UNIQUE_CORPUS_MARK")
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(
        db_mod, insights=[{"title": "Insight A", "subtitle": "UNIQUE_INSIGHT_MARK"}]
    )
    prd_id = _start_prd(db_mod, brief_id)

    captured: dict = {}
    monkeypatch.setattr(
        prd_runner, "llm_call",
        lambda **kw: (captured.update(kw), _llm_result(_TWO_PART))[1],
    )
    prd_runner._run_sync(prd_id, brief_id, 0)

    assert "UNIQUE_INSIGHT_MARK" in captured["input"]
    assert "UNIQUE_CORPUS_MARK" in captured["input"]
    # The skill template structure rides along too (Part A / Part B).
    assert "Part B" in captured["input"]


def test_run_sync_stores_both_parts(isolated_settings, monkeypatch):
    """Part A (human) → payload_md; Part B (LLM) → llm_part column."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    monkeypatch.setattr(prd_runner, "llm_call", lambda **kw: _llm_result(_TWO_PART))
    prd_runner._run_sync(prd_id, brief_id, 0)

    row = db_mod.get_prd(prd_id)
    assert row["status"] == "ready"
    # Part A renders as the human PRD (what the frontend shows).
    assert "Part A — Product Requirements Document" in row["payload_md"]
    assert "Users can't X." in row["payload_md"]
    # Part B is NOT in the human-rendered payload — it's stored alongside.
    assert "Part B — Implementation Spec" not in row["payload_md"]
    assert "Part B — Implementation Spec" in row["llm_part"]
    assert "WHEN x THE SYSTEM SHALL y." in row["llm_part"]


def test_run_sync_part_a_renders_as_before(isolated_settings, monkeypatch):
    """Frontend-compat: payload_md is the human PRD only, with no Part-B leakage
    and no leading/trailing separator artifacts."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    monkeypatch.setattr(prd_runner, "llm_call", lambda **kw: _llm_result(_TWO_PART))
    prd_runner._run_sync(prd_id, brief_id, 0)

    payload = db_mod.get_prd(prd_id)["payload_md"]
    assert payload.startswith("# Surface")
    assert not payload.rstrip().endswith("---")
    # get_prd_rendered (the canonical frontend read) returns the same human PRD.
    rendered = db_mod.get_prd_rendered(prd_id)
    assert rendered["payload_md"] == payload


def test_run_sync_single_part_output_degrades(isolated_settings, monkeypatch):
    """No `---` rule (degenerate single-part output) → whole doc is Part A,
    llm_part empty. payload_md still renders — never breaks the PRD screen."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    monkeypatch.setattr(
        prd_runner, "llm_call", lambda **kw: _llm_result("# Just a PRD\nbody only")
    )
    prd_runner._run_sync(prd_id, brief_id, 0)

    row = db_mod.get_prd(prd_id)
    assert row["status"] == "ready"
    assert row["payload_md"] == "# Just a PRD\nbody only"
    assert (row["llm_part"] or "") == ""


def test_run_sync_uses_fallback_title(isolated_settings, monkeypatch):
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod, insights=[{}])
    prd_id = _start_prd(db_mod, brief_id, title="placeholder")

    monkeypatch.setattr(prd_runner, "llm_call", lambda **kw: _llm_result(_TWO_PART))
    prd_runner._run_sync(prd_id, brief_id, 0)
    assert db_mod.get_prd(prd_id)["title"] == "Insight #1"


# ── decision-log + real-gateway tests (mock app.llm.call_md) ─────────────

def test_run_sync_decision_logs_skill_and_hash(isolated_settings, monkeypatch):
    """The generation is decision-logged, and the prompt_version carries the
    prd-author skill id + content-hash the gateway pinned."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    from app.skills.loader import get_skill
    skill_hash = get_skill("prd-author").content_hash

    # Let the REAL gateway run; patch only the model seam (call_md, as the
    # gateway imported it) so the gateway computes the `+prd-author@<hash>`
    # prompt_version itself.
    import app.graph.gateway as gw
    monkeypatch.setattr(gw, "call_md", lambda **kw: _TWO_PART)

    prd_runner._run_sync(prd_id, brief_id, 0)

    sup = isolated_settings["supabase"]
    rows = sup.table("agent_decision_log").select("*").execute().data
    gen = [r for r in rows if r["decision_type"] == "generate_prd"]
    assert len(gen) == 1
    pv = gen[0]["prompt_version"]
    assert "prd-author" in pv
    assert skill_hash in pv
    # The llm_call telemetry row is also logged by the gateway.
    assert any(r["decision_type"] == "llm_call" for r in rows)


def test_run_sync_real_gateway_prepends_method_to_prompt(isolated_settings, monkeypatch):
    """End-to-end through the real gateway: the prd-author METHOD (its SKILL.md)
    is prepended to the system prompt the model receives."""
    _seed_corpus(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod)
    prd_id = _start_prd(db_mod, brief_id)

    captured: dict = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return _TWO_PART

    import app.graph.gateway as gw
    monkeypatch.setattr(gw, "call_md", _capture)

    prd_runner._run_sync(prd_id, brief_id, 0)

    # METHOD delimiter + skill identity appear ahead of the agent system prompt.
    assert "METHOD (skill: prd-author" in captured["system"]
    assert "PRD Author" in captured["system"]          # from SKILL.md
    assert "Sprntly's PRD agent" in captured["system"]  # agent layer, after method

    # And the 2-part output still lands in storage.
    row = db_mod.get_prd(prd_id)
    assert "Part A" in row["payload_md"]
    assert "Part B" in row["llm_part"]


# ── error paths (unchanged contract) ─────────────────────────────────────

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
    prd_id = _start_prd(db_mod, brief_id)

    def _boom(**_kw):
        raise ValueError("LLM exploded")

    monkeypatch.setattr(prd_runner, "llm_call", _boom)
    asyncio.run(prd_runner.generate_prd(prd_id, brief_id, 0))

    row = db_mod.get_prd(prd_id)
    assert row["status"] == "failed"
    assert "ValueError" in (row["error"] or "")
