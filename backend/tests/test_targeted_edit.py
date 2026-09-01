"""Tests for the targeted-edit output contract (`app.targeted_edit`) and its
dispatch inside the PRD scoped editors (`app.prd_edit`).

Two layers:
  - The pure splice engine + six validation gates + flag + prompt derivation,
    exercised directly on a synthetic house-format PRD (no app/LLM/DB stack —
    the module is stdlib-only by design). Each gate is proven to force a
    `FallbackNeeded`, and the happy paths prove the splice is byte-correct on the
    unchanged sections.
  - The `apply_chat_edit` dispatch: flag OFF is the current
    full-emit path (unchanged return shape); flag ON with a good targeted
    response splices; flag ON with a bad targeted response transparently falls
    back to the full-emit call.

All names in fixtures are synthetic.
"""
from __future__ import annotations

import re

import pytest

import app.prd_edit as prd_edit
import app.targeted_edit as te
from app.graph.gateway import LLMResult
from app.targeted_edit import PRD_SECTION_MODEL as M, FallbackNeeded


# A synthetic house-format PRD (v4.8 shape): DOCTYPE + frame + page wrapper,
# `<div class="eyebrow">` delimiters, a `.riskiest` block in the last section.
DOC = (
    '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>T</title>'
    '<style></style></head><body><div class="frame">\n'
    '<div class="page" contenteditable="true"><h1>Speed up widget sync</h1>'
    '<div class="byline"><span class="bk">Author</span>Alex Rivera</div>'
    '<div class="eyebrow">Context</div><p>Context body here.</p>'
    '<div class="eyebrow">Problem</div><p>Problem body that is long enough here.</p>'
    '<div class="eyebrow">Goal</div><div class="goal"><div class="row">'
    '<span class="k">Primary metric</span><span>sync latency</span></div></div>'
    '<div class="eyebrow">Risks</div><p>Risks body describing named risks.</p>'
    '<div class="riskiest"><p>one riskiest assumption</p></div></div>\n'
    '</div></body></html>'
)


# A synthetic PRD carrying the legacy v4.7 "User input needed" appendix — a
# `<div class="appendix">` block (labelled "Appendix", holding an
# `<h3>User input needed</h3>` + `<ul class="inputs">`) nested as the final block
# INSIDE the last `Risks` eyebrow section, with no eyebrow delimiter of its own.
# This is the real in-production shape that made the editor fall back on
# every such document before the secondary-delimiter fix. Synthetic content only.
APPENDIX_DOC = (
    '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>T</title>'
    '<style></style></head><body><div class="frame">\n'
    '<div class="page" contenteditable="true"><h1>Speed up widget sync</h1>'
    '<div class="byline"><span class="bk">Author</span>Alex Rivera</div>'
    '<div class="eyebrow">Context</div><p>Context body here.</p>'
    '<div class="eyebrow">Goal</div><div class="goal"><p>sync latency</p></div>'
    '<div class="eyebrow">Risks</div><p>Risks body describing named risks here.</p>'
    '<div class="riskiest"><p>one riskiest assumption</p></div>'
    '<div class="appendix"><div class="label">Appendix</div>'
    '<div class="note">Renders with Part A.</div><h3>User input needed</h3>'
    '<ul class="inputs"><li>First open decision to resolve.</li>'
    '<li>Second open decision to resolve.</li></ul></div></div>\n'
    '</div></body></html>'
)


# A synthetic v4.8-house-output PRD: "User input needed" is its OWN eyebrow
# section (carrying the inputs list), AND there is a separate appendix holding
# `<h3>` sub-sections (Non-goals, Risks, Rollout). This is the OTHER real layout
# (the prd-author examples) and the one that exposes the alias-collision risk —
# "User input needed" must resolve to the eyebrow here, not the appendix.
# Synthetic content only.
V48_DOC = (
    '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>T</title>'
    '<style></style></head><body><div class="frame">\n'
    '<div class="page" contenteditable="true"><h1>Speed up widget sync</h1>'
    '<div class="byline"><span class="bk">Author</span>Alex Rivera</div>'
    '<div class="eyebrow">Context</div><p>Context body here for the doc.</p>'
    '<div class="eyebrow">Goal</div><div class="goal"><p>sync latency target</p></div>'
    '<div class="eyebrow">Requirements</div><table><tbody><tr><td>R1</td></tr></tbody></table>'
    '<div class="eyebrow">User input needed</div>'
    '<ul class="inputs"><li>First open decision to resolve here.</li>'
    '<li>Second open decision to resolve here.</li></ul>'
    '<div class="appendix"><div class="label">Appendix</div>'
    '<div class="note">Renders with Part A.</div>'
    '<h3>Non-goals</h3><p>Out of scope items listed here.</p>'
    '<h3>Risks</h3><p>Named risks paragraph here.</p>'
    '<div class="riskiest"><p>one riskiest assumption</p></div>'
    '<h3>Rollout</h3><p>Phased rollout plan here.</p></div></div>\n'
    '</div></body></html>'
)


