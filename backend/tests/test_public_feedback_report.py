"""public-feedback-report template — structured data → pinned HTML.

`render_html` is pure and deterministic. These tests pin the markup contract
(brandmark, count strip, five-point TL;DR, template-drawn monthly SVG chart
with gap handling, problems table, new/stuck/fixed beside the mix panel,
platform and competitor cuts, product-only recommendations, non-product
section, fixed next-steps block, collapsed coverage note, and the
machine-readable #report-metadata block), the XSS-escaping of all model text,
and that no dead buttons/scripts are emitted for the script-less iframe.
"""
from __future__ import annotations

import json

import app.public_feedback_report as pfr


def _month(label="", pos=0, neu=0, neg=0, event=""):
    return {"label": label, "pos": pos, "neu": neu, "neg": neg, "event": event}


def _data(**over) -> dict:
    d = {
        "eyebrow": "Public feedback · January – July 2026",
        "prepared": "Prepared 26 July 2026 · first edition",
        "covers": "feedback people posted publicly about us this year.",
        "looked": "Trustpilot, the App Store, Reddit, YouTube and X.",
        "answering": "what should the product team fix next?",
        "counts": {"collected": 52, "product": 39, "non_product": 13,
                   "sources": 11, "leaving": 2},
        "tldr_source_line": "Everything below is something a product team can change.",
        "tldr_intro": "Three problems are doing most of the damage:",
        "tldr": [
            {"headline": "\"My real rides get flagged.\"",
             "sub": "The detector catches real activities.",
             "quote": "numerous legitimate activities flagged",
             "quote_attr": "Trustpilot, 24 Jan 2026"},
            {"headline": "Leaving over data sharing.",
             "sub": "Two said so plainly.", "quote": "", "quote_attr": ""},
        ],
        "first_move": "Let people appeal a flagged activity in the app.",
        "months": [
            _month("Aug 24"),                       # gap month
            _month("", 0, 2, 1),
            _month("Nov", 0, 2, 8, event="API terms change"),
            _month("Jul", 1, 0, 1),
        ],
        "chart_kpis": [
            {"label": "Busiest month", "value": "Jan 2026",
             "detail": "10 posts", "tone": "flat"},
            {"label": "Last three months", "value": "Negative",
             "detail": "4 negative", "tone": "down"},
        ],
        "chart_note": "Two spikes stand out.",
        "chart_callout": "We only found feedback in 14 of these 24 months.",
        "ratings_kpis": [
            {"label": "Our App Store rating", "value": "4.8★",
             "detail": "~306k ratings", "tone": "up"},
        ],
        "ratings_callout": "The App Store catches us on a good day.",
        "problems": [
            {"voice": "My real rides get flagged and I can't get the flag removed.",
             "gloss": "False positives with no appeal · Product and Engineering",
             "how_often": "Most often", "mood": "neg", "mood_label": "Negative",
             "direction": "up", "direction_label": "▲ new, rising",
             "quote": "numerous legitimate activities flagged",
             "quote_attr": "Trustpilot · 24 Jan 2026"},
        ],
        "new_items": [{"text": "Real activities wrongly flagged",
                       "detail": "Started Jan 2026 · growing"}],
        "stuck_items": [{"text": "No way to turn off the AI write-up",
                         "detail": "Raised since Oct 2024 · still open"}],
        "fixed_items": [{"text": "\"The leaderboards are full of cheats\"",
                         "detail": "Went quiet after the January cleanup"}],
        "mix": {"product_pos": 6, "product_neu": 8, "product_neg": 25,
                "non_pos": 0, "non_neu": 6, "non_neg": 7,
                "note": "Nobody praises an invoice — treat that 0% as normal."},
        "progress_callout": "Don't skip the green column.",
        "platforms": [
            {"name": "Trustpilot", "audience": "People with an unresolved problem",
             "mood": "neg", "mood_label": "Most negative",
             "what": "Flags, lockouts, billing complaints"},
        ],
        "platforms_note": "These groups describe different moments.",
        "compare_title": "How we compare vs Garmin Connect & Komoot",
        "why_these": "Garmin is the app people name most when leaving.",
        "us_name": "Strava (us)",
        "us_loves": [{"title": "Segments", "detail": "nobody else has the game"}],
        "rivals": [
            {"name": "Garmin Connect",
             "loves": [{"title": "Analytics depth", "detail": "rivals paid tiers"}]},
            {"name": "Komoot",
             "loves": [{"title": "Route planning", "detail": "the benchmark"}]},
        ],
        "dimensions": [
            {"name": "Analytics depth", "us": "Decent, but paid-only",
             "us_mood": "mix",
             "cells": [{"text": "Positive", "mood": "pos"},
                       {"text": "Not their game", "mood": "neu"}],
             "ahead": "a", "ahead_label": "Garmin — clear"},
        ],
        "switching": "Two people said it plainly, both naming Garmin.",
        "recs_intro": "Ordered by how many people it affects.",
        "recommendations": [
            {"title": "Self-serve appeal for flagged activities",
             "prio_label": "High impact", "prio": "hi",
             "problem_line": "My real rides get flagged.",
             "why": "False positives land on the athletes who care most.",
             "moves": "power-user retention ↑ · support volume ↓"},
        ],
        "non_product_intro": "13 of the 52 posts aren't things the product team can fix.",
        "non_product_items": [
            {"tag": "Support", "title": "Users can't reach a human",
             "detail": "Multi-week replies.", "owner_line": "Owned by Support · 4 posts"},
        ],
        "next_steps": "#1 and #3 are the pair to move on.",
        "integrity": [{"title": "How this was made.",
                       "text": "We logged each piece of feedback separately."}],
        "metadata": {"generated_by": "Sprntly", "totals": {"collected": 52},
                     "by_source": {"Trustpilot": {"total": 17}}},
    }
    d.update(over)
    return d


