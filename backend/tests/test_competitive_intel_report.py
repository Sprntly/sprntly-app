"""Competitive-intelligence report renderer — schema shape, integrity guardrails,
radar geometry, and the defensive normalization.

Pure rendering: no network, LLM or DB. The two behaviours worth pinning are the
ones the skill's own guardrails depend on:

  * an unsourced or untiered number NEVER reaches the page, and
  * mis-shaped model output degrades the section instead of raising.
"""
from __future__ import annotations

import json
import re

import pytest

from app import competitive_intel_report as rep


def _fact(value="$55.0B", source="Q1 10-Q", date="2026-05-01", tier="h"):
    return {"value": value, "source": source, "date": date, "tier": tier}


def _cell(mark="yes", emphasis=False):
    return {"mark": mark, "emphasis": emphasis}


DIMS = ["Reach", "Intent signal", "Creative tooling", "Automation transparency",
        "Trust", "Measurement proof", "Advertiser experience", "Commerce"]


def _radar(caption="Against the scale players"):
    return {
        "caption": caption,
        "dimensions": list(DIMS),
        "series": [
            {"name": "Google", "is_us": False, "scores": [4, 5, 4, 2, 2, 3, 3, 4]},
            {"name": "Us", "is_us": True, "scores": [5, 3, 4, 1, 2, 2, 2, 3]},
        ],
    }


DATA: dict = {
    "title": "Where Acme stands",
    "opening": [{"lead": "The automation race is over.",
                 "text": "Every rival now ships automated buying."}],
    "radars": [_radar(), _radar("Against the specialists")],
    "radar_read": [{"lead": "Read the left chart first.",
                    "text": "Our shape and Google's are nearly identical."}],
    "scale_rows": [
        {"name": "Us", "is_us": True, "revenue": _fact(),
         "growth": _fact("+33%", "Q1 10-Q", "2026-05-01", "h"),
         "differentiator": "Reach", "takes_from_us": ""},
        {"name": "Globex", "is_us": False,
         "revenue": _fact("est. $28–44B", "three analyst notes", "2026", "s"),
         "growth": _fact("", "", "", "s"),
         "differentiator": "Free creative suite",
         "takes_from_us": "Creative-led budget"},
    ],
    "scale_note": "Globex is private; published estimates span $28–44B.",
    "scale_read": "Growth rate is not where we are behind.",
    "position_x_labels": ["Undifferentiated", "Differentiated",
                          "Structurally differentiated"],
    "position_rows": [
        {"label": "High reach", "cells": [
            {"name": "—", "note": "", "is_us": False},
            {"name": "Globex", "note": "Intent is structural.", "is_us": False},
            {"name": "Us", "note": "Largest reach.", "is_us": True},
        ]},
    ],
    "position_read": "We sit top-right on scale.",
    "feature_competitors": ["Globex", "Initech"],
    "feature_rows": [
        {"capability": "Automated campaigns", "emphasis": False, "us": _cell(),
         "cells": [_cell(), _cell()], "status": "Table stakes",
         "status_class": "table-stakes"},
        {"capability": "Automation you can see into", "emphasis": True,
         "us": _cell("no"), "cells": [_cell("partial"), _cell("yes", True)],
         "status": "Initech only", "status_class": "only"},
    ],
    "feature_read": "Four of twelve capabilities are table stakes.",
    "launch_log": [
        {"competitor": "Globex", "entries": [
            {"date": "20 May", "what": "Asset Studio rebuilt",
             "classification": "net-new", "tier": "h", "vendor_reported": False},
            {"date": "Q1", "what": "Auto-bidding, reported 16% improvement",
             "classification": "parity", "tier": "h", "vendor_reported": True},
        ], "pattern": "Four net-new against three parity.",
         "nothing_shipped": False, "window_checked": "Jan–Jul 2026"},
        {"competitor": "Initech", "entries": [], "pattern":
            "A quiet quarter from a fast-moving rival is worth watching.",
         "nothing_shipped": True, "window_checked": "Jan–Jul 2026"},
    ],
    "threats": [
        {"threat": "Discovery moves upstream", "severity": "removes",
         "timing": "now", "defence": "none", "defence_label": "",
         "detail": "Assistants carry more retail spend each quarter.",
         "figures": [{"label": "AI retail spend",
                      "fact": _fact("$20.9B", "eMarketer", "2025-12", "s")}]},
        {"threat": "Rival ad platform matures", "severity": "reshapes",
         "timing": "this-year", "defence": "in-flight",
         "defence_label": "Partial", "detail": "Weak analytics today.",
         "figures": []},
    ],
    "threat_callout": {"label": "The one that matters",
                       "paragraphs": ["Five of seven threats have no defence."]},
    "sentiment_rows": [
        {"name": "Us", "is_us": True,
         "rating": _fact("2.1", "App Store", "2026-07", "h"),
         "review_volume": _fact("18,400", "App Store", "2026-07", "h"),
         "direction": "", "themes": "Reliability, opacity"},
    ],
    "competitor_praise": [{"name": "Initech", "theme": "Transparency",
                           "tier": "h"}],
    "our_quotes": [{"quote": "It freezes and doesn't publish ads.",
                    "attribution": "App Store review", "tier": "h"}],
    "our_themes": [
        {"theme": "Reliability", "description": "Freezing, failed publishes",
         "who_sells_against_it": "", "tier": "h"},
        {"theme": "Opacity", "description": "Vague rejection reasons",
         "who_sells_against_it": "Initech", "tier": "h"},
    ],
    "sentiment_read": "Three of four themes map to a named rival.",
    "not_sourced": "Numeric sentiment across the full set.",
    "review_sections": [],
    "recommendations": [
        {"eyebrow": "01 · Product", "title": "Make delivery legible",
         "from": "Radar · sentiment · threat scan", "do": "Ship an explanation panel",
         "why_now": "Two rivals validated the demand", "measure": "Support contacts",
         "watch": "Do not expose auction mechanics"},
    ],
    "carried_decisions": [
        {"recommendation": "Provenance by default", "status": "in progress",
         "outcome_note": "Design done, build starts next sprint"},
    ],
    "sources": [{"competitor": "Globex", "detail": "Q2 10-Q, filed 22 Jul."}],
    "meta_line": "Window Jan – 26 Jul 2026 · set derived as Globex (direct).",
    "metadata": {"window": "Jan – 26 Jul 2026", "mode": "review"},
    "next_state": {"competitors": {"Globex": {}}, "decisions": []},
}


