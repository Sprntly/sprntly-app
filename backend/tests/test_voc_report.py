"""voice-of-customer-report template — structured data → pinned HTML.

`render_html` is pure and deterministic; `build` wraps one llm_call. These tests
pin the markup contract (pills, table columns, gate line), the XSS-escaping of all
model text, and that the demo-only banner/brand chrome is never emitted.
"""
from __future__ import annotations

import app.voc_report as vr


def _data(**over) -> dict:
    d = {
        "title": "Voice of Customer — Q2 2026",
        "lede": "Customers love the output and struggle to **start it**.",
        "coverage": "coverage: 94% of items tagged",
        "sources": ["17 CSM calls · ~15 accounts", "42 tickets", "Apr–Jun 2026"],
        "top_findings": [
            {"problem": "Hard to get connected", "sentence": "Users stall.",
             "impact_line": "IMPACTS → activation 🅗",
             "vol": {"level": "high", "count": "11/17"}, "sev": {"level": "high"},
             "silent_killer": False},
            {"problem": "Can't control access", "sentence": "Admins can't fence it.",
             "impact_line": "IMPACTS → NRR 🅢",
             "vol": {"level": "low", "count": "5/17"}, "sev": {"level": "high"},
             "silent_killer": True},
        ],
        "problems": [
            {"problem": "Hard to get connected",
             "vol": {"level": "high", "count": "11/17 calls"},
             "sev": {"level": "high", "note": "blocks all value"},
             "metric": "activation rate", "by_how_much": "11 of 15 stalled 🅗",
             "revenue_line": "revenue: 🅘 unknown", "revenue_unknown": True,
             "silent_killer": False},
        ],
        "long_tail": {"label": "Long tail (7 items)", "count_note": "≤2 each"},
        "themes": [
            {"title": "Hard to get connected", "size_line": "🅗 11 of 17 · persistent",
             "description": "Connecting stalls trials.",
             "quotes": [{"text": '"It took nine days."', "attr": "CSM call, May"}],
             "impact_line": "Impact: activation 🅗", "impact_warn": True,
             "silent_killer": False},
        ],
        "gate": {"candidates": 12, "selected": 5, "routed": 7},
        "goals_note": "activation · NRR",
        "recommendations": [
            {"title": "Guided connection flow", "description": "Build guided setup.",
             "impact_line": "IMPACTS → activation", "investigation_only": False},
            {"title": "Investigate ARR", "description": "Connect CRM.",
             "impact_line": "IMPACTS → confidence", "investigation_only": True},
        ],
    }
    d.update(over)
    return d


def test_render_html_is_a_self_contained_document():
    html = vr.render_html(_data())
    assert html.startswith("<!DOCTYPE html>")
    assert "<style>" in html and "Voice of Customer — Q2 2026" in html


def test_render_html_no_demo_chrome():
    html = vr.render_html(_data())
    assert "sample-banner" not in html
    assert "VoidAI" not in html and 'class="brand"' not in html


def test_render_html_no_meta_chips_row():
    # The reference design's top chips (skill name / curation / basis /
    # coverage) are internal chrome — the report starts at the page title.
    html = vr.render_html(_data())
    assert 'class="chips"' not in html
    assert "voice-of-customer-report" not in html
    assert "curated, direct-access sources only" not in html
    assert "coverage: 94% of items tagged" not in html


def test_render_html_table_columns_and_pills():
    html = vr.render_html(_data())
    for col in ("User problem", "Volume", "Severity", "Metric it impacts", "By how much"):
        assert f">{col}</th>" in html
    assert 'class="pill high"' in html
    # TL;DR mini-pills carry the VOL/SEV keys on the shared scale.
    assert '<span class="k">VOL</span>' in html and '<span class="k">SEV</span>' in html


def test_render_html_gate_line_uses_real_counts():
    html = vr.render_html(_data())
    assert "PRIORITIZATION GATE PASSED ✓ — 12 candidate actions identified · 5 selected" in html
    assert "7 routed to monitor/ideation" in html


def test_render_html_silent_killer_flag():
    html = vr.render_html(_data())
    assert "🔇" in html  # applied on the low-vol / high-sev finding


def test_render_html_has_no_cta_buttons():
    # The report renders in a script-less sandboxed iframe where nothing is
    # clickable — it must carry NO dead CTA buttons; the app panel hosts the
    # real Generate PRD action outside the document.
    html = vr.render_html(_data())
    assert "Generate PRD" not in html
    assert "Move to ideation" not in html and "Move to backlog" not in html
    assert 'class="cta' not in html


def test_render_html_tldr_side_column_is_auto_sized():
    # Regression: a fixed 172px side column let wide VOL/SEV pills overflow
    # left over the finding title in narrow panels.
    assert "minmax(0,1fr) auto" in vr._STYLE
    assert "1fr 172px" not in vr._STYLE


def test_render_html_revenue_unknown_styles_na():
    html = vr.render_html(_data())
    assert '<span class="impact-rev na">revenue: 🅘 unknown</span>' in html


def test_render_html_escapes_model_text():
    html = vr.render_html(_data(top_findings=[
        {"problem": "x <script>alert(1)</script>", "sentence": "y & z",
         "impact_line": "i", "vol": {"level": "high", "count": "1"},
         "sev": {"level": "high"}, "silent_killer": False},
    ]))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "y &amp; z" in html


def test_render_html_lede_bold_spans():
    html = vr.render_html(_data())
    assert "<b>start it</b>" in html


def test_render_html_unknown_level_falls_back_to_med():
    html = vr.render_html(_data(problems=[
        {"problem": "p", "vol": {"level": "bogus", "count": "1"},
         "sev": {"level": "", "note": "n"}, "metric": "m", "by_how_much": "b",
         "revenue_line": "r", "revenue_unknown": False, "silent_killer": False},
    ]))
    assert 'class="pill med"' in html


def test_build_runs_llm_and_renders(monkeypatch):
    class _R:
        output = _data()
    captured = {}
    monkeypatch.setattr(vr, "llm_call", lambda **k: captured.update(k) or _R())
    html = vr.build(enterprise_id="co", question="voc?", corpus_text="CALLS…",
                    source_line="=== CALLS ===", model="m")
    assert html.startswith("<!DOCTYPE html>")
    assert captured["skill"] == "voice-of-customer-report"
    assert captured["json_schema"] is vr.SCHEMA
    assert "CALLS…" in captured["input"] and "=== CALLS ===" in captured["input"]


def test_build_raises_on_non_dict_output(monkeypatch):
    class _R:
        output = "not a dict"
    monkeypatch.setattr(vr, "llm_call", lambda **k: _R())
    import pytest
    with pytest.raises(ValueError):
        vr.build(enterprise_id="co", question="q", corpus_text="c",
                 source_line="s", model="m")