def _delim(name, body):
    return f'<div class="eyebrow">{name}</div>{body}'


def _appendix(items):
    return ('<div class="appendix"><div class="label">Appendix</div>'
            '<div class="note">Renders with Part A.</div><h3>User input needed</h3>'
            '<ul class="inputs">' + items + '</ul></div>')


def _llm_result(output):
    return LLMResult(
        output=output, model="claude-sonnet-4-6", prompt_version="v",
        input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.001, latency_ms=5,
        stop_reason="end_turn",
    )


# ── flag ─────────────────────────────────────────────────────────────────────

def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("TARGETED_EDIT_ENABLED", raising=False)
    assert te.enabled() is False


@pytest.mark.parametrize("val,expected", [
    ("1", True), ("true", True), ("YES", True), ("on", True),
    ("0", False), ("off", False), ("", False), ("nope", False),
])
def test_flag_parsing(monkeypatch, val, expected):
    monkeypatch.setenv("TARGETED_EDIT_ENABLED", val)
    assert te.enabled() is expected


# ── tokenizer ────────────────────────────────────────────────────────────────

def test_tokenize_roundtrip_and_spine():
    preamble, sections, suffix = te._tokenize(DOC, M)
    assert [n for n, _ in sections] == ["Context", "Problem", "Goal", "Risks"]
    assert preamble.startswith("<!DOCTYPE")
    assert 'class="eyebrow"' not in preamble  # no delimiter leaked into preamble
    assert suffix.endswith("</body></html>")
    # lossless: preamble + blocks + suffix reconstructs the original doc
    assert preamble + "".join(b for _, b in sections) + suffix == DOC


def test_tokenize_non_house_doc_returns_none():
    assert te._tokenize("<html><body><p>plain</p></body></html>", M) is None


# ── happy-path splices ───────────────────────────────────────────────────────

def test_replace_one_section_keeps_others_byte_identical():
    ops = [{"op": "replace", "section": "Goal",
            "new_html": _delim("Goal", '<div class="goal"><p>p95 &lt; 200ms</p></div>')}]
    out = te.apply_targeted_edit(DOC, ops, M)
    assert "p95 &lt; 200ms" in out
    # every other section survives verbatim
    assert "<p>Context body here.</p>" in out
    assert "<p>Problem body that is long enough here.</p>" in out
    assert "<p>Risks body describing named risks.</p>" in out
    assert out.count('<div class="eyebrow">') == 4
    assert te._is_well_formed(out)


def test_replace_last_section_preserves_wrapper():
    ops = [{"op": "replace", "section": "Risks",
            "new_html": _delim("Risks", "<p>Rewritten risks.</p>"
                               '<div class="riskiest"><p>new</p></div>')}]
    out = te.apply_targeted_edit(DOC, ops, M)
    assert "Rewritten risks." in out
    assert out.endswith("</div>\n</div></body></html>")
    assert out.count("</body>") == 1 and out.count("</html>") == 1
    assert te._is_well_formed(out)


def test_multi_section_replace():
    ops = [
        {"op": "replace", "section": "Context", "new_html": _delim("Context", "<p>New ctx.</p>")},
        {"op": "replace", "section": "Problem", "new_html": _delim("Problem", "<p>New prob.</p>")},
    ]
    out = te.apply_targeted_edit(DOC, ops, M)
    assert "New ctx." in out and "New prob." in out
    assert out.count('<div class="eyebrow">') == 4