def _render(**overrides) -> str:
    data = dict(DATA)
    data.update(overrides)
    return rep.render_html(data)


# ── Document shape ───────────────────────────────────────────────────────────

def test_renders_a_self_contained_document_with_every_section():
    html = _render()
    assert html.startswith("<!DOCTYPE html>")
    assert html.rstrip().endswith("</html>")
    assert "Where Acme stands" in html
    # Section order is the skill's Output order and sections are additive: the
    # radar never replaces a benchmark.
    for marker in ("Where we win and where we lose", "Scale benchmark",
                   "Market position", "Feature benchmark", "What they launched",
                   "New markets, new technology", "What customers say",
                   "What to do", "Sources"):
        assert marker in html, f"missing section: {marker}"
    assert html.index("Scale benchmark") < html.index("Feature benchmark")
    assert html.index("What they launched") < html.index("What to do")
    assert "Window Jan – 26 Jul 2026" in html


def test_report_is_iframe_safe_and_script_less():
    """The chat renders reports in a sandboxed iframe with NO allow-scripts, so
    the only <script> permitted is the inert application/json metadata block."""
    html = _render()
    scripts = re.findall(r"<script[^>]*>", html)
    assert scripts == ['<script type="application/json" id="report-metadata">']
    assert "onclick" not in html


def test_metadata_block_is_inert_and_cannot_close_the_script_element():
    html = _render(metadata={"note": "</script><img src=x>", "window": "w"})
    assert "</script><img" not in html
    assert "\\u003c/script" in html
    body = re.search(
        r'<script type="application/json" id="report-metadata">(.*?)</script>',
        html, re.S,
    )
    assert body and json.loads(body.group(1).replace("\\u003c", "<"))["window"] == "w"


def test_model_strings_are_html_escaped():
    html = _render(title="<img src=x onerror=alert(1)>")
    assert "<img src=x" not in html
    assert "&lt;img src=x" in html


# ── Integrity: no unsourced, untiered number reaches the page ────────────────

def test_sourced_fact_renders_with_its_date_and_tier_chip():
    html = _render()
    assert "<strong>$55.0B</strong>" in html
    assert '<span class="t h">H</span>' in html


