"""Tests for BLOCK-level targeted-edit ops and the no-change / salvage contract.

Two layers, matching `app.targeted_edit`'s own stdlib-only testability rule:

  - The block tokenizer, the block splice engine, and every block-level gate
    analogue, exercised directly on synthetic house-format documents (no
    app/LLM/DB stack). Every new gate is proven to force a `FallbackNeeded`
    with its OWN reason string — matching on the reason is what stops a test
    passing vacuously off the pre-existing "unknown op kind" rejection.
  - The dispatch consequence that motivated the work: a turn that today burns a
    rejected targeted call AND a full re-emit must now cost exactly one call.

Fixture discipline: every block-op fixture names an op verb that does not exist
under the section-only contract, so no test here can pass against the old path.

All names and content in fixtures are synthetic.
"""
from __future__ import annotations

import logging

import pytest

import app.targeted_edit as te
from app.graph.gateway import LLMResult
from app.targeted_edit import PRD_SECTION_MODEL as M, FallbackNeeded

# The PRD scoped editor lives in a differently-named module on different release
# lines, but both expose the SAME dispatch surface this file exercises:
# `llm_call`, and `apply_chat_edit(html, instruction, enterprise_id)`. Bind
# whichever is present so this file is portable rather than pinned to one line.
try:
    from app import prd_questions as prd_dispatch
except ImportError:  # pragma: no cover - depends on the release line
    from app import prd_edit as prd_dispatch


# A synthetic house-format PRD (v4.8 shape) built to exercise BLOCK addressing:
#   Context      — three sibling <p> blocks (a flat run, the common case)
#   Requirements — one <table> block with <thead> + <tbody> (the real PRD shape:
#                  "add a requirement" is one <tr> two levels down)
#   Risks        — a <p> then a <ul> (two-level <li> addressing)
BLOCK_DOC = (
    '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>T</title>'
    '<style></style></head><body><div class="frame">\n'
    '<div class="page" contenteditable="true"><h1>Speed up widget sync</h1>'
    '<div class="byline"><span class="bk">Author</span>Alex Rivera</div>'
    '<div class="eyebrow">Context</div>'
    '<p>First context paragraph here.</p>'
    '<p>Second context paragraph here.</p>'
    '<p>Third context paragraph here.</p>\n'
    '<div class="eyebrow">Requirements</div>'
    '<table><thead><tr><th>#</th><th>Requirement</th></tr></thead><tbody>'
    '<tr><td class="id">R1</td><td class="req">Reminder schedule</td></tr>'
    '<tr><td class="id">R2</td><td class="req">Tone approval once</td></tr>'
    '<tr><td class="id">R3</td><td class="req">Stop on payment</td></tr>'
    '</tbody></table>\n'
    '<div class="eyebrow">Risks</div>'
    '<p>Risks intro paragraph here.</p>'
    '<ul class="risks"><li>First named risk to watch.</li>'
    '<li>Second named risk to watch.</li></ul></div>\n'
    '</div></body></html>'
)

CTX0 = '<p>First context paragraph here.</p>'
CTX1 = '<p>Second context paragraph here.</p>'
CTX2 = '<p>Third context paragraph here.</p>'
A_CTX0 = "First context paragraph here."
A_CTX1 = "Second context paragraph here."
A_REQ_R2 = "R2 Tone approval once"
A_RISK_LI0 = "First named risk to watch."


@pytest.fixture(autouse=True)
def _blocks_on(monkeypatch):
    """Block ops are behind their own sub-flag; on for this module by default."""
    monkeypatch.setenv("TARGETED_EDIT_BLOCKS_ENABLED", "1")


def _llm_result(output):
    return LLMResult(
        output=output, model="claude-sonnet-4-6", prompt_version="v",
        input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.001, latency_ms=5,
        stop_reason="end_turn",
    )


def _section_of(doc, name):
    _, secs, _ = te._tokenize(doc, M)
    return dict(secs)[name]


# ── flag ─────────────────────────────────────────────────────────────────────

def test_blocks_flag_default_off(monkeypatch):
    monkeypatch.delenv("TARGETED_EDIT_BLOCKS_ENABLED", raising=False)
    assert te.blocks_enabled() is False


def test_blocks_flag_off_rejects_block_ops_as_today(monkeypatch):
    """Kill switch: flag OFF must behave byte-for-byte like today, i.e. a block
    verb is simply an unknown op kind and the turn falls back."""
    monkeypatch.setenv("TARGETED_EDIT_BLOCKS_ENABLED", "0")
    with pytest.raises(FallbackNeeded, match="unknown op kind"):
        te.apply_targeted_edit(BLOCK_DOC, [
            {"op": "replace_blocks", "section": "Context", "from": 0, "to": 0,
             "anchor_text": A_CTX0, "new_html": "<p>Rewritten.</p>"}], M)


