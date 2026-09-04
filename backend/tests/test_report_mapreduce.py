"""Concurrent report map-reduce — the shared `answer_first.gateway_sections`
primitive, its post-strip / truncation helpers, the three-gate flag, and the
load-bearing production-safety invariant: with the gates OFF every report takes
its exact current single-pass path.

Everything is pure or mocked — no network, no live LLM, no DB. Company names in
fixtures are synthetic (Acme / Northwind / Contoso); the repo is public.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

import app.answer_first as af
import app.graph.gateway as gateway_mod

_MAPREDUCE_ENV = (
    "ANSWER_FIRST_STREAMING_ENABLED",
    "REPORT_MAPREDUCE_ENABLED",
    "VOC_MAPREDUCE_ENABLED",
    "MARKET_INTEL_MAPREDUCE_ENABLED",
    "PUBLIC_FEEDBACK_MAPREDUCE_ENABLED",
)


@pytest.fixture(autouse=True)
def _clean_flags(monkeypatch):
    """Every test starts with all map-reduce gates unset (production default)."""
    for name in _MAPREDUCE_ENV:
        monkeypatch.delenv(name, raising=False)


# ── _strip_split_markers ─────────────────────────────────────────────────────


def test_strip_split_markers_removes_a_title_marker_but_keeps_the_title():
    assert af._strip_split_markers(
        "# Part 1 of 2: Voice of Customer Report\n\n## Scope"
    ) == "# Voice of Customer Report\n\n## Scope"


def test_strip_split_markers_drops_a_pure_marker_heading():
    assert af._strip_split_markers("# Part 1 of 2\n\n## Scope") == "## Scope"


def test_strip_split_markers_drops_a_bold_pure_marker():
    assert af._strip_split_markers("**Part 2 of 2**\nRecommendations") == "Recommendations"


@pytest.mark.parametrize("prose", [
    "The account has 2 of 3 seats active.",
    "## Recommendations",
    "The report is current as of 2 August 2026.",
    "We reviewed section A of the contract with the customer.",
])
def test_strip_split_markers_leaves_ordinary_prose_untouched(prose):
    assert af._strip_split_markers(prose) == prose


def test_strip_split_markers_is_idempotent():
    once = af._strip_split_markers("# Part 1 of 2: Report\n\nbody")
    assert af._strip_split_markers(once) == once


# ── _strip_metadata_blocks ───────────────────────────────────────────────────


def test_strip_metadata_blocks_removes_a_tagged_metadata_fence():
    out = af._strip_metadata_blocks(
        "## Bottom line\nProse here.\n\n```metadata\nwindow: Feb - Jul 2026\n"
        "entrants: 7\n```\n"
    )
    assert "```" not in out
    assert "Prose here." in out


def test_strip_metadata_blocks_removes_a_tagged_json_fence():
    out = af._strip_metadata_blocks(
        'Prose.\n\n```json\n{\n  "window": "Feb - Jul 2026"\n}\n```'
    )
    assert "```" not in out and out.strip() == "Prose."


def test_strip_metadata_blocks_removes_a_trailing_untagged_kv_dump():
    out = af._strip_metadata_blocks(
        'Report body.\n\n```\n{\n  "window": "Feb - Jul 2026",\n  "collected": 29\n}\n```'
    )
    assert "```" not in out and out.strip() == "Report body."


@pytest.mark.parametrize("keep", [
    # A prose pull-quote fence — not a key:value dump.
    "Here is a quote:\n\n```\nThis product changed how we work.\nWe use it daily.\n```",
    # A tagged code example — a real code block, not a structured dump.
    "Run this:\n\n```python\nx = compute()\nprint(x)\n```",
    # Fence-free prose.
    "## Bottom line\nJust prose, nothing to strip here.",
])
def test_strip_metadata_blocks_leaves_prose_and_code_untouched(keep):
    assert af._strip_metadata_blocks(keep) == keep


def test_strip_metadata_blocks_is_idempotent():
    once = af._strip_metadata_blocks("Body.\n\n```metadata\nk: v\nk2: v2\n```")
    assert af._strip_metadata_blocks(once) == once


# ── gateway_sections: fan-out, merge, truncation, metadata ───────────────────


def _fake_gateway_llm(monkeypatch, *, stop_reasons, meta_output=None):
    """Fake `graph.gateway.llm_call`: section calls return synthetic prose with a
    per-section `stop_reason`; a `*_meta` call returns `meta_output` (a dict)."""
    calls: list[str] = []

    def _fake(**kw):
        purpose = kw["purpose"]
        calls.append(purpose)
        if purpose.endswith("_meta"):
            return SimpleNamespace(output=meta_output or {}, stop_reason="end_turn")
        idx = int(purpose.rsplit("_s", 1)[1])
        return SimpleNamespace(output=f"PROSE for {purpose}",
                               stop_reason=stop_reasons[idx])

    monkeypatch.setattr(gateway_mod, "llm_call", _fake)
    return calls


def _run_sections(**overrides):
    kw = dict(
        question="q", forced_system="SYSTEM", forced_user="Question: q",
        sections=[("scope", "directive A"), ("quotes-bottom", "directive B")],
        on_delta=None, default_confidence=0.6, enterprise_id="e", agent="qa",
        purpose="voc_report", prompt_version="v3",
    )
    kw.update(overrides)
    return af.gateway_sections(**kw)


def test_gateway_sections_merges_both_sections_in_order(monkeypatch):
    _fake_gateway_llm(monkeypatch, stop_reasons=["end_turn", "end_turn"])
    out = _run_sections(derive_metadata=False)
    assert out["answer"] == "PROSE for voc_report_s0\n\nPROSE for voc_report_s1"


def test_gateway_sections_flags_a_truncated_section_and_logs(monkeypatch, caplog):
    _fake_gateway_llm(monkeypatch, stop_reasons=["end_turn", "max_tokens"])
    with caplog.at_level(logging.WARNING, logger="app.answer_first"):
        out = _run_sections(purpose="market_intelligence_report", derive_metadata=False)
    assert out["_truncated"] is True
    assert out["_truncated_purposes"] == ["market_intelligence_report_s1"]
    assert any("TRUNCATED" in r.message and "market_intelligence_report_s1" in r.message
               for r in caplog.records)


def test_gateway_sections_no_marker_when_all_sections_complete(monkeypatch):
    _fake_gateway_llm(monkeypatch, stop_reasons=["end_turn", "end_turn"])
    out = _run_sections(derive_metadata=False)
    assert "_truncated" not in out and "_truncated_purposes" not in out


def test_derive_metadata_false_skips_the_meta_call_and_defaults_fields(monkeypatch):
    calls = _fake_gateway_llm(monkeypatch, stop_reasons=["end_turn", "end_turn"])
    out = _run_sections(derive_metadata=False)
    assert not any(p.endswith("_meta") for p in calls)
    assert out["key_points"] == [] and out["citations"] == []
    assert out["confidence"] == 0.6 and out["unanswered"] == ""


def test_derive_metadata_true_fires_the_meta_call(monkeypatch):
    calls = _fake_gateway_llm(
        monkeypatch, stop_reasons=["end_turn", "end_turn"],
        meta_output={"key_points": ["kp"], "citations": [], "confidence": 0.9,
                     "unanswered": ""},
    )
    out = _run_sections(derive_metadata=True)
    assert calls == ["voc_report_s0", "voc_report_s1", "voc_report_meta"]
    assert out["key_points"] == ["kp"] and out["confidence"] == 0.9


# ── report_mapreduce_enabled: the three-gate matrix ──────────────────────────


def test_gate_is_off_by_default(monkeypatch):
    for report in ("voc", "market_intel", "public_feedback"):
        assert af.report_mapreduce_enabled(report) is False


def test_gate_needs_all_three_switches(monkeypatch):
    # answer-first master + global map-reduce master + the per-report gate.
    monkeypatch.setenv("ANSWER_FIRST_STREAMING_ENABLED", "1")
    monkeypatch.setenv("MARKET_INTEL_MAPREDUCE_ENABLED", "1")
    assert af.report_mapreduce_enabled("market_intel") is False  # no global master

    monkeypatch.setenv("REPORT_MAPREDUCE_ENABLED", "1")
    assert af.report_mapreduce_enabled("market_intel") is True

    monkeypatch.delenv("ANSWER_FIRST_STREAMING_ENABLED")
    assert af.report_mapreduce_enabled("market_intel") is False  # answer-first off


def test_gate_per_report_switches_are_independent(monkeypatch):
    monkeypatch.setenv("ANSWER_FIRST_STREAMING_ENABLED", "1")
    monkeypatch.setenv("REPORT_MAPREDUCE_ENABLED", "1")
    monkeypatch.setenv("VOC_MAPREDUCE_ENABLED", "1")
    assert af.report_mapreduce_enabled("voc") is True
    assert af.report_mapreduce_enabled("market_intel") is False
    assert af.report_mapreduce_enabled("public_feedback") is False


# ── PRODUCTION-SAFETY INVARIANT ──────────────────────────────────────────────
# With the gates OFF, all three reports take their EXACT current single-pass
# forced-JSON path and the map-reduce branch is never reached. This is the
# guarantee that makes the whole change safe to merge behind a dark flag.


def _tripwire_gateway_sections(monkeypatch):
    def _boom(**kw):
        raise AssertionError("gateway_sections must NOT run with the gates off")
    monkeypatch.setattr(af, "gateway_sections", _boom)


def test_market_intel_off_uses_the_single_pass_forced_json(monkeypatch):
    import app.market_intel as mi
    import app.research.market as market

    profile = {"display_name": "Acme", "industry": "Widgets",
               "product_description": "Widget automation",
               "product": {"name": "Acme"}}
    monkeypatch.setattr(market, "company_profile", lambda _eid: dict(profile))
    monkeypatch.setattr(mi, "_capture", lambda *a, **k: ([{"category": "funding",
                        "headline": "Northwind raises", "entity": "Northwind"}], False))
    _tripwire_gateway_sections(monkeypatch)

    captured: dict = {}

    def _fake(**kw):
        captured.update(kw)
        return SimpleNamespace(output={"answer": "## Report\nprose",
                                       "window_label": "2026", "metadata": {}},
                               stop_reason="end_turn")
    monkeypatch.setattr(mi, "llm_call", _fake)

    out = mi.answer(enterprise_id="e1", question="run a market intelligence report")
    assert out["answer"] == "## Report\nprose"
    # The single-pass call is forced-JSON over the module's report schema.
    assert captured["json_schema"] is mi._REPORT_SCHEMA
    assert captured["purpose"] == "market_intelligence_report"


def test_public_feedback_off_uses_the_single_pass_forced_json(monkeypatch):
    import app.public_feedback as pf
    import app.research.market as market
    import app.db as db

    monkeypatch.setattr(market, "company_profile",
                        lambda _eid: {"display_name": "Acme", "product": {"name": "Acme"}})
    monkeypatch.setattr(pf, "_capture", lambda *a, **k: ([{"verbatim": "great",
                        "source": {"platform": "Reddit"}}], False))
    monkeypatch.setattr(db, "save_public_feedback_run", lambda *a, **k: None)
    _tripwire_gateway_sections(monkeypatch)

    captured: dict = {}

    def _fake(**kw):
        captured.update(kw)
        return SimpleNamespace(output={"answer": "## Feedback\nprose",
                                       "window_label": "2026",
                                       "metadata": {"totals": {"collected": 1}}},
                               stop_reason="end_turn")
    monkeypatch.setattr(pf, "llm_call", _fake)

    out = pf.answer(enterprise_id="e1", question="what are people saying about us online")
    assert out["answer"] == "## Feedback\nprose"
    assert captured["json_schema"] is pf._REPORT_SCHEMA
    assert captured["purpose"] == "public_feedback_report"


def test_voc_off_uses_the_single_pass_forced_json(monkeypatch):
    import app.call_digest as cd
    from app.kg_ingest.pullers.fireflies import CallTranscript

    def _call(i):
        return CallTranscript(
            external_id=f"c{i}", title=f"Call {i}", date="2026-06-20",
            participants=["p@x.com"], overview=f"overview {i}",
            quotes=[{"speaker": "Cust", "text": f"quote {i}"}],
        )

    monkeypatch.setattr(cd, "_load_api_key", lambda cid: "key")
    monkeypatch.setattr(cd, "fetch_calls", lambda *a, **k: [_call(1), _call(2)])
    _tripwire_gateway_sections(monkeypatch)

    captured: dict = {}

    def _fake(**kw):
        captured.update(kw)
        return SimpleNamespace(output={"answer": "## Voice of customer\nprose",
                                       "key_points": [], "citations": [],
                                       "confidence": 0.6, "unanswered": ""},
                               stop_reason="end_turn")
    monkeypatch.setattr(gateway_mod, "llm_call", _fake)

    out = cd.answer(enterprise_id="co", question="give me a voice of customer report for last week")
    assert out["answer"].startswith("## Voice of customer")
    # Forced-JSON single-pass, scoped to the VoC skill — the map-reduce branch
    # never ran (the tripwire would have raised).
    assert captured["json_schema"] is not None
    assert captured["purpose"] == "voc_report"
    assert captured["skill"] == "voice-of-customer-report"


def test_market_intel_on_reaches_the_mapreduce_branch(monkeypatch):
    """The complement — proves the off-tests above are not vacuous: with all
    three gates on, MI DOES route through gateway_sections (and not forced-JSON)."""
    import app.market_intel as mi
    import app.research.market as market

    monkeypatch.setenv("ANSWER_FIRST_STREAMING_ENABLED", "1")
    monkeypatch.setenv("REPORT_MAPREDUCE_ENABLED", "1")
    monkeypatch.setenv("MARKET_INTEL_MAPREDUCE_ENABLED", "1")

    monkeypatch.setattr(market, "company_profile",
                        lambda _eid: {"display_name": "Acme", "industry": "Widgets",
                                      "product_description": "x", "product": {"name": "Acme"}})
    monkeypatch.setattr(mi, "_capture", lambda *a, **k: ([{"category": "funding",
                        "headline": "h", "entity": "Northwind"}], False))

    def _forced_json_boom(**kw):
        raise AssertionError("single-pass forced-JSON must NOT run with the gates on")
    monkeypatch.setattr(mi, "llm_call", _forced_json_boom)

    seen: dict = {}

    def _fake_sections(**kw):
        seen.update(kw)
        return {"answer": "## MR report\nprose", "key_points": [], "citations": [],
                "confidence": 0.6, "unanswered": ""}
    monkeypatch.setattr(af, "gateway_sections", _fake_sections)

    out = mi.answer(enterprise_id="e1", question="run a market intelligence report")
    assert out["answer"] == "## MR report\nprose"
    assert seen["sections"] is mi._MI_SECTIONS
    assert seen["derive_metadata"] is False
    assert seen["max_tokens"] == mi._MI_SECTION_MAX_TOKENS
