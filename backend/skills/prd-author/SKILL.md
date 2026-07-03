---
name: prd-author
description: >
  Author a Product Requirements Document from a problem, signals, and (optionally)
  business context, a company PRD template, or a codebase. Two invocations:
  `part-a` generates the human-readable PRD as a styled, editable HTML page;
  `part-b` generates the machine-readable Implementation Spec as a Markdown (.md)
  file, derived ONLY from an existing Part A. Part A carries an author byline
  taken from the logged-in user.
---

# prd-author (v4.2)

## Purpose
Turn a signal (or a one-line idea) into a decision-ready PRD a human can approve in minutes (Part A), and — once Part A exists — an implementation spec a coding agent can execute losslessly (Part B). Part A is for **people to approve**; Part B is for **your coding agent to build from**.

## Invocation — two separate calls
| Call | Input | Output |
|---|---|---|
| `prd-author part-a` | Problem/signals/evidence brief (+ optional company template, codebase) | **Part A** — human PRD as a styled, editable HTML page (visual spec below), Word-exportable / print-to-PDF |
| `prd-author part-b <part-a>` | An existing Part A (file or the one just generated in-session) | **Part B** — Implementation Spec as a Markdown `.md` file |

**Derivation rule (hard):** Part B is generated ONLY from a Part A — never directly from raw signals. Every B3 requirement must trace to a Part A requirement ID; content with no Part A anchor may not appear in Part B. If `part-b` is invoked with no Part A available, generate Part A first (or halt and say so) — do not skip to Part B. This keeps the human-approved document the single source of truth: what the agent builds is exactly what a person signed off on.

## Author byline (v4 — amends the v3 "no metadata" rule)
Part A renders the **author's name directly under the title**, labeled `Author`. The name is taken **directly from the logged-in user** generating the PRD — never typed in, never guessed, never defaulted to a placeholder name. If the generating identity is unavailable, render `[NEED: author]` in the byline slot. No other metadata (status, date, version) returns to the header — the byline is the only exception to the lean-title rule.

## Operating modes
1. **Prose mode (default):** net-new PRD from a problem statement, evidence brief, or idea.
2. **Spec-aware mode:** a prior spec/PRD exists — inherit its requirement IDs, extend rather than renumber, mark deltas explicitly.

## Input handling
- **Mine artifacts before asking.** Read everything provided and extract facts first.
- **Ask at most 5 clarifying questions**, ranked by leverage. Anything unresolved goes to **User input needed**, not into a guess.
- **Company template adaptation:** if a company PRD template is present, map content into *their* section structure — but keep the v4 visual system unless the company supplies its own brand tokens. Template adaptation copies structure, not judgment.
- **Scope assumption up front:** state the scope interpretation at the top of the Problem section.

## Groundedness (internal — never printed)
- **Zero invented numbers.** Every figure traces to a provided source; missing data gets `[NEED: …]`.
- **Signal-linking is non-negotiable.** Every requirement traces to a signal, explicit assumption, or named stakeholder ask.
- **Unresolvable product decisions route to `[ESCALATE]`** — never silently decided.
- Do NOT print a "How this PRD was generated" section.

## Part A — structure (in order — v4.1 order is normative)
The spine reads: what world is this (Context) → what's wrong (Problem) → how we know (Evidence) → who it happens to (Users) → what we'll measure (Goal) → what we believe (Hypothesis) → what we build (Requirements).