def test_blocks_flag_off_omits_block_clause_from_prompt(monkeypatch):
    monkeypatch.setenv("TARGETED_EDIT_BLOCKS_ENABLED", "0")
    base = "Return the FULL updated HTML document in `html`, and a one-line `summary` of it."
    assert "replace_blocks" not in te.targeted_system(base, M)


def test_blocks_flag_on_adds_block_clause_to_prompt():
    base = "Return the FULL updated HTML document in `html`, and a one-line `summary` of it."
    out = te.targeted_system(base, M)
    for tok in ("replace_blocks", "insert_blocks_after", "delete_blocks", "anchor_text"):
        assert tok in out


# ── 1-4. block tokenizer ─────────────────────────────────────────────────────

def test_block_tokenizer_roundtrip_is_byte_identical():
    body = _section_of(BLOCK_DOC, "Context")
    head, items = te._decompose_section(body.rstrip(), M)
    assert [t for t, _ in items] == ["p", "p", "p"]
    assert head + "".join(txt for _, txt in items) == body.rstrip()


def test_block_tokenizer_refuses_stray_text_between_blocks():
    doc = BLOCK_DOC.replace(CTX1, CTX1 + "loose prose here")
    with pytest.raises(FallbackNeeded, match="gate0b"):
        te.apply_targeted_edit(doc, [
            {"op": "replace_blocks", "section": "Context", "from": 0, "to": 0,
             "anchor_text": A_CTX0, "new_html": "<p>Rewritten.</p>"}], M)


def test_two_level_addresses_table_rows_and_list_items():
    req = _section_of(BLOCK_DOC, "Requirements").rstrip()
    _, items = te._decompose_section(req, M)
    kids = te._child_items(items[0][1], "table")
    assert kids is not None
    assert [t for t, _ in kids[1]] == ["tr", "tr", "tr"]

    risks = _section_of(BLOCK_DOC, "Risks").rstrip()
    _, ritems = te._decompose_section(risks, M)
    rkids = te._child_items(ritems[1][1], "ul")
    assert rkids is not None
    assert [t for t, _ in rkids[1]] == ["li", "li"]


def test_depth_cap_refuses_a_three_level_address():
    with pytest.raises(FallbackNeeded, match="gate1b: .*depth"):
        te.apply_targeted_edit(BLOCK_DOC, [
            {"op": "replace_blocks", "section": "Requirements",
             "from": "0.0.0", "to": "0.0.0", "anchor_text": "R1",
             "new_html": "<td>x</td>"}], M)


# ── 5-10. happy paths (byte-identity is the assertion) ───────────────────────

def test_insert_blocks_after_last_appends_and_leaves_everything_else_identical():
    out = te.apply_targeted_edit(BLOCK_DOC, [
        {"op": "insert_blocks_after", "section": "Context", "after": 2,
         "anchor_text": "Third context paragraph here.",
         "new_html": "<p>Fourth context paragraph added.</p>"}], M)
    assert "<p>Fourth context paragraph added.</p>" in out
    # every other block of the touched section survives byte-for-byte
    for blk in (CTX0, CTX1, CTX2):
        assert blk in out
    # every untouched section is byte-identical
    for name in ("Requirements", "Risks"):
        assert _section_of(out, name) == _section_of(BLOCK_DOC, name)
    # exactly one paragraph was added; nothing was duplicated
    assert out.count("<p>") == BLOCK_DOC.count("<p>") + 1


def test_replace_blocks_over_a_range_keeps_untouched_blocks_identical():
    out = te.apply_targeted_edit(BLOCK_DOC, [
        {"op": "replace_blocks", "section": "Context", "from": 0, "to": 1,
         "anchor_text": A_CTX0,
         "new_html": "<p>Merged opening paragraph.</p>"}], M)
    assert "<p>Merged opening paragraph.</p>" in out
    assert CTX0 not in out and CTX1 not in out
    assert CTX2 in out                       # untouched block byte-identical
    assert _section_of(out, "Risks") == _section_of(BLOCK_DOC, "Risks")


def test_delete_blocks_leaves_the_section_set_unchanged():
    out = te.apply_targeted_edit(BLOCK_DOC, [
        {"op": "delete_blocks", "section": "Context", "from": 1, "to": 1,
         "anchor_text": A_CTX1}], M)
    assert CTX1 not in out
    assert CTX0 in out and CTX2 in out
    _, secs, _ = te._tokenize(out, M)
    assert [n for n, _ in secs] == ["Context", "Requirements", "Risks"]


