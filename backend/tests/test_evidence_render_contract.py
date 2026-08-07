"""THE EVIDENCE RENDER CONTRACT — the thing the LLM/skill split must not break.

Moving the brief's CONTENT from the vendored `evidence-brief` skill into the
prompt (evidence-kg-v6) frees the model's prose. It must not free its markup:
`evidences.payload_md` is read by six consumers, and one of them decides which
renderer runs from the FIRST FEW CHARACTERS of the payload.

The contract, and what breaks when each clause slips:

  1. The payload starts with `<!doctype` / `<meta` / `<html` / `<div` / `<style`.
     `web/app/lib/htmlBrief.ts::looksLikeHtmlBrief` is an anchored sniff and
     `markdownToEvidenceState` branches on it ALONE — no variant fallback — so a
     one-sentence preamble sends the whole document into the legacy `:::block`
     parser: zero sections, BLANK panel, on the artifact panel, the Artifacts
     screen and every share link. (The full-page EvidenceScreen survives on
     `variant === "v3"`; nothing else does.)
  2. Exactly one `<style>`, holding the canonical stylesheet the SERVER injects.
  3. Canonical class vocabulary — `.wrap` above all, which both the stylesheet
     and the viewer's width override key on.
  4. Charts are inline `<svg>`; no `<script>`, no external URL. The viewer's
     iframe is `sandbox="allow-same-origin"` with NO `allow-scripts`, so
     anything needing JS renders as nothing.
  5. No `class="hyp"` — the viewer strips that component.
  6. Rows keep `variant == "v3"`, which is what the MCP tool derives
     `content_format: "html"` from.

The golden fixture is `tests/fixtures/evidence/golden_brief.html`, deliberately
shared with `web/app/lib/__tests__/evidence-render-contract.test.ts` so both
runtimes assert against the SAME bytes.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.evidence_html import EvidenceHtmlError, normalize_evidence_html
from app.skills.loader import get_skill

REPO_BACKEND = Path(__file__).resolve().parents[1]
GOLDEN = REPO_BACKEND / "tests" / "fixtures" / "evidence" / "golden_brief.html"
SKILL_MD = REPO_BACKEND / "skills" / "evidence-brief" / "SKILL.md"

# The sniff, transcribed from web/app/lib/htmlBrief.ts. If these two ever
# disagree the frontend is right and this is the bug.
_SNIFF = re.compile(r"^\s*<(?:!doctype|meta|html|div|style)\b", re.IGNORECASE)

CANONICAL_CSS = get_skill("evidence-brief").assets["evidence.css"]


@pytest.fixture
def golden() -> str:
    return GOLDEN.read_text(encoding="utf-8")


def _stored(raw: str) -> str:
    """What the runners persist into `payload_md` for a model output."""
    return normalize_evidence_html(raw, CANONICAL_CSS)


# ── 1. the contract itself, on the golden brief ──────────────────────────────

def test_golden_brief_satisfies_the_render_contract(golden):
    """The regression guard. Every clause of the contract, on one document."""
    html = _stored(golden)

    assert _SNIFF.match(html), "payload must open with a tag looksLikeHtmlBrief accepts"
    assert html.count("<style>") == 1, "exactly one stylesheet element"
    assert "--problem:#dd4b32" in html, "canonical stylesheet was injected"
    assert '<div class="wrap"' in html
    assert "<svg" in html and "<figure>" in html and "<figcaption>" in html
    assert "<script" not in html.lower()
    assert 'class="hyp"' not in html
    assert "```" not in html
    assert ":::" not in html
    # No network: the sandboxed iframe has no origin to fetch from anyway, and
    # a remote font/image would render as a hole.
    assert not re.search(r"""(?:src|href)\s*=\s*["']https?://""", html, re.I)