def test_insert_after_adds_section_in_place():
    ops = [{"op": "insert_after", "after": "Goal", "section": "Rollout",
            "new_html": _delim("Rollout", "<p>Phased rollout.</p>")}]
    out = te.apply_targeted_edit(DOC, ops, M)
    assert out.count('<div class="eyebrow">') == 5
    assert out.index(">Goal<") < out.index(">Rollout<") < out.index(">Risks<")


def test_delete_section():
    out = te.apply_targeted_edit(DOC, [{"op": "delete", "section": "Problem"}], M)
    assert "Problem body" not in out
    assert out.count('<div class="eyebrow">') == 3
    assert te._is_well_formed(out)


# ── the six gates each force fallback ─────────────────────────────────────────

def _expect_fallback(ops, doc=DOC, match=None):
    with pytest.raises(FallbackNeeded, match=match):
        te.apply_targeted_edit(doc, ops, M)


def test_gate0_non_house_doc():
    _expect_fallback([{"op": "replace", "section": "Goal", "new_html": _delim("Goal", "<p>x</p>")}],
                     doc="<html><body><p>plain</p></body></html>", match="gate0")


def test_gate1_anchor_not_resolved():
    _expect_fallback([{"op": "replace", "section": "Nope", "new_html": _delim("Nope", "<p>x</p>")}],
                     match="gate1")


def test_gate2_payload_wrong_delimiter():
    _expect_fallback([{"op": "replace", "section": "Goal", "new_html": _delim("Problem", "<p>x</p>")}],
                     match="gate2")


def test_gate2_payload_missing_delimiter():
    _expect_fallback([{"op": "replace", "section": "Goal", "new_html": "<p>no delimiter</p>"}],
                     match="gate2")


def test_gate3_truncated_payload_not_well_formed():
    _expect_fallback([{"op": "replace", "section": "Goal",
                       "new_html": _delim("Goal", '<div class="goal"><p>unclosed')}],
                     match="gate3")


def test_gate5_payload_smuggles_wrapper():
    _expect_fallback([{"op": "replace", "section": "Risks",
                       "new_html": _delim("Risks", "<p>x</p>") + "</div></body></html>"}],
                     match="gate5")


def test_gate6_size_collapse():
    big = DOC.replace("<p>Risks body describing named risks.</p>",
                      "<p>" + ("risk " * 3000) + "</p>")
    _expect_fallback([{"op": "replace", "section": "Risks", "new_html": _delim("Risks", "<p>tiny</p>")}],
                     doc=big, match="gate6")


def test_empty_ops_falls_back():
    _expect_fallback([], match="no ops")


# ── section-set invariant (gate 4) ───────────────────────────────────────────

def test_gate4_invariant_accounts_for_replace_then_delete():
    # A replace whose new_html carries a DIFFERENT delimiter name is caught at
    # gate 2 first; gate 4 is the backstop for any splice that changes the spine.
    # Here we prove the invariant correctly nets a replace-then-delete to a
    # delete (Goal removed, nothing added), rather than false-positiving.
    ops = [
        {"op": "replace", "section": "Goal", "new_html": _delim("Goal", "<p>a</p>")},
        {"op": "delete", "section": "Goal"},
    ]
    # second op sees Goal already replaced (still 1:1 by name) then deletes it —
    # net effect is a delete; invariant must hold (Goal removed, nothing extra).
    out = te.apply_targeted_edit(DOC, ops, M)
    assert ">Goal<" not in out
    assert out.count('<div class="eyebrow">') == 3


# ── legacy appendix (v4.7 "User input needed") ───────────────────────────────

def test_appendix_tokenizes_as_its_own_section():
    preamble, sections, suffix = te._tokenize(APPENDIX_DOC, M)
    names = [n for n, _ in sections]
    assert names == ["Context", "Goal", "Risks", "Appendix"]
    # the appendix sits inside Risks physically but is now addressable, and the
    # Risks block no longer contains it
    risks = dict(sections)["Risks"]
    assert 'class="appendix"' not in risks
    assert 'class="riskiest"' in risks
    # lossless
    assert preamble + "".join(b for _, b in sections) + suffix == APPENDIX_DOC


def test_appendix_alias_resolution():
    assert M.resolve("Appendix") == M.resolve("appendix") == "appendix"
    assert M.resolve("User input needed") == "appendix"
    assert M.resolve("Risks") == "risks"


