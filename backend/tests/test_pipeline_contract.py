"""CROSS-STACK PIPELINE CONTRACT (backend side).

PRD/evidence generation is a two-runtime pipeline: a backend template tells the
LLM which `:::name` semantic blocks to emit, the model produces markdown, and
the frontend adapters (`web/app/lib/{evidence,prd}-adapter.ts`) parse those
blocks into typed sections the UI renders. When the two sides drift — a block
renamed, a JSON body broken, a required block dropped — nothing throws: the
adapter silently degrades the block to a plain paragraph and the rich UI
quietly disappears. That class of "a pipeline change broke the UI" regression
is what these tests exist to catch EARLY, in the backend CI lane.

This file guards the backend half of the contract:
  - The template/sample files that ship in the repo are STRUCTURALLY VALID:
    every JSON-bodied `:::block` is valid JSON, and every required block is
    present. A broken edit fails here before it ever reaches the model.
  - The rich block VOCABULARY is locked on the SAMPLE (kept so PRDs generated
    before the lean-markdown switch keep rendering), so renaming or adding a
    block forces an update here — the cue to also update the frontend adapter
    case (guarded on the web side by `pipeline-contract.test.ts`).
  - The LIVE template (`sprntly_prd_template.md`) and the `prd-author` skill
    template are lean markdown — NO `:::` blocks, no Part B — matching the
    simplified human PRD the runner now emits.

NOTE: the EVIDENCE artifact has moved OFF this `:::block` contract — it is now
the `evidence-brief` skill's self-contained HTML visual brief (rendered in a
sandboxed iframe; see app.evidence_kg + test_evidence_kg). The evidence template
files are retained as legacy assets (v1/v2 rows), so the asset-integrity +
vocabulary checks below still guard them, but the evidence runner no longer
emits `:::block` markdown. The PRD half of the contract is unchanged.

The matching frontend guard reads the PRD files through the real adapter;
`test-web.yml` triggers on changes to these template paths so a backend-only
edit still runs the adapter side.
"""
from __future__ import annotations

import json
import re

import pytest

# ── canonical block vocabulary (mirrors the adapter switch cases) ─────────────
# Blocks whose body is JSON (validated with json.loads). context-chip (plain
# text) and callout (bold `**Supports:**`/`**Rules out:**` lines) are NOT JSON.
EVIDENCE_JSON_BLOCKS = {"hero", "cuts-index", "source", "quote"}
PRD_JSON_BLOCKS = {
    "tldr", "problem", "hypothesis", "requirements", "acceptance-criteria",
    "metrics", "risks", "milestones", "dod",
}
# The complete set of `:::block` openers each family of files is expected to
# emit. Locking these means a backend rename/add trips the assertion (and must
# be mirrored into the frontend adapter — see web pipeline-contract.test.ts).
EVIDENCE_FILE_BLOCKS = {"context-chip", "hero", "callout", "cuts-index", "source", "quote"}
PRD_FILE_BLOCKS = {
    "context-chip", "tldr", "problem", "hypothesis", "requirements",
    "acceptance-criteria", "metrics", "risks", "milestones", "dod",
}

_OPEN_RE = re.compile(r"^:::([a-z][a-z0-9-]*)(\s+.*)?$")
_CLOSE_RE = re.compile(r"^:::$")


def _iter_blocks(markdown: str):
    """Yield (name, body) for every `:::name … :::` block opened at line start —
    mirrors the adapters' BLOCK_OPEN_RE so we see exactly what they would see.
    Inline `:::name` inside prose/backticks/table cells is ignored."""
    lines = markdown.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        m = _OPEN_RE.match(lines[i].strip())
        if m:
            name = m.group(1)
            body: list[str] = []
            j = i + 1
            while j < len(lines) and not _CLOSE_RE.match(lines[j].strip()):
                body.append(lines[j])
                j += 1
            yield name, "\n".join(body).strip()
            i = j
        i += 1


def _block_names(markdown: str) -> set[str]:
    return {name for name, _ in _iter_blocks(markdown)}


def _data_file(repo_root, name: str) -> str:
    return (repo_root / "data" / name).read_text(encoding="utf-8")


def _skill_prd_template_part_a(repo_root) -> str:
    # Part A is the prd-author skill's HTML page template.
    return (
        repo_root / "skills" / "prd-author" / "templates" / "prd-template.html"
    ).read_text(encoding="utf-8")


def _skill_prd_template_part_b(repo_root) -> str:
    # Part B moved to the standalone implementation-spec skill (v4.2 split).
    return (
        repo_root / "skills" / "implementation-spec" / "templates"
        / "implementation-spec-template.md"
    ).read_text(encoding="utf-8")


EVIDENCE_FILES = ("sprntly_evidence_template.md", "sprntly_evidence_sample.md")
PRD_FILES = ("sprntly_prd_template.md", "sprntly_prd_sample.md")
# Only the SAMPLE still carries the rich `:::block` vocabulary — it's the
# reference doc that exercises every adapter case so PRDs generated before the
# lean-markdown switch keep rendering. The LIVE template (sprntly_prd_template.md)
# is now lean markdown with NO blocks (guarded separately below).
PRD_RICH_FILES = ("sprntly_prd_sample.md",)


