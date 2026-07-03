# prd-author (v4.2)

Author PRDs in two parts, from two separate calls:

- **Part A — PRD (for people):** a styled, editable HTML page. Lean structure, author byline, appendix inline. This is what humans read, edit, and approve.
- **Part B — Implementation Spec (for your coding agent):** a Markdown `.md` file, derived ONLY from a Part A. This is what Claude Code / Cursor builds from.

## How to use

**1. Generate the human PRD:**
> "Run prd-author part-a on [signals / evidence brief / problem statement]"

The skill mines your artifacts first, asks at most 5 clarifying questions, and produces the Part A page. The author byline is filled automatically from the logged-in user generating the PRD — it is never typed or guessed ([NEED: author] if identity is unavailable). Edit the page directly (it's contenteditable), print to PDF, or export to Word.

**2. Derive the machine spec:**
> "Run prd-author part-b on [the Part A file / the PRD we just made]"

Part B opens with a B0 Derivation header naming its source Part A. Every EARS requirement in B3 traces to a Part A requirement ID — nothing enters Part B that a human didn't approve in Part A. Calling part-b with no Part A generates Part A first; it never skips.

## Evidence must link to Sprntly (required)
Evidence is source-agnostic — data analysis, user quotes (always verbatim), customer complaints, competitive analysis, churn interviews, session recordings, surveys, and anything else Sprntly ingests — and each item wears a type label naming which kind it is. **The PRD's evidence must link to the evidence in Sprntly through one link:** the Evidence section header carries a single `View evidence page in Sprntly ↗` link to this PRD's evidence page (app.sprntly.ai/evidence/{prd-evidence-id}), which aggregates every underlying signal with its source and analysis. Per-item links are not used — one clean link, one page, everything behind it. The link comes from the PRD's real evidence-page ID at generation time and is never fabricated; signals not yet in Sprntly carry the note that they appear on the evidence page when they land. The example PRDs use placeholder page IDs because their companies are fictional.

## Branding on shareable artifacts
Every Part A carries the **VoidAI mark** top-right inside the document — the ring-and-dot glyph with `by VoidAI` in small mono. It sits in the page, not the chrome, so anything shared externally (printed, exported to PDF or Word, forwarded) travels with the mark. It is non-editable and deliberately quiet: identifiable at a glance, invisible when reading.

## The visual system (normative — all Part A pages align to it)
Spectral title · Inter body · IBM Plex Mono for IDs, formulas, tags, byline. A clean white Word-style document page on a neutral desk, green accent (#1A6B47) — and obviously editable: an "Editing on" pill in the chrome, dashed hover outlines on every block, a green caret. Print/export strips the affordances for a clean document. Requirements table with color-coded type pills (Happy path / Edge case / Failure). [ESCALATE] and [NEED] as tinted mono tags with owners. Hypothesis on a green left rule. Appendix under a heavy rule with the riskiest assumption boxed and its 3-line pre-mortem inside. **A blank Projected impact renders as a dotted slot** — the honesty rule made visible. Section order (v4.1): Context → Problem → Evidence → Users → Goal → Hypothesis → Requirements → User input needed → Appendix. Context must pass the cold-reader test (a stranger to the company can judge the document); every Evidence item links to its evidence page in Sprntly. Full token spec lives in SKILL.md.

## Contents
- `SKILL.md` — authoritative v4 spec (invocation, derivation rule, byline rule, visual tokens, quality bar)
- `templates/prd-template-part-a.html` — Part A skeleton in the visual system, with {{placeholders}}
- `templates/prd-template-part-b.md` — Part B skeleton incl. B0 derivation and merged B8 tests + DoD
- `examples/01-perch--part-a.html` + `01-perch--part-b.md` — default shape; **Projected impact BLANK** (designed slot); evidence mixes data analysis, a verbatim user quote, churn interviews, competitive analysis
- `examples/02-tandem--part-a.html` + `02-tandem--part-b.md` — **Projected impact FILLED** with assumption chain + confidence tag; single-user variant
- `examples/03-copperline--part-a.html` + `03-copperline--part-b.md` — three [ESCALATE] items carried A→B; failure-heavy requirement set

## Pipeline position
evidence-brief → **prd-author part-a** → human approves/edits → **prd-author part-b** → coding agent executes → prd-critique on the loop back.
