"""voice-of-customer-report v3 template — structured data → pinned HTML.

`render_html` is pure and deterministic; `build` wraps one llm_call. These tests
pin the v3 markup contract (literal title + period, Asked line, run line,
problems-first TL;DR, glance table with frustration 1–5 + tone + metric, the
template-drawn SVG radar, theme cards, goal-fit recommendations with the
deliberately-not-recommended block), the XSS-escaping of all model text, and
that no dead CTA buttons are emitted.
"""
from __future__ import annotations

import app.voc_report as vr


def _glance_row(**over) -> dict:
    g = {
        "problem": "Manager adoption cliff", "accounts": "24 (59%)",
        "accounts_n": 24, "frustration": 4, "tone": "Weary, resigned",
        "metric": "Weekly participation — North Star", "metric_none": False,
        "minor": False,
    }
    g.update(over)
    return g


def _data(**over) -> dict:
    d = {
        "eyebrow": "Kindling · Product",
        "period": "6 January 2026 – 30 June 2026",
        "period_note": "176 days · last two quarters",
        "deck": "The program launches, then quietly stops.",
        "asked": "Give me the voice of customer report for the last two quarters.",
        "honored": "Full window, no filters.",
        "run_line": {
            "scope": "all sources · no filters applied",
            "excluded": "",
            "sources": "14 CSM calls · 218 tickets",
            "coverage": "41 of 128 accounts · 683 records captured, 570 counted",
            "goals": "weekly participation (North Star), gross retention",
        },
        "tldr": {
            "source_line": "Synthesized from 570 pieces of first-party feedback.",
            "intro": "What they describe are four problems they cannot solve on their own:",
            "close": "We sell a habit and ship a launch.",
        },
        "findings": [
            {"problem": "Sponsors can't make a manager who has never posted, post.",
             "sentence": "Recognition collapses onto a few enthusiasts.",
             "quote": "I was personally DMing directors to please post something.",
             "quote_attr": "— Northwind Health (churned)"},
        ],
        "glance": [
            _glance_row(),
            _glance_row(problem="No way to prove ROI", accounts="17 (41%)",
                        accounts_n=17, frustration=5, tone="Exposed, defensive",
                        metric="Gross retention"),
            _glance_row(problem="Thin international catalogue", accounts="11 (27%)",
                        accounts_n=11, frustration=3, tone="Apologetic",
                        metric="Net revenue retention"),
            _glance_row(problem="Dark mode", accounts="1 (2%)", accounts_n=1,
                        frustration=1, tone="Neutral", metric="none identified",
                        metric_none=True, minor=True),
        ],
        "glance_notes": "Denominator is 41 accounts. Frustration is analyst-assigned.",
        "radar_read": "Payroll is the sharpest divergence — smallest theme, maximum frustration.",
        "themes": [
            {"title": "Manager adoption cliff", "size_chip": "24 accts · 59%",
             "rank_label": "Critical", "tier": "critical",
             "description": "Recognition concentrates on a handful of managers.",
             "stats": [{"text": "24 of 41 (59%)", "kind": "plain"},
                       {"text": "frustration 4/5 · weary", "kind": "mood"},
                       {"text": "2 churned · $118K", "kind": "churn"},
                       {"text": "$214K renewing ≤2Q", "kind": "money"},
                       {"text": "usage analytics: not connected", "kind": "miss"}],
             "quotes": [{"text": "By March it was the same four managers.",
                         "attr": "— Northwind Health, Director of People (churned)"}],
             "rows": [], "flag": "", "flag_kind": ""},
            {"title": "Minor / one-off requests", "size_chip": "7 accts · 17%",
             "rank_label": "Low", "tier": "minor",
             "description": "Isolated, calm, tied to no goal metric.",
             "stats": [], "quotes": [],
             "rows": [{"text": "Dark mode — a one-line submission.",
                       "who": "Torrey Analytics"}],
             "flag": "🔇 Silent killer: quiet but costly.", "flag_kind": "warn"},
        ],
        "goal_note": "Selected by fit to the goal metrics this team tracks.",
        "recommendations": [
            {"title": "Build manager activation",
             "why": "The only theme behind two of our three churns.",
             "moves": "weekly participation ↑ · gross retention ↑"},
        ],
        "not_recommended": "The Slack/Teams surface — wide but calm, no churn, no dollars.",
        "basis_note": "",
    }
    d.update(over)
    return d


def test_render_html_is_a_self_contained_document():
    html = vr.render_html(_data())
    assert html.startswith("<!DOCTYPE html>")
    assert "<style>" in html


def test_render_html_title_and_period_are_pinned():
    # v3 mandates the literal title with the explicit date range beneath it.
    html = vr.render_html(_data())
    assert "<h1>Voice of Customer Report</h1>" in html
    assert "6 January 2026 – 30 June 2026" in html
    assert "176 days · last two quarters" in html