def test_unsourced_number_renders_unknown_and_never_the_value():
    """A value that arrived with no named source is not printable — the reader
    could not trace it, so the renderer writes "unknown" instead."""
    rows = [dict(DATA["scale_rows"][0],
                 revenue=_fact("$99.9B", source="", date="2026", tier="h"))]
    html = _render(scale_rows=rows)
    assert "$99.9B" not in html
    assert rep._UNKNOWN in html


def test_untiered_number_renders_unknown_so_no_bare_number_appears():
    """The tier chip is not decoration: it is how the reader knows whether a
    figure is observed, estimated, inferred or the company's own claim. A value
    with no valid tier prints as unknown rather than as a bare number."""
    rows = [dict(DATA["scale_rows"][0],
                 revenue=_fact("$77.7B", source="Q1 filing", tier=""))]
    html = _render(scale_rows=rows)
    assert "$77.7B" not in html
    assert rep._UNKNOWN in html
    # ...and an unrecognised tier is treated the same way.
    rows = [dict(DATA["scale_rows"][0],
                 revenue=_fact("$77.7B", source="Q1 filing", tier="x"))]
    assert "$77.7B" not in _render(scale_rows=rows)


def test_empty_value_renders_unknown_not_a_blank_cell():
    html = _render()
    # Globex's growth arrived empty (not disclosed) — the cell says so.
    assert html.count(rep._UNKNOWN) >= 1


def test_vendor_reported_launch_carries_both_chips():
    """Vendor-reported is a SEPARATE axis from confidence, so a hard-sourced
    figure the company published about itself carries H and V."""
    html = _render()
    block = html[html.index("Auto-bidding"):]
    assert '<span class="t h">H</span>' in block[:400]
    assert '<span class="t v">V</span>' in block[:400]


def test_threat_figures_go_through_the_same_gate():
    threats = [dict(DATA["threats"][0], figures=[
        {"label": "spend", "fact": _fact("$1.2T", source="", tier="s")}])]
    html = _render(threats=threats)
    assert "$1.2T" not in html


# ── Skill-specific rendering rules ───────────────────────────────────────────

def test_defence_none_is_written_as_none():
    """"None" is written when it is true — the most useful word in the stage."""
    html = _render()
    assert '<span class="def ">None</span>' in html
    assert '<span class="sev rm">Removes us</span>' in html
    assert '<span class="def some">Partial</span>' in html


def test_competitor_that_shipped_nothing_is_reported_with_the_window():
    """Silence from a fast-moving rival is a finding, never an omitted section."""
    html = _render()
    assert "Nothing shipped" in html
    assert "Jan–Jul 2026" in html
    assert "A quiet quarter from a fast-moving rival" in html


def test_theme_with_no_rival_selling_against_it_reads_as_pure_loss():
    html = _render()
    assert "Nobody &mdash; pure loss" in html
    assert "<strong>Initech</strong>" in html


def test_carried_decisions_render_with_status():
    html = _render()
    assert "Carried forward from the last run" in html
    assert "in progress" in html
    assert "Design done, build starts next sprint" in html


def test_review_sections_render_only_in_review_mode():
    assert "The strategic picture" not in _render()
    html = _render(review_sections=[
        {"title": "The arena", "paragraphs": ["Five forces read."]}])
    assert "The strategic picture" in html
    assert "Five forces read." in html


def test_recommendation_rows_carry_from_do_why_measure_watch():
    html = _render()
    for label in ("From", "Do", "Why now", "Measure", "Watch"):
        assert f'<span class="rk">{label}</span>' in html


# ── Radar geometry (computed here; the model never draws) ────────────────────

def test_two_radars_are_rendered_side_by_side():
    html = _render()
    assert html.count("<svg viewBox=\"0 0 500 470\"") == 2
    assert "Against the scale players" in html
    assert "Against the specialists" in html


def test_radar_axis_zero_is_top_and_full_score_hits_the_outer_ring():
    x, y = rep._radar_point(5, 0, 8)
    assert (round(x, 1), round(y, 1)) == (250.0, 85.0)
    x, y = rep._radar_point(5, 2, 8)
    assert (round(x, 1), round(y, 1)) == (400.0, 235.0)   # clockwise, right
    # centre for a zero score, and out-of-range scores clamp rather than escape.
    assert rep._radar_point(0, 3, 8) == (pytest.approx(250.0), pytest.approx(235.0))
    assert rep._radar_point(99, 0, 8) == rep._radar_point(5, 0, 8)