def test_replace_appendix_splices_no_fallback():
    # the exact op that fell back 100% live: replace section:"Appendix"
    new_ap = _appendix("<li>Second open decision to resolve.</li>")  # dropped one
    out = te.apply_targeted_edit(APPENDIX_DOC, [
        {"op": "replace", "section": "Appendix", "new_html": new_ap}], M)
    assert out.count("<li>") == 1
    # every eyebrow section untouched
    assert "<p>Context body here.</p>" in out
    assert "<p>Risks body describing named risks here.</p>" in out
    assert 'class="riskiest"' in out
    assert te._is_well_formed(out)
    assert out.endswith("</div>\n</div></body></html>")


def test_replace_appendix_via_user_input_needed_alias():
    new_ap = _appendix("<li>Only one left.</li>")
    a = te.apply_targeted_edit(APPENDIX_DOC, [
        {"op": "replace", "section": "Appendix", "new_html": new_ap}], M)
    b = te.apply_targeted_edit(APPENDIX_DOC, [
        {"op": "replace", "section": "User input needed", "new_html": new_ap}], M)
    assert a == b  # the alias resolves to the same section


def test_delete_appendix_keeps_risks_and_wrapper():
    out = te.apply_targeted_edit(APPENDIX_DOC, [{"op": "delete", "section": "Appendix"}], M)
    assert 'class="appendix"' not in out
    assert '<div class="eyebrow">Risks</div>' in out
    assert te._is_well_formed(out)
    assert out.endswith("</div>\n</div></body></html>")


def test_appendix_gates_still_intact():
    # gate2: a payload that names a different delimiter than the op still fails
    with pytest.raises(FallbackNeeded, match="gate2"):
        te.apply_targeted_edit(APPENDIX_DOC, [
            {"op": "replace", "section": "Appendix",
             "new_html": _delim("Goal", "<p>wrong</p>")}], M)
    # a genuinely unresolvable label still falls back (not loosened)
    with pytest.raises(FallbackNeeded, match="gate1"):
        te.apply_targeted_edit(APPENDIX_DOC, [
            {"op": "replace", "section": "Glossary",
             "new_html": _delim("Glossary", "<p>x</p>")}], M)


def test_appendix_secondary_absent_is_noop_for_house_v48_doc():
    # DOC has no appendix; the secondary delimiter must not perturb it at all
    assert [n for n, _ in te._tokenize(DOC, M)[1]] == ["Context", "Problem", "Goal", "Risks"]


# ── second real layout: "User input needed" eyebrow + h3-appendix ────────────
# The prd-author example layout. "User input needed" is a real eyebrow here, so
# the alias to the appendix must NOT collide with it (doc-aware resolution).

def test_v48_layout_tokenizes_eyebrow_uin_plus_appendix():
    names = [n for n, _ in te._tokenize(V48_DOC, M)[1]]
    assert names == ["Context", "Goal", "Requirements", "User input needed", "Appendix"]


def test_v48_user_input_eyebrow_and_appendix_both_targetable_no_collision():
    # This is the collision case: both a "User input needed" eyebrow AND an
    # appendix. Doc-aware resolution routes "User input needed" to the eyebrow
    # (literal match) and "Appendix" to the appendix — neither falls back.
    pre, secs, suf = te._tokenize(V48_DOC, M)
    d = dict(secs)
    for target in ("User input needed", "Appendix"):
        out = te.apply_targeted_edit(
            V48_DOC, [{"op": "replace", "section": target, "new_html": d[target]}], M)
        assert out == V48_DOC, f"{target} identical-replace must reproduce the doc"


def test_v48_edit_user_input_eyebrow_splices_not_appendix():
    # Editing the inputs list hits the eyebrow section,
    # leaving the appendix (Non-goals/Risks/Rollout) byte-identical.
    pre, secs, suf = te._tokenize(V48_DOC, M)
    d = dict(secs)
    trimmed = ('<div class="eyebrow">User input needed</div>'
               '<ul class="inputs"><li>Second open decision to resolve here.</li></ul>')
    out = te.apply_targeted_edit(
        V48_DOC, [{"op": "replace", "section": "User input needed", "new_html": trimmed}], M)
    assert out.count("<li>") == 1
    assert d["Appendix"] in out  # appendix untouched, byte-identical
    assert te._is_well_formed(out)


