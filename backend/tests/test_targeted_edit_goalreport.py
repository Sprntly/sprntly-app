"""Tests for the goal-report targeted-edit adapter (`GOALREPORT_SECTION_MODEL`)
and its dispatch inside `goal_report_chat_edit.apply_report_edit`.

Two layers:
  - Pure-module: the `<h2>`-delimited, wrapper-less, count-heading adapter on a
    real-shaped (synthetic-content) goal report — tokenize incl. the two count
    headings, targeted replace of a small section splices byte-clean, a
    count-heading edit resolves and REPLACES, a conditional-section delete, a
    hand-edited/non-house report falls back, the count-strip normalize, and the
    dedicated sub-gate. These need only `app.targeted_edit` (stdlib-only).
  - Dispatch (CI): `apply_report_edit` flag-off = today's full-emit
    (byte-identical), flag-on splices, flag-on-bad → fallback, and the anchor
    REPLACE-not-append guard on the REAL prompt. These import
    `app.goal_report_chat_edit` (needs fastapi/app deps), so they run in CI.

All names in fixtures are synthetic.
"""
from __future__ import annotations

import re

import pytest

import app.targeted_edit as te
from app.targeted_edit import GOALREPORT_SECTION_MODEL as G, FallbackNeeded


# A synthetic goal report with the real render shape: <h1> preamble, then <h2>
# sections concatenated with NO inter-section whitespace ("".join in
# render_report_html._assemble), no wrapper. Includes BOTH count headings
# ("What the evidence says (N)", "Considered and ruled out (N)") and the <h3>
# sub-headings that ride inside their parent <h2>. Synthetic content only.
REPORT = (
    "<h1>Do reminder emails reduce late invoice payments?</h1>"
    "<h2>What this was asked to establish</h2>"
    "<blockquote>The question as posed.</blockquote><p>Scope note here.</p>"
    "<h2>What was read</h2><ul><li>Source one.</li><li>Source two.</li></ul>"
    "<h3>What was missing from it</h3><ul><li>A coverage gap.</li></ul>"
    "<h2>The short version</h2><p>Reminders correlate with faster payment.</p>"
    "<h2>What the evidence says (2)</h2>"
    "<h3>1. First finding.</h3><p>Detail.</p><h3>2. Second finding.</h3><p>Detail.</p>"
    "<h2>What you already believed</h2><ul><li>A prior belief.</li></ul><p>How it held.</p>"
    "<h2>Considered and ruled out (1)</h2><ul><li>An alternative, ruled out.</li></ul>"
    "<h2>What this cannot tell you</h2><ul><li>A stated limit.</li></ul>"
)

_SPINE = [
    "What this was asked to establish", "What was read", "The short version",
    "What the evidence says (2)", "What you already believed",
    "Considered and ruled out (1)", "What this cannot tell you",
]


# ── sub-gate ─────────────────────────────────────────────────────────────────

def test_goalreport_flag_default_off(monkeypatch):
    monkeypatch.delenv("TARGETED_EDIT_GOALREPORT_ENABLED", raising=False)
    assert te.goalreport_enabled() is False


@pytest.mark.parametrize("val,expected", [
    ("1", True), ("true", True), ("On", True), ("0", False), ("", False)])
def test_goalreport_flag_parsing(monkeypatch, val, expected):
    monkeypatch.setenv("TARGETED_EDIT_GOALREPORT_ENABLED", val)
    assert te.goalreport_enabled() is expected


def test_goalreport_gate_is_independent_of_prd_gate(monkeypatch):
    # Turning the PRD flag on must NOT enable goal-report (staged separately).
    monkeypatch.setenv("TARGETED_EDIT_ENABLED", "1")
    monkeypatch.delenv("TARGETED_EDIT_GOALREPORT_ENABLED", raising=False)
    assert te.goalreport_enabled() is False
    assert te.enabled() is True


# ── tokenizer ────────────────────────────────────────────────────────────────

def test_tokenize_h1_preamble_h2_sections_empty_suffix():
    preamble, sections, suffix = te._tokenize(REPORT, G)
    assert preamble == "<h1>Do reminder emails reduce late invoice payments?</h1>"
    assert suffix == ""  # no wrapper — net_open == 0 branch
    assert [n for n, _ in sections] == _SPINE
    # lossless
    assert preamble + "".join(b for _, b in sections) + suffix == REPORT


def test_h3_subheadings_are_not_delimiters():
    # <h3> ride inside their parent <h2>; they are NOT addressable sections.
    _, sections, _ = te._tokenize(REPORT, G)
    read = dict(sections)["What was read"]
    assert "<h3>What was missing from it</h3>" in read  # stays inside the parent
    findings = dict(sections)["What the evidence says (2)"]
    assert "<h3>1. First finding.</h3>" in findings