def test_us_series_always_takes_the_first_colour():
    """The reader has to find us on the chart instantly, so "us" is green
    regardless of the order the model listed the series in."""
    html = _render()
    # Series polygons are the filled ones (rings and spokes are stroke-only).
    series = re.findall(r'<polygon [^>]*fill-opacity="0\.10"[^>]*>', html)
    assert series, "no series polygon rendered"
    assert 'fill="#1A6B47"' in series[0]
    # The model listed Google first; "us" still won the green slot.
    assert 'fill="#9C3223"' in series[1]


def test_radar_with_too_few_dimensions_is_omitted_not_broken():
    html = _render(radars=[{"caption": "thin", "dimensions": ["A", "B"],
                            "series": [{"name": "Us", "is_us": True,
                                        "scores": [1, 2]}]}])
    assert "<svg" not in html
    assert "Where we win and where we lose" not in html
    # the rest of the report still renders
    assert "Scale benchmark" in html


def test_series_with_missing_scores_is_padded_not_dropped():
    radar = _radar()
    radar["series"] = [{"name": "Us", "is_us": True, "scores": [5, 4]}]
    html = _render(radars=[radar])
    assert html.count("<svg") == 1


# ── Defensive normalization (#928: 'str' has no attribute 'get') ─────────────

def test_string_where_an_object_was_specified_loses_the_row_not_the_report():
    html = _render(
        scale_rows=["Globex is big", {"name": "Us", "is_us": True,
                                      "revenue": _fact(), "growth": _fact("+1%"),
                                      "differentiator": "d", "takes_from_us": ""}],
        threats=["a threat, as prose"],
        recommendations=["do the thing"],
        our_themes="not a list",
        launch_log=[{"competitor": "Globex", "entries": "prose",
                     "pattern": "p", "nothing_shipped": False,
                     "window_checked": "w"}],
    )
    assert html.startswith("<!DOCTYPE html>")
    assert "$55.0B" in html                 # the well-formed row survived
    assert "Nothing shipped" in html        # entries="prose" → treated as silence


def test_completely_empty_payload_renders_a_document():
    html = rep.render_html({})
    assert html.startswith("<!DOCTYPE html>")
    assert "Scale benchmark" not in html    # empty sections are omitted
    assert "report-metadata" in html


def test_none_payload_does_not_raise():
    assert rep.render_html(None).startswith("<!DOCTYPE html>")


def test_non_numeric_scores_do_not_raise():
    radar = _radar()
    radar["series"] = [{"name": "Us", "is_us": True,
                        "scores": ["high", None, 3, 3, 3, 3, 3, 3]}]
    assert "<svg" in _render(radars=[radar])


# ── Schema contract ──────────────────────────────────────────────────────────

def test_schema_forces_value_source_date_tier_on_every_quantitative_field():
    props = rep.SCHEMA["properties"]
    required = set(rep.SCHEMA["required"])
    assert {"scale_rows", "threats", "sentiment_rows", "next_state",
            "metadata"} <= required

    def _fact_shape(node: dict) -> None:
        assert set(node["required"]) == {"value", "source", "date", "tier"}
        assert node["properties"]["tier"]["enum"] == ["h", "s", "i", "v"]

    scale = props["scale_rows"]["items"]["properties"]
    _fact_shape(scale["revenue"])
    _fact_shape(scale["growth"])
    sentiment = props["sentiment_rows"]["items"]["properties"]
    _fact_shape(sentiment["rating"])
    _fact_shape(sentiment["review_volume"])
    _fact_shape(props["threats"]["items"]["properties"]["figures"]["items"]
                ["properties"]["fact"])


def test_schema_pins_the_skills_enumerations():
    props = rep.SCHEMA["properties"]
    launch = props["launch_log"]["items"]["properties"]["entries"]["items"]
    assert launch["properties"]["classification"]["enum"] == [
        "net-new", "parity", "deprecation", "beta", "market"]
    threat = props["threats"]["items"]["properties"]
    assert threat["severity"]["enum"] == ["dents", "reshapes", "removes"]
    assert threat["timing"]["enum"] == ["now", "this-year", "watch"]
    assert threat["defence"]["enum"] == ["named", "in-flight", "none"]
    assert props["carried_decisions"]["items"]["properties"]["status"]["enum"] == [
        "open", "in progress", "done", "dropped"]
    # All three benchmarks are mandatory keys — the radar cannot substitute.
    for key in ("scale_rows", "position_rows", "feature_rows", "radars"):
        assert key in rep.SCHEMA["required"]