def test_insert_blocks_after_minus_one_prepends():
    out = te.apply_targeted_edit(BLOCK_DOC, [
        {"op": "insert_blocks_after", "section": "Context", "after": -1,
         "new_html": "<p>Zeroth context paragraph.</p>"}], M)
    assert '<div class="eyebrow">Context</div><p>Zeroth context paragraph.</p>' in out
    assert CTX0 in out


def test_identical_block_replace_is_byte_identical():
    """Boundary whitespace must survive a block splice the same way it survives
    a section splice — otherwise the byte-identity safety story degrades."""
    out = te.apply_targeted_edit(BLOCK_DOC, [
        {"op": "replace_blocks", "section": "Context", "from": 2, "to": 2,
         "anchor_text": "Third context paragraph here.", "new_html": CTX2}], M)
    assert out == BLOCK_DOC


def test_two_level_insert_adds_one_table_row_only():
    """The motivating case: 'add a requirement' costs one <tr>, not the table."""
    out = te.apply_targeted_edit(BLOCK_DOC, [
        {"op": "insert_blocks_after", "section": "Requirements", "after": "0.2",
         "anchor_text": "R3 Stop on payment",
         "new_html": '<tr><td class="id">R4</td><td class="req">Digest email</td></tr>'}], M)
    assert '<td class="req">Digest email</td>' in out
    assert out.count("<tr>") == BLOCK_DOC.count("<tr>") + 1
    # the header row and every existing body row are byte-identical
    assert '<thead><tr><th>#</th><th>Requirement</th></tr></thead>' in out
    for rid in ("R1", "R2", "R3"):
        assert f'<td class="id">{rid}</td>' in out
    assert _section_of(out, "Context") == _section_of(BLOCK_DOC, "Context")


def test_two_level_replace_of_one_list_item():
    out = te.apply_targeted_edit(BLOCK_DOC, [
        {"op": "replace_blocks", "section": "Risks", "from": "1.0", "to": "1.0",
         "anchor_text": A_RISK_LI0,
         "new_html": "<li>First named risk, now reworded.</li>"}], M)
    assert "<li>First named risk, now reworded.</li>" in out
    assert "<li>Second named risk to watch.</li>" in out
    assert "<li>First named risk to watch.</li>" not in out
    assert "<p>Risks intro paragraph here.</p>" in out


def test_block_op_and_section_op_mix_in_one_response():
    out = te.apply_targeted_edit(BLOCK_DOC, [
        {"op": "replace_blocks", "section": "Context", "from": 0, "to": 0,
         "anchor_text": A_CTX0, "new_html": "<p>Opening rewritten.</p>"},
        {"op": "replace", "section": "Risks",
         "new_html": '<div class="eyebrow">Risks</div><p>Wholly new risks body here.</p>'},
    ], M)
    assert "<p>Opening rewritten.</p>" in out
    assert "<p>Wholly new risks body here.</p>" in out
    assert CTX1 in out and CTX2 in out
    assert _section_of(out, "Requirements") == _section_of(BLOCK_DOC, "Requirements")


# ── 11-19. every new gate, forced red individually ───────────────────────────

def test_gate0b_unparseable_section_body():
    doc = BLOCK_DOC.replace(CTX0, "leading stray text" + CTX0)
    with pytest.raises(FallbackNeeded, match="gate0b"):
        te.apply_targeted_edit(doc, [
            {"op": "delete_blocks", "section": "Context", "from": 1, "to": 1,
             "anchor_text": A_CTX1}], M)


def test_gate1b_ordinal_out_of_range():
    with pytest.raises(FallbackNeeded, match="gate1b: .*out of range"):
        te.apply_targeted_edit(BLOCK_DOC, [
            {"op": "replace_blocks", "section": "Context", "from": 0, "to": 9,
             "anchor_text": A_CTX0, "new_html": "<p>x y z.</p>"}], M)


def test_gate1b_from_greater_than_to():
    with pytest.raises(FallbackNeeded, match="gate1b: .*out of range"):
        te.apply_targeted_edit(BLOCK_DOC, [
            {"op": "replace_blocks", "section": "Context", "from": 2, "to": 0,
             "anchor_text": "Third context paragraph here.", "new_html": "<p>x y z.</p>"}], M)


def test_gate1b_overlapping_ranges():
    with pytest.raises(FallbackNeeded, match="gate1b: overlapping"):
        te.apply_targeted_edit(BLOCK_DOC, [
            {"op": "replace_blocks", "section": "Context", "from": 0, "to": 1,
             "anchor_text": A_CTX0, "new_html": "<p>First rewrite here.</p>"},
            {"op": "replace_blocks", "section": "Context", "from": 1, "to": 2,
             "anchor_text": A_CTX1, "new_html": "<p>Second rewrite here.</p>"},
        ], M)