def test_non_house_report_with_no_h2_falls_back():
    with pytest.raises(FallbackNeeded, match="gate0"):
        te.apply_targeted_edit(
            "<h1>G</h1><p>a freeform hand-edited report, no headings left</p>",
            [{"op": "replace", "section": "X", "new_html": "<h2>X</h2><p>y</p>"}], G)


# ── count-heading normalize (the count_heading=True path) ─────────────────────

def test_count_heading_normalize_strips_count():
    assert G.normalize("What the evidence says (2)") == "what the evidence says"
    assert G.normalize("What the evidence says") == "what the evidence says"
    assert G.normalize("Considered and ruled out (1)") == "considered and ruled out"
    # static headings unaffected
    assert G.normalize("The short version") == "the short version"


# ── targeted splices (small section, count heading, delete) ──────────────────

def test_replace_small_section_splices_others_byte_identical():
    _, sections, _ = te._tokenize(REPORT, G)
    new_sv = ("<h2>The short version</h2>"
              "<p>Reminders strongly correlate with faster payment, for execs.</p>")
    out = te.apply_targeted_edit(
        REPORT, [{"op": "replace", "section": "The short version", "new_html": new_sv}], G)
    assert "for execs" in out
    for name, block in sections:
        if name == "The short version":
            continue
        assert block in out, f"{name} not byte-identical"
    assert te._is_well_formed(out)


def test_replace_limits_section_splices():
    new_limits = ("<h2>What this cannot tell you</h2>"
                  "<ul><li>A stated limit.</li><li>A second stated limit.</li></ul>")
    out = te.apply_targeted_edit(
        REPORT, [{"op": "replace", "section": "What this cannot tell you",
                  "new_html": new_limits}], G)
    assert "A second stated limit." in out
    assert out.count("<h2>") == 7
    assert "<h2>The short version</h2><p>Reminders correlate" in out  # untouched


def test_count_heading_edit_resolves_and_replaces_not_appends():
    # The critical count path: the op names the heading WITHOUT the count and the
    # payload carries a CHANGED count — both must normalize to the same anchor,
    # and the section must be REPLACED (not appended, which would double the h2).
    new_find = ("<h2>What the evidence says (3)</h2>"
                "<h3>1. First finding REVISED.</h3><h3>2. b</h3><h3>3. c</h3>")
    out = te.apply_targeted_edit(
        REPORT, [{"op": "replace", "section": "What the evidence says",
                  "new_html": new_find}], G)
    assert "REVISED" in out and "<h3>3. c</h3>" in out
    assert out.count("<h2>What the evidence says") == 1  # replaced, not appended
    assert out.count("<h2>") == 7


def test_delete_conditional_section_via_count_normalized_name():
    out = te.apply_targeted_edit(
        REPORT, [{"op": "delete", "section": "Considered and ruled out"}], G)
    assert "Considered and ruled out" not in out
    assert out.count("<h2>") == 6
    assert te._is_well_formed(out)


def test_gate2_wrong_heading_payload_falls_back():
    with pytest.raises(FallbackNeeded, match="gate2"):
        te.apply_targeted_edit(
            REPORT, [{"op": "replace", "section": "The short version",
                      "new_html": "<h2>What was read</h2><p>mismatch</p>"}], G)


def test_all_sections_idempotent_reproduce_the_report():
    # Replacing each section with its own current block reproduces the doc
    # byte-for-byte (no drift, wrapper-less/empty-suffix handling correct).
    _, sections, _ = te._tokenize(REPORT, G)
    for name, block in sections:
        out = te.apply_targeted_edit(
            REPORT, [{"op": "replace", "section": name, "new_html": block}], G)
        assert out == REPORT, f"{name} idempotent-replace changed the report"


# ── the anchor REPLACE-not-append guard (pure, representative prompt) ─────────

def test_targeted_system_replaces_prompt_without_the_word_document():
    # Goal-report's prompt says "Return the FULL updated HTML in `html`, …" —
    # WITHOUT "document". The shared anchor must still REPLACE it (broadened to
    # make "document" optional); appending would leave the model with two
    # contradictory instructions and silently defeat splicing.
    base = ("Editing discipline here.\n\nReturn the FULL updated HTML in `html`, "
            "the human-readable section names you changed in `sections_changed`, "
            "and a one-line `summary` of the edit.")
    out = te.targeted_system(base, G)
    assert "Return the FULL updated HTML in `html`" not in out  # replaced, not appended
    assert "OUTPUT CONTRACT (targeted edit)" in out
    assert "Do NOT re-emit the whole document" in out
    assert "Editing discipline here." in out  # discipline preserved


def test_broadened_anchor_still_replaces_prd_prompt_with_document():
    # Regression: the broadened anchor must still match the PRD prompt (which
    # DOES carry "document"), so the PRD path is unaffected.
    from app.targeted_edit import PRD_SECTION_MODEL as P
    base = ("disc.\n\nReturn the FULL updated HTML document in `html`, the list "
            "of human-readable section names you changed in `sections_changed` "
            '(e.g. ["Requirements", "Goal"]), and a one-line `summary` of the edit.')
    out = te.targeted_system(base, P)
    assert "Return the FULL updated HTML document" not in out
    assert "OUTPUT CONTRACT (targeted edit)" in out


