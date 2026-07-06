# prd-author (v4.2)

Author **Part A — the PRD for people**: a styled, editable HTML page. Lean structure, author byline, appendix inline. This is what humans read, edit, and approve. The machine-readable **Part B — Implementation Spec (for your coding agent)** is a *separate* skill, `implementation-spec`, derived only from a finished Part A.

## How to use

**Generate the human PRD:**
> "Run prd-author on [signals / evidence brief / problem statement]"

The skill mines your artifacts first, asks at most 5 clarifying questions, and produces the Part A page as a single self-contained HTML document. The author byline is filled automatically from the logged-in user generating the PRD — it is never typed or guessed ([NEED: author] if identity is unavailable). Edit the page directly (it's contenteditable), print to PDF, or export to Word.

**Then derive the machine spec (separate skill):**
> "Run implementation-spec on [the Part A we just made]"

`implementation-spec` opens with a B0 Derivation header naming its source Part A. Every EARS requirement traces to a Part A requirement ID — nothing enters Part B that a human didn't approve in Part A. It generates only from a finished Part A; if none exists, author Part A here first.

## Evidence must link to Sprntly (required)
Evidence is source-agnostic — data analysis, user quotes (always verbatim), customer complaints, competitive analysis, churn interviews, session recordings, surveys, and anything else Sprntly ingests — and each item wears a type label naming which kind it is. The Evidence section header is a plain label and carries **no link** (neither a "View evidence page" link nor per-item links). Signals not yet in Sprntly carry the note that they appear on the evidence page when they land.

## The visual system (normative — all Part A pages align to it)
Spectral title · Inter body · IBM Plex Mono for IDs, formulas, tags, byline. A clean white Word-style document page on a neutral desk, green accent (#1A6B47) — and obviously editable: an "Editing on" pill in the chrome, dashed hover outlines on every block, a green caret. Print/export strips the affordances for a clean document. Requirements table with color-coded type pills (Happy path / Edge case / Failure). [ESCALATE] and [NEED] as tinted mono tags with owners. Hypothesis on a green left rule. Appendix under a heavy rule with the riskiest assumption boxed and its 3-line pre-mortem inside. **A blank Projected impact renders as a dotted slot** — the honesty rule made visible. Section order (v4.1): Context → Problem → Evidence → Users → Goal → Hypothesis → Requirements → User input needed → Appendix. Context must pass the cold-reader test (a stranger to the company can judge the document); every Evidence item links to its evidence page in Sprntly. Full token spec lives in SKILL.md.

## Contents
- `SKILL.md` — authoritative v4 spec (invocation, byline rule, visual tokens, output contract, quality bar)
- `templates/prd-template.html` — Part A skeleton in the visual system, with {{placeholders}}
- `examples/01-perch.html` — default shape; **Projected impact BLANK** (designed slot); evidence mixes data analysis, a verbatim user quote, churn interviews, competitive analysis
- `examples/02-tandem.html` — **Projected impact FILLED** with assumption chain + confidence tag; single-user variant
- `examples/03-copperline.html` — three [ESCALATE] items; failure-heavy requirement set

The paired Part B (Implementation Spec) for each example lives in the `implementation-spec` skill's `examples/`.

## Pipeline position
evidence-brief → **prd-author** (Part A) → human approves/edits → **implementation-spec** (Part B) → coding agent executes → prd-critique on the loop back.