def test_gate1b_block_op_and_section_op_on_the_same_section():
    with pytest.raises(FallbackNeeded, match="gate1b: .*both a section-level"):
        te.apply_targeted_edit(BLOCK_DOC, [
            {"op": "replace_blocks", "section": "Context", "from": 0, "to": 0,
             "anchor_text": A_CTX0, "new_html": "<p>Opening rewritten.</p>"},
            {"op": "replace", "section": "Context",
             "new_html": '<div class="eyebrow">Context</div><p>Whole new context.</p>'},
        ], M)


def test_gate2b_i_payload_is_a_partial_element():
    with pytest.raises(FallbackNeeded, match="gate2b: .*complete"):
        te.apply_targeted_edit(BLOCK_DOC, [
            {"op": "replace_blocks", "section": "Context", "from": 0, "to": 0,
             "anchor_text": A_CTX0, "new_html": "<p>Truncated at the token wall"}], M)


def test_gate2b_i_payload_has_trailing_stray_text():
    with pytest.raises(FallbackNeeded, match="gate2b: .*complete"):
        te.apply_targeted_edit(BLOCK_DOC, [
            {"op": "replace_blocks", "section": "Context", "from": 0, "to": 0,
             "anchor_text": A_CTX0, "new_html": "<p>Fine.</p> and then loose prose"}], M)


def test_gate2b_ii_payload_tag_differs_from_target_tag():
    with pytest.raises(FallbackNeeded, match="gate2b: .*tag"):
        te.apply_targeted_edit(BLOCK_DOC, [
            {"op": "replace_blocks", "section": "Risks", "from": 1, "to": 1,
             "anchor_text": A_RISK_LI0,
             "new_html": "<p>The risks, now as a paragraph.</p>"}], M)


def test_gate2b_iii_anchor_mismatch_blocks_a_silent_off_by_one():
    """THE load-bearing test. An ordinal is unique but UNVERIFIED: without the
    echoed anchor this op would splice happily onto the wrong block, which is a
    corruption class the section-only design does not have. The anchor names
    block 0 while the ordinal names block 1 -> must refuse, not splice."""
    with pytest.raises(FallbackNeeded, match="gate2b: anchor"):
        te.apply_targeted_edit(BLOCK_DOC, [
            {"op": "replace_blocks", "section": "Context", "from": 1, "to": 1,
             "anchor_text": A_CTX0, "new_html": "<p>Rewritten paragraph.</p>"}], M)


def test_gate2b_iii_anchor_missing_is_refused():
    with pytest.raises(FallbackNeeded, match="gate2b: anchor"):
        te.apply_targeted_edit(BLOCK_DOC, [
            {"op": "replace_blocks", "section": "Context", "from": 1, "to": 1,
             "new_html": "<p>Rewritten paragraph.</p>"}], M)


def test_gate2b_iii_anchor_too_short_is_refused():
    """A one-character echo would make the gate decorative; require the model to
    echo the whole 24-char prefix (or the whole visible text, if shorter)."""
    with pytest.raises(FallbackNeeded, match="gate2b: anchor"):
        te.apply_targeted_edit(BLOCK_DOC, [
            {"op": "replace_blocks", "section": "Context", "from": 1, "to": 1,
             "anchor_text": "S", "new_html": "<p>Rewritten paragraph.</p>"}], M)


def test_gate2b_iii_anchor_is_whitespace_and_case_insensitive():
    out = te.apply_targeted_edit(BLOCK_DOC, [
        {"op": "replace_blocks", "section": "Context", "from": 1, "to": 1,
         "anchor_text": "  second   CONTEXT paragraph here.  ",
         "new_html": "<p>Rewritten paragraph.</p>"}], M)
    assert "<p>Rewritten paragraph.</p>" in out


def test_gate4b_untouched_block_not_byte_identical_is_caught():
    """Defence-in-depth: gates 2b(i)/5b make this unreachable through the op
    path, so the reconciliation itself is unit-tested directly. A splice whose
    reassembly disagrees with re-tokenization must never be written."""
    head, items = te._decompose_section(_section_of(BLOCK_DOC, "Context").rstrip(), M)
    corrupt = head + CTX0 + "<p>Silently altered.</p>" + CTX2
    with pytest.raises(FallbackNeeded, match="gate4b"):
        te._verify_block_splice(corrupt, head, items, [(CTX0, 0), (CTX1, 1), (CTX2, 2)], M)