# ── dispatch inside goal_report_chat_edit (CI: needs fastapi/app deps) ────────

def _llm_result(output):
    from app.graph.gateway import LLMResult
    return LLMResult(
        output=output, model="claude-sonnet-4-6", prompt_version="v",
        input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.001, latency_ms=5,
        stop_reason="end_turn",
    )


def test_apply_report_edit_flag_off_is_full_emit(monkeypatch):
    # Prod-safety invariant: with the sub-gate unset, apply_report_edit takes its
    # EXACT current full-emit path — same schema, prompt, purpose, max_tokens —
    # and the targeted schema/prompt/splice are never constructed.
    import app.goal_report_chat_edit as gr
    monkeypatch.delenv("TARGETED_EDIT_GOALREPORT_ENABLED", raising=False)
    seen = {}

    def _fake(**kw):
        seen.update(kw)
        return _llm_result({"html": "<h1>G</h1><h2>The short version</h2><p>x</p>",
                            "sections_changed": ["The short version"], "summary": "did it"})

    monkeypatch.setattr(gr, "llm_call", _fake)
    out = gr.apply_report_edit(REPORT, "tighten the summary", enterprise_id="co")
    assert out == {"html": "<h1>G</h1><h2>The short version</h2><p>x</p>",
                   "sections_changed": ["The short version"], "summary": "did it"}
    assert seen["prompt_version"] == gr.EDIT_PROMPT_VERSION  # no -targeted suffix
    assert seen["json_schema"] is gr._EDIT_SCHEMA
    assert seen["system"] is gr._EDIT_SYSTEM  # no targeted rewrite
    assert seen["max_tokens"] == 32000
    assert seen["purpose"] == "apply_goal_report_chat_edit"


def test_apply_report_edit_flag_on_targeted_splices(monkeypatch):
    import app.goal_report_chat_edit as gr
    monkeypatch.setenv("TARGETED_EDIT_GOALREPORT_ENABLED", "1")

    def _fake(**kw):
        return _llm_result({
            "mode": "targeted", "summary": "tightened the summary",
            "ops": [{"op": "replace", "section": "The short version",
                     "new_html": "<h2>The short version</h2><p>Tightened for execs.</p>"}],
        })

    monkeypatch.setattr(gr, "llm_call", _fake)
    out = gr.apply_report_edit(REPORT, "tighten the summary", enterprise_id="co")
    assert "Tightened for execs." in out["html"]
    assert "<h2>What the evidence says (2)</h2>" in out["html"]  # untouched, byte-identical
    assert out["sections_changed"] == ["The short version"]
    assert out["summary"] == "tightened the summary"


def test_apply_report_edit_flag_on_bad_targeted_falls_back(monkeypatch):
    import app.goal_report_chat_edit as gr
    monkeypatch.setenv("TARGETED_EDIT_GOALREPORT_ENABLED", "1")
    calls = []

    def _fake(**kw):
        calls.append(kw["prompt_version"])
        if kw["json_schema"] is te.TARGETED_EDIT_SCHEMA:
            # targeted attempt names a section that does not exist -> gate1 fallback
            return _llm_result({
                "mode": "targeted", "summary": "x",
                "ops": [{"op": "replace", "section": "Nonexistent heading",
                         "new_html": "<h2>Nonexistent heading</h2><p>y</p>"}]})
        return _llm_result({"html": "<h1>G</h1><h2>The short version</h2><p>fb</p>",
                            "sections_changed": ["The short version"], "summary": "fell back"})

    monkeypatch.setattr(gr, "llm_call", _fake)
    out = gr.apply_report_edit(REPORT, "reword the summary", enterprise_id="co")
    assert out["summary"] == "fell back" and "fb" in out["html"]
    assert calls == [f"{gr.EDIT_PROMPT_VERSION}-targeted", gr.EDIT_PROMPT_VERSION]


def test_real_goalreport_prompt_anchor_is_replaced_not_appended():
    # The must-fix gotcha, on the REAL prompt: targeted_system must REPLACE the
    # goal-report full-emit sentence (which lacks the word "document"), not append
    # the ops contract beside it. The append path is silent except a log line.
    import app.goal_report_chat_edit as gr
    out = te.targeted_system(gr._EDIT_SYSTEM, te.GOALREPORT_SECTION_MODEL)
    assert "Return the FULL updated HTML in `html`" not in out
    assert "OUTPUT CONTRACT (targeted edit)" in out
    assert "Do NOT re-emit the whole document" in out
    # discipline from the base prompt survives
    assert 'Could not be sized' in out