def test_v48_h3_subsection_falls_back_safely():
    # An h3 inside the appendix (e.g. "Rollout") is not an addressable section;
    # naming it must fall back safely, never corrupt.
    with pytest.raises(FallbackNeeded, match="gate1"):
        te.apply_targeted_edit(
            V48_DOC, [{"op": "replace", "section": "Rollout",
                       "new_html": "<h3>Rollout</h3><p>new</p>"}], M)


def test_all_sections_idempotent_across_real_shaped_layouts():
    # Belt-and-braces: replacing every section with its own current block must
    # reproduce the document byte-for-byte (no drift, no dropped whitespace) for
    # both real-shaped layouts.
    for doc in (APPENDIX_DOC, V48_DOC):
        pre, secs, suf = te._tokenize(doc, M)
        for name, block in secs:
            out = te.apply_targeted_edit(
                doc, [{"op": "replace", "section": name, "new_html": block}], M)
            assert out == doc, f"{name} idempotent-replace changed the doc"


# ── whitespace-boundary preservation ─────────────────────────────────────────

def test_replace_preserves_trailing_boundary_whitespace():
    # A re-emit keeps the blank line between sections; a naive splice drops it
    # when the model omits the trailing newline. The splice must restore the
    # original block's trailing whitespace so the boundary is byte-identical.
    doc = (
        '<!DOCTYPE html><html><head><style></style></head><body><div class="frame">\n'
        '<div class="page"><h1>T</h1><div class="byline">A</div>'
        '<div class="eyebrow">Context</div><p>ctx</p>\n\n'  # blank line after Context
        '<div class="eyebrow">Goal</div><div class="goal"><p>g</p></div>\n'
        '<div class="eyebrow">Risks</div><p>r</p></div>\n'
        '</div></body></html>'
    )
    # model returns the Context block WITHOUT its trailing "\n\n"
    payload = '<div class="eyebrow">Context</div><p>ctx EDITED</p>'
    out = te.apply_targeted_edit(doc, [
        {"op": "replace", "section": "Context", "new_html": payload}], M)
    assert "ctx EDITED" in out
    # the "\n\n" boundary before Goal is preserved
    assert '<p>ctx EDITED</p>\n\n<div class="eyebrow">Goal</div>' in out


def test_identical_replace_is_byte_identical_with_boundary_ws():
    doc = (
        '<!DOCTYPE html><html><head><style></style></head><body><div class="frame">\n'
        '<div class="page"><h1>T</h1><div class="byline">A</div>'
        '<div class="eyebrow">Context</div><p>ctx</p>\n\n'
        '<div class="eyebrow">Risks</div><p>r</p></div>\n'
        '</div></body></html>'
    )
    pre, secs, suf = te._tokenize(doc, M)
    ctx = dict(secs)["Context"]
    out = te.apply_targeted_edit(doc, [
        {"op": "replace", "section": "Context", "new_html": ctx.rstrip()}], M)
    assert out == doc  # trailing ws restored -> byte-identical


# ── interpret() ──────────────────────────────────────────────────────────────

def test_interpret_mode_full():
    html, secs = te.interpret(
        {"mode": "full", "full_html": "  <html>full</html>  ", "summary": "s"},
        stored_doc=DOC, model=M, strip_fence=lambda x: x,
    )
    assert html == "<html>full</html>"


def test_interpret_mode_full_populates_sections_changed():
    # mode:full must carry the changed-section list (from the schema field) so
    # the chat's "Updated: X, Y" confirmation is not blanked.
    html, secs = te.interpret(
        {"mode": "full", "full_html": "<html>x</html>",
         "sections_changed": ["Requirements", "Goal", "", 3], "summary": "shorter"},
        stored_doc=DOC, model=M, strip_fence=lambda x: x,
    )
    assert secs == ["Requirements", "Goal"]  # strings only, non-empty


def test_interpret_mode_full_truncated_falls_back():
    # A token-wall-truncated full_html must be caught (strictly safer than today's
    # unvalidated full-emit) and re-run through the proven path.
    with pytest.raises(FallbackNeeded, match="not well-formed"):
        te.interpret({"mode": "full", "full_html": "<div><p>unclosed", "summary": "s"},
                     stored_doc=DOC, model=M, strip_fence=lambda x: x)