def test_gate4b_block_count_reconciles():
    head, items = te._decompose_section(_section_of(BLOCK_DOC, "Context").rstrip(), M)
    corrupt = head + CTX0 + CTX1          # a block vanished
    with pytest.raises(FallbackNeeded, match="gate4b"):
        te._verify_block_splice(corrupt, head, items, [(CTX0, 0), (CTX1, 1), (CTX2, 2)], M)


def test_gate5b_block_payload_containing_a_section_delimiter():
    with pytest.raises(FallbackNeeded, match="gate5b"):
        te.apply_targeted_edit(BLOCK_DOC, [
            {"op": "replace_blocks", "section": "Context", "from": 0, "to": 0,
             "anchor_text": A_CTX0,
             "new_html": '<div><div class="eyebrow">Smuggled</div><p>body</p></div>'}], M)


def test_gate6b_section_shrinks_below_the_per_section_band():
    with pytest.raises(FallbackNeeded, match="gate6b"):
        te.apply_targeted_edit(BLOCK_DOC, [
            {"op": "replace_blocks", "section": "Context", "from": 0, "to": 2,
             "anchor_text": A_CTX0, "new_html": "<p>Tiny.</p>"}], M)


def test_gate3_still_catches_an_unbalanced_result():
    """Gate 3 is unchanged and becomes more load-bearing under finer ops."""
    with pytest.raises(FallbackNeeded, match="gate2b|gate3"):
        te.apply_targeted_edit(BLOCK_DOC, [
            {"op": "replace_blocks", "section": "Context", "from": 0, "to": 0,
             "anchor_text": A_CTX0, "new_html": "<p><span>unclosed</p>"}], M)


# ── 20-23. Change A: mode:"none", salvage, kept rejection, shape log ─────────

def test_schema_carries_none_mode_and_block_ops():
    props = te.TARGETED_EDIT_SCHEMA["properties"]
    assert set(props["mode"]["enum"]) == {"targeted", "full", "none"}
    op_enum = set(props["ops"]["items"]["properties"]["op"]["enum"])
    assert {"replace", "delete", "insert_after"} <= op_enum
    assert {"replace_blocks", "insert_blocks_after", "delete_blocks"} <= op_enum
    assert "anchor_text" in props["ops"]["items"]["properties"]


def test_interpret_mode_none_returns_stored_doc_untouched():
    html, secs = te.interpret(
        {"mode": "none", "summary": "That is a question, not an edit."},
        stored_doc=BLOCK_DOC, model=M, strip_fence=lambda x: x)
    assert html == BLOCK_DOC and secs == []


def test_interpret_targeted_no_ops_with_full_html_is_salvaged():
    html, secs = te.interpret(
        {"mode": "targeted", "ops": [], "full_html": BLOCK_DOC,
         "sections_changed": ["Context"], "summary": "s"},
        stored_doc=BLOCK_DOC, model=M, strip_fence=lambda x: x)
    assert html == BLOCK_DOC and secs == ["Context"]


def test_interpret_targeted_no_ops_salvage_still_runs_wellformedness():
    with pytest.raises(FallbackNeeded, match="not well-formed"):
        te.interpret({"mode": "targeted", "ops": [], "full_html": "<div><p>cut off",
                      "summary": "s"},
                     stored_doc=BLOCK_DOC, model=M, strip_fence=lambda x: x)


def test_interpret_targeted_no_ops_and_no_full_html_still_falls_back():
    """A3: the empty-ops guard is KEPT. mode:"none" is now the correct way to say
    'no change', so a bare empty ops array remains a genuine malformation."""
    with pytest.raises(FallbackNeeded, match="no ops in targeted response"):
        te.interpret({"mode": "targeted", "ops": [], "summary": "s"},
                     stored_doc=BLOCK_DOC, model=M, strip_fence=lambda x: x)


def test_rejection_shape_log_carries_shape_only_and_no_document_content(caplog):
    marker = "ZEBRAFISHMARKERSTRING"
    with caplog.at_level(logging.WARNING, logger="app.targeted_edit"):
        with pytest.raises(FallbackNeeded):
            te.interpret({"mode": "targeted", "ops": [],
                          "summary": f"tried to edit {marker} section",
                          "html": f"<p>{marker}</p>"},
                         stored_doc=BLOCK_DOC, model=M, strip_fence=lambda x: x)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "shape:" in text
    assert "mode=targeted" in text and "ops=0" in text and "full_html=0" in text
    assert "unknown_keys=" in text and "html" in text
    assert marker not in text                     # no document content, no PII
    assert "First context paragraph" not in text


# ── the double-payment case, at the dispatch layer ───────────────────────────