# ── asset integrity: JSON bodies parse ────────────────────────────────────────

@pytest.mark.parametrize("fname", EVIDENCE_FILES + PRD_FILES)
def test_every_json_block_body_is_valid_json(repo_root, fname):
    """A `:::hero`/`:::tldr`/… body that isn't valid JSON makes the adapter
    silently fall back to a paragraph. Catch the broken edit at the source."""
    json_blocks = EVIDENCE_JSON_BLOCKS | PRD_JSON_BLOCKS
    md = _data_file(repo_root, fname)
    for name, body in _iter_blocks(md):
        if name in json_blocks:
            try:
                json.loads(body)
            except json.JSONDecodeError as e:  # pragma: no cover - failure path
                pytest.fail(f"{fname}: :::{name} body is not valid JSON — {e}")


# ── vocabulary lock ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("fname", EVIDENCE_FILES)
def test_evidence_files_emit_the_expected_block_vocabulary(repo_root, fname):
    """Lock the evidence block vocabulary. A rename/add here must be mirrored in
    the evidence-adapter (web pipeline-contract.test.ts enforces the other half)."""
    assert _block_names(_data_file(repo_root, fname)) == EVIDENCE_FILE_BLOCKS


@pytest.mark.parametrize("fname", PRD_RICH_FILES)
def test_prd_sample_emits_the_expected_block_vocabulary(repo_root, fname):
    """Lock the rich PRD block vocabulary on the SAMPLE (the back-compat reference
    that keeps pre-lean PRDs rendering). A rename/add must be mirrored in the
    prd-adapter (web pipeline-contract.test.ts enforces the other half)."""
    assert _block_names(_data_file(repo_root, fname)) == PRD_FILE_BLOCKS


def test_live_prd_template_is_lean_markdown(repo_root):
    """The LIVE template the runner injects (data/sprntly_prd_template.md) is now
    lean markdown — NO typed `:::` blocks. New PRDs render as h2/p/ul/table. (The
    matching web guard parses it through the real adapter.)"""
    assert _block_names(_data_file(repo_root, "sprntly_prd_template.md")) == set()


# ── prd-author v4.2 ships two templates: HTML Part A + Markdown Part B ────────
# The runner's *injected* live template stays data/sprntly_prd_template.md
# (guarded above); these guard the skill's own bundled reference templates, which
# v4.2 restructured — Part A as a styled, editable HTML page in the normative
# visual system, Part B as the derived Implementation Spec.

def test_prd_skill_part_a_template_is_html_visual_system(repo_root):
    """prd-author v4.2 Part A is a single-file, editable HTML page in the
    normative visual system, in the v4.1 section order (Context → Problem →
    Evidence → Users → Goal → Hypothesis → Requirements → User input needed →
    Appendix) — not lean markdown. Guard the shape + section order so it can't
    regress."""
    html = _skill_prd_template_part_a(repo_root)
    assert "<!DOCTYPE html>" in html
    assert 'contenteditable="true"' in html  # obviously editable
    # v4.1 normative section order, top to bottom.
    order = ["Context", "Problem", "Evidence", "Users", "Goal", "Hypothesis",
             "Requirements", "User input needed", "Appendix"]
    positions = [html.find(f">{label}") for label in order]
    assert all(p != -1 for p in positions), f"missing section: {order}, {positions}"
    assert positions == sorted(positions), "sections out of v4.1 order"


def test_prd_skill_has_no_scope_assumption_boilerplate(repo_root):
    """The Problem section must NOT carry a canned 'Scope assumption:' lead-in.
    It was removed from the template, the three few-shot examples, and SKILL.md
    so the model stops emitting boilerplate scope caveats. Guard every source so
    it can't regress — the examples matter most, since the model imitates them."""
    prd_dir = repo_root / "skills" / "prd-author"
    sources = [
        prd_dir / "templates" / "prd-template.html",
        prd_dir / "SKILL.md",
        prd_dir / "examples" / "01-perch.html",
        prd_dir / "examples" / "02-tandem.html",
        prd_dir / "examples" / "03-copperline.html",
    ]
    for path in sources:
        text = path.read_text(encoding="utf-8").lower()
        assert "scope assumption" not in text, f"scope-assumption boilerplate in {path.name}"
        assert 'class="scope"' not in text, f"leftover .scope markup in {path.name}"


def test_prd_skill_part_b_template_is_derived_impl_spec(repo_root):
    """Part B is the machine-readable Implementation Spec, derived ONLY from a
    Part A: a B0 derivation header plus EARS requirements traced to Part A IDs."""
    md = _skill_prd_template_part_b(repo_root)
    assert "## B0. Derivation" in md
    assert "## B3. Requirements (EARS, traced to Part A IDs)" in md
    assert "traces to a Part A" in md  # the hard derivation rule


# NOTE: the evidence-runner end-to-end `:::block` persistence test was removed
# when evidence moved to the HTML visual brief. The HTML evidence path's
# end-to-end behavior (skill binding + HTML payload + variant v3) is now covered
# by tests/test_evidence_kg.py and tests/test_evidence_runner.py.