def test_render_html_asked_and_run_lines():
    html = vr.render_html(_data())
    assert "<b>Asked:</b>" in html
    assert "Give me the voice of customer report" in html
    assert "<b>Scope:</b>" in html and "<b>Coverage:</b>" in html
    assert "683 records captured, 570 counted" in html


def test_render_html_excluded_only_when_present():
    assert "<b>Excluded by filter:</b>" not in vr.render_html(_data())
    d = _data()
    d["run_line"]["excluded"] = "6 sales calls"
    html = vr.render_html(d)
    assert "<b>Excluded by filter:</b> 6 sales calls" in html


def test_render_html_tldr_findings_are_enumerated_problems():
    html = vr.render_html(_data())
    assert '<span class="hash">#1</span>' in html
    assert "Sponsors can&#x27;t make a manager who has never posted, post." in html
    assert "<b>What this means for us:</b>" in html


def test_render_html_glance_columns_frustration_and_tone():
    html = vr.render_html(_data())
    for col in ("Problem", "Accounts", "Frustration", "How they sound", "Metric impacted"):
        assert f">{col}</th>" in html
    assert '<td class="f4">4 / 5</td>' in html
    assert '<td class="f5">5 / 5</td>' in html
    assert "Weary, resigned" in html
    # minor rows are dimmed; none-identified metrics style as .none
    assert 'class="minor"' in html
    assert '<td class="metric none">none identified</td>' in html


def test_render_html_frustration_clamped_to_1_5():
    html = vr.render_html(_data(glance=[_glance_row(frustration=9)] * 3))
    assert 'class="f5"' in html and 'class="f9"' not in html


def test_render_html_radar_from_glance_rows():
    html = vr.render_html(_data())
    # Template draws the radar itself: both series + axis labels present.
    assert "<svg" in html and 'stroke="#1f6f52"' in html and 'stroke="#bb463c"' in html
    assert "Volume — accounts affected (0–24)" in html
    assert "Manager adoption cliff</text>" in html
    # Minor rows stay off the radar.
    assert "Dark mode</text>" not in html


def test_render_html_radar_skipped_under_three_axes():
    html = vr.render_html(_data(glance=[_glance_row(), _glance_row(minor=True)]))
    assert "<svg" not in html


def test_render_html_theme_cards_tiers_and_stats():
    html = vr.render_html(_data())
    assert 'class="card c1"' in html          # critical tier
    assert 'class="card c4 minor"' in html    # minor bucket card
    assert 'class="stat mood"' in html and 'class="stat churn"' in html
    assert 'class="stat money"' in html and 'class="stat miss"' in html
    assert "🗣 Voice of customer" in html and "🗣 What was asked" in html
    assert 'class="flag"' in html and "Silent killer" in html


def test_render_html_recommendations_and_not_recommended():
    html = vr.render_html(_data())
    assert '<div class="rnum">1</div>' in html
    assert "<b>Why:</b>" in html
    assert "Moves: weekly participation ↑ · gross retention ↑" in html
    assert "<b>Deliberately not recommended:</b>" in html
    assert "wide but calm, no churn, no dollars" in html


def test_render_html_basis_note_only_when_present():
    assert 'class="basisnote"' not in vr.render_html(_data())
    html = vr.render_html(_data(basis_note="Tier-1 basis: volume + frustration only."))
    assert 'class="basisnote"' in html and "Tier-1 basis" in html


def test_render_html_has_no_cta_buttons_or_scripts():
    # The report renders in a script-less sandboxed iframe where nothing is
    # clickable — no dead CTAs, no print button, no onclick handlers.
    html = vr.render_html(_data())
    assert "Generate PRD" not in html
    assert "Move to ideation" not in html and "Move to backlog" not in html
    assert "onclick" not in html and "printbtn" not in html
    assert "can be developed into a PRD" in html  # the soft close line instead


def test_render_html_escapes_model_text():
    html = vr.render_html(_data(findings=[
        {"problem": "x <script>alert(1)</script>", "sentence": "y & z",
         "quote": "", "quote_attr": ""},
    ]))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "y &amp; z" in html


def test_render_html_escapes_radar_axis_labels():
    html = vr.render_html(_data(glance=[
        _glance_row(problem='<img src=x onerror=alert(1)>'),
        _glance_row(problem="B"), _glance_row(problem="C"),
    ]))
    assert "<img src=x" not in html
    assert "&lt;img src=x" in html


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
    # The extraction prompt carries the v3 method anchors.
    assert "CAPTURE" in captured["system"] and "1–5" in captured["system"]
    # Full-window corpora exceed the default request timeout — must stream.
    assert captured["long_output"] is True


def test_build_raises_on_non_dict_output(monkeypatch):
    class _R:
        output = "not a dict"
    monkeypatch.setattr(vr, "llm_call", lambda **k: _R())
    import pytest
    with pytest.raises(ValueError):
        vr.build(enterprise_id="co", question="q", corpus_text="c",
                 source_line="s", model="m")
