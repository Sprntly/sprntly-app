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
  - The block VOCABULARY is locked to an explicit expected set, so renaming or
    adding a block forces an update here — the cue to also update the frontend
    adapter case (guarded on the web side by `pipeline-contract.test.ts`).
  - The LIVE `prd-author` skill template keeps the two-part (Part A / `---` /
    Part B) structure the runner's `_split_2part` + per-part directives rely on.
  - End to end: the evidence runner persists the rich-block markdown intact
    (status=ready, payload_md carries every `:::block`), so a generated evidence
    page actually reaches the adapter in the shape it expects.

The matching frontend guard reads these SAME files through the real adapters;
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


def _skill_prd_template(repo_root) -> str:
    return (
        repo_root / "skills" / "prd-author" / "templates" / "prd-template.md"
    ).read_text(encoding="utf-8")


EVIDENCE_FILES = ("sprntly_evidence_template.md", "sprntly_evidence_sample.md")
PRD_FILES = ("sprntly_prd_template.md", "sprntly_prd_sample.md")


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


@pytest.mark.parametrize("fname", PRD_FILES)
def test_prd_files_emit_the_expected_block_vocabulary(repo_root, fname):
    assert _block_names(_data_file(repo_root, fname)) == PRD_FILE_BLOCKS


# ── live PRD skill template keeps the two-part structure ──────────────────────

def test_prd_skill_template_keeps_two_part_structure(repo_root):
    """The runner generates Part A + Part B against this template and relies on
    the `# Part A` / `---` / `# Part B` shape (`_split_2part`, the per-part
    directives). Guard it so a template refactor can't silently break the split."""
    md = _skill_prd_template(repo_root)
    assert "# Part A" in md
    assert "# Part B" in md
    # A standalone horizontal-rule line separates the two halves.
    assert any(line.strip() == "---" for line in md.split("\n")), "missing `---` separator"


# ── end-to-end: evidence runner persists the rich-block markdown intact ───────

def test_evidence_runner_persists_rich_blocks_intact(
    isolated_settings, fake_llm, monkeypatch, repo_root
):
    """Drive the real evidence runner with the canonical sample as the model
    output; the stored row must be ready and carry every `:::block` marker
    unchanged — proving a generated evidence page reaches the adapter in the
    shape the adapter (and UI) expect."""
    from app import evidence_runner

    db_mod = isolated_settings["db"]
    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text("corpus body")
    payload = {"summary_headline": "stub", "insights": [{"title": "Insight A"}],
               "_schema_version": 1}
    brief_id = db_mod.save_brief(
        dataset="asurion", week_label="Week of stub", payload=payload, schema_version=1
    )
    evidence_id = db_mod.start_evidence(
        brief_id=brief_id, insight_index=0, title="t", template_version=1, variant="v2",
    )

    sample_md = _data_file(repo_root, "sprntly_evidence_sample.md")
    monkeypatch.setattr(evidence_runner, "call_md", lambda **kw: sample_md)

    evidence_runner._run_sync(evidence_id, brief_id, 0)

    row = db_mod.get_evidence(evidence_id)
    assert row["status"] == "ready"
    assert row["payload_md"] == sample_md
    for block in EVIDENCE_FILE_BLOCKS:
        assert f":::{block}" in row["payload_md"], f"lost :::{block} through generation"
