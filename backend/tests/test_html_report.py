"""app.html_report — server-side reading of our own HTML report documents.

The Slack surface has no iframe, so it needs to recognise an HTML answer and say
something useful about it in plain text. These helpers run inside a
fire-and-forget delivery task, so the contract is "degrade, never raise".
"""
from __future__ import annotations

import pytest

from app import html_report as hr

CIR_DOC = (
    '<!DOCTYPE html>\n<html lang="en"><head><style>body{color:red}</style></head>'
    "<body><h1>Where Acme stands</h1>"
    '<div class="opener"><b>The automation race is over and everyone finished.</b>'
    " Every platform we compete with now ships automated buying with AI creative."
    "</div>"
    '<div class="opener">Discovery is moving upstream into AI assistants, and we '
    "own no surface in that journey today.</div>"
    '<div class="opener">A third opener that should not make the summary.</div>'
    '<script type="application/json" id="report-metadata">'
    '{"window": "Jan \\u2013 26 Jul 2026", "mode": "scan"}</script>'
    "</body></html>"
)

PF_DOC = (
    '<!DOCTYPE html><html><body><h1>What people are saying about us online</h1>'
    '<p class="src">short label</p>'
    '<p class="intro">Reliability complaints are the loudest theme this period, '
    "and they are getting worse rather than better.</p>"
    "</body></html>"
)


# ── looks_like_html_report (port of looksLikeHtmlBrief) ──────────────────────

@pytest.mark.parametrize("payload", [
    CIR_DOC,
    "<!doctype html><html></html>",
    '<div class="page">x</div>',
    "<style>body{}</style>",
    "<meta charset=utf-8>",
    "  \n<html></html>",
    "```html\n<!DOCTYPE html><html></html>\n```",
])
def test_detects_html_documents(payload):
    assert hr.looks_like_html_report(payload)


@pytest.mark.parametrize("payload", [
    "", None, "Onboarding is the top theme this week.",
    "Here's a summary. <div>with an inline tag</div>",
    "1. First point\n2. Second point",
])
def test_prose_answers_are_not_html_reports(payload):
    assert not hr.looks_like_html_report(payload)


def test_fence_stripping_matches_the_frontend():
    assert hr.strip_html_fence("```html\n<html></html>\n```") == "<html></html>"
    assert hr.strip_html_fence("plain") == "plain"
    assert hr.strip_html_fence(None) == ""


# ── summarize_report ────────────────────────────────────────────────────────

def test_summary_uses_the_documents_own_title_window_and_opening():
    out = hr.summarize_report(CIR_DOC)
    assert out.startswith("*Where Acme stands*")
    assert "_Jan – 26 Jul 2026_" in out
    assert "The automation race is over and everyone finished." in out
    assert "Discovery is moving upstream" in out
    # Two lead paragraphs only — this is a Slack message, not the report.
    assert "third opener" not in out
    # No markup or entities survive.
    assert "<" not in out and "&" not in out


def test_summary_falls_back_to_the_intro_paragraph_shape():
    out = hr.summarize_report(PF_DOC)
    assert "*What people are saying about us online*" in out
    assert "Reliability complaints are the loudest theme" in out
    # Short template labels are scaffolding, not findings.
    assert "short label" not in out


def test_summary_is_length_capped():
    doc = ('<h1>T</h1><div class="opener">' + ("word " * 500) + "</div>")
    out = hr.summarize_report(doc, limit=200)
    assert len(out) <= 200
    assert out.endswith("…")


def test_summary_of_an_unreadable_document_is_empty_not_an_exception():
    assert hr.summarize_report("") == ""
    assert hr.summarize_report(None) == ""
    assert hr.summarize_report("<html><body></body></html>") == ""


def test_summary_survives_a_document_with_no_title():
    out = hr.summarize_report(
        '<div class="opener">A finding long enough to clear the scaffolding '
        "filter and be worth posting.</div>"
    )
    assert out.startswith("A finding long enough")


# ── report_metadata ─────────────────────────────────────────────────────────

def test_metadata_block_is_read_back_with_the_escape_undone():
    assert hr.report_metadata(CIR_DOC)["mode"] == "scan"


def test_metadata_absent_or_invalid_yields_an_empty_dict():
    assert hr.report_metadata("<html></html>") == {}
    assert hr.report_metadata(
        '<script type="application/json" id="report-metadata">not json</script>'
    ) == {}
    assert hr.report_metadata(
        '<script type="application/json" id="report-metadata">[1,2]</script>'
    ) == {}
    assert hr.report_metadata(None) == {}