1. **Title** — descriptive only.
2. **Author byline** — logged-in user's name, directly under the title.
3. **Context — must pass the cold-reader test.** A reader with zero prior knowledge of the company must come away able to judge the rest of the document. Cover, in one tight paragraph: what the product is and who it serves; how the affected workflow works TODAY, step by step; and a definition of any term of art the document relies on (rendered with a dotted underline). Still no padding — enrichment means the missing facts a stranger needs, not more words.
4. **Problem** — business + user pain; scope assumption stated first; no smuggled solution.
5. **Evidence — rich, linked, and source-agnostic.** Any signal type qualifies: data analysis, user quotes, customer complaints/support tickets, competitive analysis, churn/exit interviews, session recordings, workflow analysis, surveys, sales-call notes, experiment results — the list is open. Each item carries a **type label** on its meta line naming what kind of evidence it is, and a strong Evidence section mixes types rather than leaning on one. **User quotes are verbatim** — word for word, rendered in italic serif with quotation marks; never paraphrase something into a quote. Beyond the type label, each signal carries: the claim with its magnitude in bold, the source, and the date/period. **One link for the whole section:** the Evidence header carries a single `View evidence page in Sprntly ↗` link to this PRD's evidence page at app.sprntly.ai, which aggregates all underlying signals — per-item links are NOT used. The link comes from the PRD's real evidence-page ID at generation time; never fabricate it. `[NEED]` entries note that the item appears on the evidence page when the signal lands.
6. **Users** — two maximum (one is allowed). Users come BEFORE Goal: the reader should know who this happens to before being told what we'll measure about it.
7. **Goal** — ONE primary metric with formula + baseline + **projected impact** (assumption chain + confidence, or a visibly blank slot — the blank is a designed element, see visual spec). Guardrails listed separately, never collapsed into the primary.
8. **Hypothesis** — one tight paragraph directly before Requirements.
9. **Requirements** — table by default: `# | Requirement | Description | Type`, Type ∈ Happy path / Edge case / Failure (never "Core"). Long mode adds Priority / Signal/Source / Acceptance. Prose only on request.
10. **User input needed** — ≤5 items, tagged `[ESCALATE]`/`[NEED]`, each with an owner; self-clearing; section disappears when empty.
11. **Appendix** — renders with Part A, not a separate file: Non-goals · Risks incl. **exactly one named riskiest assumption with a 3-line pre-mortem** · Alignment · Rollout · Done-when.

## Part A — visual specification (normative)
Every Part A renders as a single-file HTML editable page using these tokens. All PRDs generated by this skill must align to this system.

**Canvas — clean Word document, obviously editable:** the page is pure white (`#FFFFFF`) on a neutral desk (`#E9E7E2`), near-square corners (2px), document-margin padding (~72px), soft page shadow — it reads as a Word document, and prints/exports as one. Editability must be *obvious*, not hinted: (1) an `Editing on — click any text to change it` pill in the chrome with a softly pulsing green dot (static under reduced-motion); (2) every block shows a dashed green outline on hover with a text cursor; (3) the caret is accent green and selection is green-tinted. The whole body is `contenteditable`; the print stylesheet strips all chrome and affordances so the export is a clean document.
**Type:** Spectral 600 for the title (~33px); Inter for body (15px) and labels; IBM Plex Mono for IDs, formulas, tags, and the byline.
**Color:** ink `#1F241F`; secondary `#5B615B`; accent green `#1A6B47`; tints — happy `#E7F1EA`/`#1A6B47`, edge `#FBF0DC`/`#8A5A12`, fail `#F9E7E4`/`#9C3223`.
**Brand mark (required on externally shareable artifacts):** every Part A carries the VoidAI mark in the top-right corner of the document itself — a 15px glyph (ink outer ring, solid green center dot: a signal in the void) beside `by VoidAI` in small mono, the name in ink. It lives inside the page, not the chrome, so it survives print, PDF, and Word export. Non-editable (`contenteditable="false"`), quiet, and never larger than specified — identifiable, not distracting.

**Components:**
- *Toolbar chips* above the card: `Part A — PRD (for people)` (green), document name, `prd-author v4`, and the hint "Editable — click anywhere and type."
- *Byline:* mono, small, `AUTHOR` label in accent green, sits tight under the title.
- *Section eyebrows:* 10.5px uppercase, letterspaced, accent green, thin top rule (first section unruled). Section order per the v4.1 structure: Context, Problem, Evidence, Users, Goal, Hypothesis, Requirements, User input needed, Appendix.
- *Terms of art* in Context: dotted underline (`border-bottom: 1.5px dotted`).
- *Evidence section:* the eyebrow row is a flex header — `EVIDENCE` on the left, the single `View evidence page in Sprntly ↗` link right-aligned in mono green. Items sit on dash-ruled rows — claim (magnitudes bold) over a mono meta line: `TYPE-LABEL — source · date`, where the type label is a small tinted chip naming the evidence kind (DATA ANALYSIS, USER QUOTE, CUSTOMER COMPLAINTS, COMPETITIVE ANALYSIS, …). No per-item links. Verbatim quotes render in italic Spectral inside quotation marks.
- *Goal block:* white panel; rows keyed `PRIMARY METRIC / PROJECTED IMPACT / GUARDRAILS` in mono; formula in mono; confidence rendered as a tinted tag (`medium` = edge tint, `low` = fail tint). **Blank projected impact renders as a dotted underline slot** with a muted italic note (e.g. "blank — fills after pilot") — never omitted, never faked.
- *Users:* numbered green discs.
- *Hypothesis:* 3px green left rule, no background fill.
- *Requirements table:* white, hairline rules, uppercase column heads; Type as a color-coded pill (happy/edge/fail tints).
- *User input needed:* checkbox squares + `[ESCALATE]` (fail tint) / `[NEED]` (edge tint) mono tags + owner in secondary color.
- *Appendix:* heavy 2px top rule, `APPENDIX` label, note "Renders with Part A — not a separate file"; **riskiest assumption** in a white box with a red-brown left rule, the 3-line pre-mortem inside it in italic.
**Accessibility/quality floor:** responsive to mobile, type labels never color-only (pill text carries the meaning), print-clean.