@pytest.mark.parametrize(
    "cls",
    ["wrap", "eyebrow", "deck", "meta", "tldr", "opp-top", "tag", "context",
     "kicker", "voc", "q", "ch", "extract", "yes", "no", "us",
     "ax", "vlabel", "blabel"],
)
def test_every_canonical_class_is_defined_by_the_injected_stylesheet(cls):
    """The skill's vocabulary and the server-injected stylesheet must agree.
    A class named in SKILL.md but absent from evidence.css renders unstyled —
    silently, which is how the look drifts one brief at a time."""
    assert re.search(rf"\.{re.escape(cls)}\b", CANONICAL_CSS), (
        f".{cls} is in the skill's vocabulary but not in assets/evidence.css"
    )


def test_golden_brief_uses_only_the_canonical_vocabulary(golden):
    """The inverse: the golden brief must not reach for a class the stylesheet
    does not define. This is exactly what the five worked briefs used to do —
    each shipped its own `<style>` with private classes (`.kicker.w`) and
    tokens (`--warn`), teaching the model markup the canonical sheet ignores."""
    defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", CANONICAL_CSS))
    used = set(re.findall(r'class="([^"]+)"', golden))
    names = {n for value in used for n in value.split()}
    assert names <= defined, f"undefined classes in the brief: {sorted(names - defined)}"

    tokens = set(re.findall(r"--([a-z-]+):", CANONICAL_CSS))
    referenced = set(re.findall(r"var\(--([a-z-]+)\)", golden))
    assert referenced <= tokens, (
        f"undefined CSS variables in the brief: {sorted(referenced - tokens)}"
    )


# ── 2. malformed output — what the pipeline does when the model deviates ─────

def test_leading_prose_is_stripped_so_the_sniff_still_passes(golden):
    """THE failure this change makes more likely, and the one that renders
    blank rather than ugly. A chattier model is the whole point of the split."""
    html = _stored("Here's the evidence brief you asked for:\n\n" + golden)
    assert _SNIFF.match(html)
    assert "Here's the evidence brief" not in html
    assert '<div class="wrap"' in html


def test_a_code_fence_is_stripped(golden):
    html = _stored(f"```html\n{golden}\n```")
    assert "```" not in html
    assert _SNIFF.match(html)


def test_fence_and_preamble_together(golden):
    html = _stored(f"Sure — here it is.\n\n```html\n{golden}\n```")
    assert "```" not in html and "Sure" not in html
    assert _SNIFF.match(html)


def test_trailing_commentary_is_dropped(golden):
    html = _stored(golden + "\n\nLet me know if you'd like a different chart.")
    assert "Let me know" not in html
    assert html.rstrip().endswith(">")


def test_a_document_opening_on_an_unrecognised_tag_is_made_to_sniff(golden):
    """A brief that starts at `<section>` is valid HTML and completely broken
    for the panel. Injection prepends the stylesheet, which fixes it; the meta
    prefix is the belt-and-braces for the case where it cannot."""
    html = _stored("<section><h2>No wrapper</h2></section>")
    assert _SNIFF.match(html)


def test_scripts_are_stripped_from_the_stored_payload(golden):
    """The sandbox already neutralises scripts. Stripping them keeps the stored
    artifact honest for the consumers that never render it — the MCP tool, the
    PRD-tab chat context, the downstream QA/design/risk agents."""
    dirty = golden.replace(
        '<div class="wrap">',
        '<div class="wrap"><script>fetch("/steal")</script>',
    )
    html = _stored(dirty)
    assert "<script" not in html.lower()
    assert "fetch(" not in html
    assert '<div class="wrap"' in html


def test_output_with_no_html_at_all_fails_loudly(golden):
    """A markdown answer stored as-is renders as an empty page and nothing
    errors — the silent-wrong shape. Raise instead: the caller marks the row
    `failed`, which the UI already surfaces with an explicit retry."""
    with pytest.raises(EvidenceHtmlError):
        _stored("# Evidence\n\nThe signals converge on SSO.")


def test_normalisation_is_idempotent(golden):
    once = _stored(golden)
    assert _stored(once) == once