def _dispatch(monkeypatch, outputs):
    """Run apply_chat_edit with the targeted flag ON, returning (result, calls)."""
    monkeypatch.setenv("TARGETED_EDIT_ENABLED", "1")
    calls = []

    def _fake(**kw):
        calls.append(kw)
        return _llm_result(outputs[len(calls) - 1])

    monkeypatch.setattr(prd_dispatch, "llm_call", _fake)
    res = prd_dispatch.apply_chat_edit(BLOCK_DOC, "what does R2 mean?", "ent-1")
    return res, calls


def test_no_ops_with_full_html_costs_one_call_not_two(monkeypatch):
    """The measured worst case was a DOUBLE PAYMENT: a rejected targeted call
    followed by a full re-emit. Salvaging a mis-moded response makes it one."""
    res, calls = _dispatch(monkeypatch, [
        {"mode": "targeted", "ops": [], "full_html": BLOCK_DOC,
         "sections_changed": ["Context"], "summary": "s"},
        {"html": "<html>SECOND CALL SHOULD NOT HAPPEN</html>"},
    ])
    assert len(calls) == 1
    assert res["html"] == BLOCK_DOC
    assert "SECOND CALL" not in res["html"]


def test_mode_none_costs_one_call_and_leaves_the_document_untouched(monkeypatch):
    """A question typed into PRD chat must no longer trigger a full rewrite."""
    res, calls = _dispatch(monkeypatch, [
        {"mode": "none", "summary": "That is a question about R2, not an edit."},
        {"html": "<html>SECOND CALL SHOULD NOT HAPPEN</html>"},
    ])
    assert len(calls) == 1
    assert res["html"] == BLOCK_DOC
    assert res["sections_changed"] == []


def test_genuinely_malformed_response_still_pays_the_fallback(monkeypatch):
    """Fail-to-slow is preserved: no ops AND no full_html is still two calls."""
    res, calls = _dispatch(monkeypatch, [
        {"mode": "targeted", "ops": [], "summary": "s"},
        {"html": "<html>full re-emit</html>", "sections_changed": ["Context"]},
    ])
    assert len(calls) == 2
    assert res["html"] == "<html>full re-emit</html>"


def test_block_op_dispatch_splices_through_the_real_entry_point(monkeypatch):
    res, calls = _dispatch(monkeypatch, [
        {"mode": "targeted", "summary": "added a requirement",
         "ops": [{"op": "insert_blocks_after", "section": "Requirements",
                  "after": "0.2", "anchor_text": "R3 Stop on payment",
                  "new_html": '<tr><td class="id">R4</td>'
                              '<td class="req">Digest email</td></tr>'}]},
        {"html": "<html>SECOND CALL SHOULD NOT HAPPEN</html>"},
    ])
    assert len(calls) == 1
    assert '<td class="req">Digest email</td>' in res["html"]
    assert res["sections_changed"] == ["Requirements"]


# ── the shared goal-report path gets block ops for free (no goal-report code) ─

GR_DOC = (
    "<h1>Cut onboarding drop-off</h1>"
    "<h2>What the evidence says (3)</h2>"
    "<h3>Finding one</h3><p>Body of finding one here.</p>"
    "<h3>Finding two</h3><p>Body of finding two here.</p>"
    "<h2>Considered and ruled out (1)</h2>"
    "<p>Ruled-out option paragraph here.</p>"
)


def test_goalreport_block_replace_of_one_finding_body():
    """A per-finding trim is the case the probe measured falling back entirely.
    The <h3> is not a delimiter, so it is a BLOCK — addressable now."""
    from app.targeted_edit import GOALREPORT_SECTION_MODEL as G
    out = te.apply_targeted_edit(GR_DOC, [
        {"op": "replace_blocks", "section": "What the evidence says",
         "from": 3, "to": 3, "anchor_text": "Body of finding two here.",
         "new_html": "<p>Finding two, trimmed.</p>"}], G)
    assert "<p>Finding two, trimmed.</p>" in out
    assert "<h3>Finding one</h3><p>Body of finding one here.</p>" in out
    assert "<h2>Considered and ruled out (1)</h2>" in out
    assert "<p>Body of finding two here.</p>" not in out


def test_goalreport_gate5b_refuses_an_h2_inside_a_block_payload():
    from app.targeted_edit import GOALREPORT_SECTION_MODEL as G
    with pytest.raises(FallbackNeeded, match="gate5b"):
        te.apply_targeted_edit(GR_DOC, [
            {"op": "replace_blocks", "section": "What the evidence says",
             "from": 0, "to": 0, "anchor_text": "Finding one",
             "new_html": "<div><h2>Smuggled</h2></div>"}], G)


# ── deliberate refusals: safe fallbacks, not silent best-effort splices ──────