# ── section contract ─────────────────────────────────────────────────────────

def test_renders_all_pinned_sections():
    html = pfr.render_html(_data())
    for marker in [
        "Generated by <b>Sprntly</b>",
        "What people are saying about us online",
        "Public feedback · January – July 2026",
        "Feedback collected",
        "Said they're leaving",
        "TL;DR",
        "What we'd do first:",
        "How feedback has looked over time",
        "The problems people are running into",
        "What's new, what's stuck, what's fixed",
        "Looks fixed — people have stopped bringing it up",
        "What the feedback is about",
        "By platform",
        "How we compare vs Garmin Connect &amp; Komoot",
        "⚠ Who said they're leaving.",
        "Recommendations",
        "Also worth a look — not for the product team",
        "Next steps",
        "use <b>Sprntly</b> to turn them into a PRD",
        "Where this came from, and how much to trust it",
        'id="report-metadata"',
    ]:
        assert marker in html, marker


def test_count_strip_percentages_computed_here():
    html = pfr.render_html(_data())
    assert "39 <small>75%</small>" in html
    assert "13 <small>25%</small>" in html


def test_mix_panel_percentages_and_denominator():
    html = pfr.render_html(_data())
    # 25 of 39 product posts negative → 64%
    assert "<b>64%</b> negative" in html
    assert "Out of the <b>52 pieces of feedback</b>" in html


def test_zero_collected_never_divides():
    d = _data(counts={"collected": 0, "product": 0, "non_product": 0,
                      "sources": 0, "leaving": 0},
              mix={"product_pos": 0, "product_neu": 0, "product_neg": 0,
                   "non_pos": 0, "non_neu": 0, "non_neg": 0, "note": ""})
    html = pfr.render_html(d)
    assert "0 <small>posts</small>" in html


def test_compare_section_omitted_when_no_title():
    html = pfr.render_html(_data(compare_title="", rivals=[], dimensions=[]))
    assert "How we compare" not in html
    assert "Who said they&#x27;re leaving" not in html


# ── monthly chart ────────────────────────────────────────────────────────────

def test_chart_gap_months_render_flat_lines_and_note():
    html = pfr.render_html(_data())
    assert "flat line = no feedback collected that month (1 of the 4 months)" in html


def test_chart_event_annotation_drawn():
    html = pfr.render_html(_data())
    assert "API terms change" in html
    assert 'stroke-dasharray="2 3"' in html


def test_chart_stacks_sentiment_colors():
    html = pfr.render_html(_data())
    for color in ("#1f7a5a", "#d9a441", "#bb463c"):
        assert f'fill="{color}"' in html


def test_no_months_no_chart():
    html = pfr.render_html(_data(months=[]))
    assert '<div class="chartwrap">' not in html


# ── safety: escaping, script-less iframe ─────────────────────────────────────

def test_all_model_text_is_escaped():
    evil = '<script>alert(1)</script>'
    d = _data(
        covers=evil, tldr_intro=evil, first_move=evil, switching=evil,
        next_steps=evil, non_product_intro=evil,
        tldr=[{"headline": evil, "sub": evil, "quote": evil, "quote_attr": evil}],
        problems=[{"voice": evil, "gloss": evil, "how_often": evil,
                   "mood": "neg", "mood_label": evil, "direction": "up",
                   "direction_label": evil, "quote": evil, "quote_attr": evil}],
        months=[_month("Aug", 1, 0, 0, event=evil)],
        integrity=[{"title": evil, "text": evil}],
    )
    html = pfr.render_html(d)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_metadata_block_cannot_be_closed_by_model_strings():
    d = _data(metadata={"note": "</script><script>alert(1)</script>"})
    html = pfr.render_html(d)
    # the only raw <script is the JSON block itself
    assert html.count("<script") == 1
    assert "\\u003c/script" in html
    # and it round-trips as JSON
    payload = html.split('id="report-metadata">', 1)[1].split("</script>", 1)[0]
    assert json.loads(payload)["note"] == "</script><script>alert(1)</script>"


def test_no_dead_interactive_elements():
    html = pfr.render_html(_data())
    assert "<button" not in html
    assert "onclick" not in html
    # collapse works without JS
    assert "<details" in html and "<summary>" in html


def test_metadata_default_empty_object():
    html = pfr.render_html(_data(metadata={}))
    payload = html.split('id="report-metadata">', 1)[1].split("</script>", 1)[0]
    assert json.loads(payload) == {}