# ── 3. the split — what each layer is now allowed to say ─────────────────────

# Verbs that decide CONTENT. After the split these belong to the prompt; the
# skill must not carry a second, drifting copy of them.
_ANALYSIS_IN_SKILL = [
    r"\bconverge\b(?!nce diagram)",
    r"\bthe wedge\b",
    r"honesty pass",
    r"never invent",
    r"correlation is never called causation",
    r"value-driven hypothesis",
    r"\bVoice of customer matters\b",
]


@pytest.mark.parametrize("pattern", _ANALYSIS_IN_SKILL)
def test_the_skill_no_longer_carries_analysis_instructions(pattern):
    """The skill is the rendering contract. Analysis instructions here are a
    second source of truth one step removed from the code that knows what was
    retrieved — which is the drift this change removes."""
    text = SKILL_MD.read_text(encoding="utf-8")
    assert not re.search(pattern, text, re.I), (
        f"SKILL.md still carries the analysis instruction {pattern!r}; it "
        "belongs in EVIDENCE_KG_SYSTEM"
    )


@pytest.mark.parametrize(
    "fragment",
    ['<div class="wrap">', "EMPTY", "inline `<svg", "class=\"hyp\"",
     "canonical class", "component"],
)
def test_the_skill_still_carries_the_format_contract(fragment):
    """The other half of the split: reducing the skill must not hollow it out.
    These are the clauses the renderer actually depends on."""
    assert fragment.lower() in SKILL_MD.read_text(encoding="utf-8").lower()


def test_the_prompt_now_owns_the_analysis():
    """Every content rule the skill dropped has to have landed somewhere."""
    from app.prompts import EVIDENCE_KG_SYSTEM as sys_prompt

    for phrase in ("converge", "wedge", "honesty pass", "Never invent",
                   "correlation", "Confidence:"):
        assert phrase.lower() in sys_prompt.lower(), f"prompt lost {phrase!r}"
    # …and it still defers markup to the bound skill rather than restating it.
    assert "RENDERING CONTRACT" in sys_prompt


def test_prompt_version_moved_with_the_split():
    """The decision log pins answers to a prompt version. A prompt this
    different that kept v5 would make the audit spine lie."""
    from app.prompts import EVIDENCE_KG_PROMPT_VERSION

    assert EVIDENCE_KG_PROMPT_VERSION == "evidence-kg-v6"


def test_the_skill_is_still_bound_and_loadable():
    """The gateway folds SKILL.md + references/* onto the cacheable prefix and
    records `content_hash` in prompt_version; assets/evidence.css is loaded by
    the runners. Trimming the skill must not break either."""
    spec = get_skill("evidence-brief")
    assert spec.method.strip()
    assert "component-reference.html" in spec.references
    assert "evidence.css" in spec.assets
    assert spec.content_hash


def test_a_preamble_containing_angle_brackets_is_not_mistaken_for_the_document(golden):
    """`<below>` in the prose is not where the brief starts. Preferring a known
    doc-start tag over "any tag at all" is what keeps the cut in the right
    place — the any-tag fallback exists only for a document that opens on
    something else entirely."""
    html = _stored("Here's the brief (details <below>):\n\n" + golden)
    assert _SNIFF.match(html)
    assert "below" not in html.split("<div", 1)[0]
    assert '<div class="wrap"' in html


def test_markup_before_the_wrapper_is_kept_not_cut(golden):
    """The other side of the tie-break, and the one that would LOSE content:
    a brief whose `<h1>` sits outside `<div class="wrap">` is off-contract but
    real. Cutting to the first doc-start tag would silently delete the title, so
    a closing tag ahead of the wrapper pins the cut to the earlier tag."""
    html = _stored('<h1>Real title</h1>\n' + golden.split("\n", 2)[2])
    assert "<h1>Real title</h1>" in html
    assert _SNIFF.match(html)      # injection/meta prefix still makes it sniff