def test_table_without_a_single_tbody_is_refused():
    """Conservative by design: anything but exactly one <tbody> degrades to a
    coarser op rather than guessing which rows are the body."""
    doc = BLOCK_DOC.replace("<tbody>", "").replace("</tbody>", "")
    with pytest.raises(FallbackNeeded, match="gate0b"):
        te.apply_targeted_edit(doc, [
            {"op": "insert_blocks_after", "section": "Requirements", "after": "0.2",
             "anchor_text": "R3 Stop on payment",
             "new_html": '<tr><td class="id">R4</td><td class="req">Digest</td></tr>'}], M)


def test_thead_rows_are_not_addressable():
    """Two-level addressing reaches the BODY rows only; ordinal 0 is R1, not the
    header row. Proven by what lands, not by what the tokenizer reports."""
    out = te.apply_targeted_edit(BLOCK_DOC, [
        {"op": "replace_blocks", "section": "Requirements", "from": "0.0", "to": "0.0",
         "anchor_text": "R1 Reminder schedule",
         "new_html": '<tr><td class="id">R1</td><td class="req">Renamed</td></tr>'}], M)
    assert '<thead><tr><th>#</th><th>Requirement</th></tr></thead>' in out
    assert '<td class="req">Renamed</td>' in out
    assert '<td class="req">Reminder schedule</td>' not in out


def test_legacy_appendix_section_refuses_block_ops():
    """The v4.7 `<div class="appendix">` delimiter is an OPENING tag, so its
    section body carries an unbalanced trailing `</div>` and cannot be
    decomposed losslessly. Refusing is correct: it degrades to the section-level
    appendix splice that is already live-verified."""
    doc = (
        '<!DOCTYPE html><html><head><style></style></head><body><div class="frame">\n'
        '<div class="page"><h1>T</h1><div class="byline">A</div>'
        '<div class="eyebrow">Context</div><p>Context body here.</p>'
        '<div class="appendix"><div class="label">Appendix</div>'
        '<h3>User input needed</h3><ul class="inputs">'
        '<li>First open decision to resolve.</li></ul></div></div>\n'
        '</div></body></html>'
    )
    with pytest.raises(FallbackNeeded, match="gate0b"):
        te.apply_targeted_edit(doc, [
            {"op": "replace_blocks", "section": "Appendix", "from": 0, "to": 0,
             "anchor_text": "Appendix", "new_html": '<div class="label">Notes</div>'}], M)
    # ...and the SECTION-level op on the same section still works, unchanged.
    out = te.apply_targeted_edit(doc, [
        {"op": "delete", "section": "Appendix"}], M)
    assert "User input needed" not in out and "Context body here." in out


def test_float_ordinal_is_refused_rather_than_guessed():
    """A bare JSON 2.1 and 2.10 are the same float; guessing one would be a
    silent off-by-nine. Refusing costs a fallback."""
    with pytest.raises(FallbackNeeded, match="gate1b: .*not an integer"):
        te.apply_targeted_edit(BLOCK_DOC, [
            {"op": "replace_blocks", "section": "Requirements", "from": 0.2, "to": 0.2,
             "anchor_text": "R1 Reminder schedule", "new_html": "<tr><td>x</td></tr>"}], M)


def test_two_block_ops_on_one_section_both_apply():
    out = te.apply_targeted_edit(BLOCK_DOC, [
        {"op": "replace_blocks", "section": "Context", "from": 0, "to": 0,
         "anchor_text": A_CTX0, "new_html": "<p>Opening rewritten here.</p>"},
        {"op": "insert_blocks_after", "section": "Context", "after": 2,
         "anchor_text": "Third context paragraph here.",
         "new_html": "<p>Closing paragraph added.</p>"},
    ], M)
    assert "<p>Opening rewritten here.</p>" in out
    assert "<p>Closing paragraph added.</p>" in out
    assert CTX1 in out and CTX2 in out
    assert CTX0 not in out


def test_ordinals_resolve_against_the_pre_edit_list():
    """Two ops in one response must not shift each other's addresses."""
    out = te.apply_targeted_edit(BLOCK_DOC, [
        {"op": "insert_blocks_after", "section": "Context", "after": 0,
         "anchor_text": A_CTX0, "new_html": "<p>Inserted after the first.</p>"},
        {"op": "replace_blocks", "section": "Context", "from": 2, "to": 2,
         "anchor_text": "Third context paragraph here.",
         "new_html": "<p>Third rewritten.</p>"},
    ], M)
    assert '<p>First context paragraph here.</p><p>Inserted after the first.</p>' in out
    assert "<p>Third rewritten.</p>" in out
    assert CTX1 in out