## Part B — Implementation Spec (.md, derived from Part A)
1. **B0 Derivation header** — names the source Part A (title + author) it was derived from.
2. **B1 Context for the agent** — what exists, what's being added, constraints.
3. **B2 Stakes gate** — what happens if built wrong; sets verification depth.
4. **B3 Requirements (EARS)** — each traced to a Part A requirement ID (`WHEN <trigger>, the system SHALL <response>`; IF-failure branches included).
5. **B4 Interface contracts** — APIs/schemas/events; no codebase → `[ASSUMPTION → T0]`.
6. **B5 Escalations** — carried over from Part A's `[ESCALATE]` items; the agent must not decide them.
7. **B6 Cross-cutting checklist** — auth, privacy, telemetry, i18n, accessibility, error states, performance.
8. **B7 Tasks** — dependency-ordered; `[P]` parallel-safe; T0 is always the research gate for assumptions.
9. **B8 Acceptance tests & Definition of Done (merged)** — Given/When/Then per B3 requirement incl. failure branches, derived BEFORE implementation; DoD = all tests pass + no open `[ESCALATE]` + cross-cutting addressed or waived + B9 passes.
10. **B9 Independent verification** — separate checker pass: no hallucinated APIs, every requirement has a passing test, Part A ↔ B3 traceability intact.

## Degradation table
| Input available | Behavior |
|---|---|
| Rich signals + codebase + template | Part A in company structure w/ v4 visuals; Part B contracts grounded in code |
| Signals only | Part A full; Part B contracts labeled `[ASSUMPTION → T0]` |
| One-line idea | Part A produced; ≤5 questions; every gap `[NEED]` |
| `part-b` called, no Part A | Generate Part A first, then derive — never skip |
| Logged-in identity unavailable | Byline renders `[NEED: author]` |
| Signal not yet in Sprntly | Plain attribution + "appears on the evidence page when the signal lands" — never a fabricated URL |

## When NOT to use
Discovery ("should we build anything?") → `evidence-brief` / `continuous-discovery`. Reviewing an existing PRD → `prd-critique`. Prioritizing candidates → `prioritize`.

## Quality checklist
- [ ] Two invocations honored; Part B derived only from Part A; B3 fully traced to Part A IDs.
- [ ] Author byline present, sourced from the logged-in user (or `[NEED: author]`) — never invented.
- [ ] Part A matches the visual specification exactly (tokens, components, blank-impact slot, pills, tags, appendix box).
- [ ] The VoidAI brand mark is present top-right inside the document and survives print/export.
- [ ] Section order: Context → Problem → Evidence → Users → Goal → Hypothesis → Requirements → User input needed → Appendix.
- [ ] Context passes the cold-reader test: product + audience, today's workflow, terms of art defined.
- [ ] Every evidence item carries a type label, source, and date; the Evidence header carries the single link to the PRD’s Sprntly evidence page — never fabricated; quotes verbatim; types varied where the signals allow.
- [ ] Problem frames business + user; scope assumption up front; no smuggled solution.
- [ ] Goal: one primary metric w/ formula + baseline; projected impact filled with chain + confidence, or rendered as the designed blank; guardrails separate.
- [ ] Requirements table default with Happy path / Edge case / Failure types; ≤5 user inputs, tagged, owned.
- [ ] Appendix renders with Part A; exactly one riskiest assumption + 3-line pre-mortem.
- [ ] Nothing fabricated; every gap labeled.

## Known gaps / limitations
- Formalizes the input — a wrong premise yields precise-but-wrong output (pair with `prd-critique` upstream).
- Template adaptation copies structure, not judgment.
- Projected impact is only as good as the baseline; with none, it stays a designed blank.
- Author byline depends on the platform exposing the logged-in identity; there is no manual override by design (prevents misattribution).