def test_interpret_mode_full_wellformed_real_doc_passes():
    html, secs = te.interpret(
        {"mode": "full", "full_html": APPENDIX_DOC, "sections_changed": ["Goal"],
         "summary": "reworded"},
        stored_doc=APPENDIX_DOC, model=M, strip_fence=lambda x: x)
    assert html == APPENDIX_DOC and secs == ["Goal"]


def test_interpret_mode_full_falls_back_to_ops_sections():
    html, secs = te.interpret(
        {"mode": "full", "full_html": "<html>x</html>", "summary": "s",
         "ops": [{"op": "replace", "section": "Goal"}]},
        stored_doc=DOC, model=M, strip_fence=lambda x: x,
    )
    assert secs == ["Goal"]


def test_interpret_mode_full_empty_falls_back():
    with pytest.raises(FallbackNeeded):
        te.interpret({"mode": "full", "full_html": "   ", "summary": ""},
                     stored_doc=DOC, model=M, strip_fence=lambda x: x.strip())


def test_interpret_mode_targeted():
    html, secs = te.interpret(
        {"mode": "targeted", "summary": "s",
         "ops": [{"op": "replace", "section": "Goal", "new_html": _delim("Goal", "<p>tuned</p>")}]},
        stored_doc=DOC, model=M, strip_fence=lambda x: x,
    )
    assert "tuned" in html and secs == ["Goal"]


def test_interpret_unknown_mode_falls_back():
    with pytest.raises(FallbackNeeded):
        te.interpret({"mode": "weird", "summary": ""}, stored_doc=DOC, model=M,
                     strip_fence=lambda x: x)


# ── prompt derivation + normalize ────────────────────────────────────────────

def test_targeted_system_swaps_full_emit_instruction():
    base = ('Edit discipline here.\n\nReturn the FULL updated HTML document in '
            '`html`, the list of human-readable section names you changed in '
            '`sections_changed` (e.g. ["Requirements", "Goal"]), and a one-line '
            '`summary` of the edit.')
    out = te.targeted_system(base, M)
    assert "Return the FULL updated HTML document" not in out
    assert "OUTPUT CONTRACT (targeted edit)" in out
    assert '"targeted"' in out and '"full"' in out
    assert "Edit discipline here." in out  # discipline preserved


def test_normalize_count_heading_only_when_flagged():
    report_model = te.SectionModel(
        name="report", delimiter_re=re.compile(r"<h2>(.*?)</h2>"), count_heading=True)
    assert report_model.normalize("What the evidence says (63)") == \
        report_model.normalize("What the evidence says")
    # PRD model has no count-stripping and is case/space-insensitive
    assert M.normalize("  Goal ") == "goal"
    assert M.normalize("Goal (3)") != M.normalize("Goal")


# ── dispatch inside prd_edit (flag off / on / fallback) ──────────────────

def test_apply_chat_edit_flag_off_is_full_emit(isolated_settings, monkeypatch):
    monkeypatch.delenv("TARGETED_EDIT_ENABLED", raising=False)
    seen = {}

    def _fake(**kw):
        seen.update(kw)
        return _llm_result({"html": "<html><body>FULL EMIT</body></html>",
                            "sections_changed": ["Goal"], "summary": "did it"})

    monkeypatch.setattr(prd_edit, "llm_call", _fake)
    out = prd_edit.apply_chat_edit(DOC, "make the goal tighter", enterprise_id="co")
    assert out == {"html": "<html><body>FULL EMIT</body></html>",
                   "sections_changed": ["Goal"], "summary": "did it"}
    # full-emit path: the current schema + prompt version, NOT the targeted ones
    assert seen["prompt_version"] == prd_edit.CHAT_EDIT_PROMPT_VERSION
    assert seen["json_schema"] is prd_edit._EDIT_SCHEMA
    assert seen["max_tokens"] == 32000
    assert seen["system"] is prd_edit._CHAT_EDIT_SYSTEM  # no targeted rewrite

def test_apply_chat_edit_flag_on_targeted_splices(isolated_settings, monkeypatch):
    monkeypatch.setenv("TARGETED_EDIT_ENABLED", "1")

    def _fake(**kw):
        return _llm_result({
            "mode": "targeted", "summary": "tightened goal",
            "ops": [{"op": "replace", "section": "Goal",
                     "new_html": _delim("Goal", '<div class="goal"><p>p95&lt;200ms</p></div>')}],
        })