# ── the anchor length floor, pinned at both edges ────────────────────────────

def test_anchor_at_the_length_floor_is_accepted():
    """A model copying "the first 24 characters" can cut inside a whitespace run
    and deliver 23 after normalization. Rejecting a CORRECT anchor costs two
    full calls, which is the one way this work can regress the product."""
    out = te.apply_targeted_edit(BLOCK_DOC, [
        {"op": "replace_blocks", "section": "Context", "from": 1, "to": 1,
         "anchor_text": A_CTX1[:16], "new_html": "<p>Rewritten paragraph.</p>"}], M)
    assert "<p>Rewritten paragraph.</p>" in out


def test_anchor_below_the_length_floor_is_refused():
    with pytest.raises(FallbackNeeded, match="gate2b: anchor_text .*too short"):
        te.apply_targeted_edit(BLOCK_DOC, [
            {"op": "replace_blocks", "section": "Context", "from": 1, "to": 1,
             "anchor_text": A_CTX1[:15], "new_html": "<p>Rewritten paragraph.</p>"}], M)


def test_a_wrong_anchor_of_legal_length_is_still_refused():
    """The floor is a length floor, NOT a strictness relaxation: a wrong anchor
    fails `startswith` no matter how long it is."""
    with pytest.raises(FallbackNeeded, match="gate2b: anchor_text .*does not match"):
        te.apply_targeted_edit(BLOCK_DOC, [
            {"op": "replace_blocks", "section": "Context", "from": 1, "to": 1,
             "anchor_text": A_CTX0[:16], "new_html": "<p>Rewritten paragraph.</p>"}], M)


def test_real_prd_example_add_a_requirement_costs_one_row():
    """End-to-end on the shipped house-format example, not a synthetic fixture:
    the whole document grows by exactly the new row's bytes."""
    import pathlib
    doc = pathlib.Path("skills/prd-author/examples/01-perch.html").read_text()
    _, items = te._decompose_section(dict(te._tokenize(doc, M)[1])["Requirements"].rstrip(), M)
    _, rows, _ = te._child_items(items[0][1], "table")
    last = len(rows) - 1
    row = ('<tr><td class="id">R6</td><td class="req">Weekly digest</td>'
           '<td>Owner gets a Monday summary of outstanding invoices.</td>'
           '<td><span class="pill h">Happy path</span></td></tr>')
    out = te.apply_targeted_edit(doc, [
        {"op": "insert_blocks_after", "section": "Requirements", "after": f"0.{last}",
         "anchor_text": te._visible_text(rows[last][1])[:24],
         "new_html": row}], M)
    assert len(out) == len(doc) + len(row)
    assert out == doc[:doc.rindex("</tbody>")] + row + doc[doc.rindex("</tbody>"):]


# ── the wire format the schema actually asks for: quoted string ordinals ─────

def test_schema_declares_ordinal_fields_exactly_as_today():
    """This dict is handed to the API verbatim as a tool `input_schema`. Keeping
    `after` at the type it already has means the proven SECTION path carries no
    new schema risk from this change."""
    props = te.TARGETED_EDIT_SCHEMA["properties"]["ops"]["items"]["properties"]
    assert props["after"] == {"type": "string"}
    assert props["from"] == {"type": "string"}
    assert props["to"] == {"type": "string"}


def test_string_ordinals_are_the_asked_for_wire_format():
    out = te.apply_targeted_edit(BLOCK_DOC, [
        {"op": "replace_blocks", "section": "Context", "from": "1", "to": "1",
         "anchor_text": A_CTX1, "new_html": "<p>Rewritten paragraph.</p>"}], M)
    assert "<p>Rewritten paragraph.</p>" in out and CTX0 in out and CTX2 in out


def test_string_minus_one_prepends():
    out = te.apply_targeted_edit(BLOCK_DOC, [
        {"op": "insert_blocks_after", "section": "Context", "after": "-1",
         "new_html": "<p>Zeroth context paragraph.</p>"}], M)
    assert '<div class="eyebrow">Context</div><p>Zeroth context paragraph.</p>' in out


def test_integer_ordinals_are_still_tolerated():
    """Defensive: a model that emits `1` instead of "1" must not cost a fallback."""
    out = te.apply_targeted_edit(BLOCK_DOC, [
        {"op": "delete_blocks", "section": "Context", "from": 1, "to": 1,
         "anchor_text": A_CTX1}], M)
    assert CTX1 not in out and CTX0 in out


def test_prompt_asks_for_quoted_ordinals():
    base = "Return the FULL updated HTML document in `html`, and a one-line `summary` of it."
    assert "Ordinals are always QUOTED strings" in te.targeted_system(base, M)
