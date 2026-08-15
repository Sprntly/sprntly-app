# prd-author (v4.7)

Authors **Part A — the PRD humans read, edit and approve** — as a styled, editable, self-contained HTML page. The machine-readable **Part B (Implementation Spec)** is a *separate* skill, `implementation-spec`, derived only from a finished Part A.

**Pipeline:** `evidence-brief` → **`prd-author`** (Part A) → human approves/edits → `implementation-spec` (Part B) → coding agent → `prd-critique` on the loop back.

---

## The four hard rules

These survive every mode, every template, and every instruction to the contrary. They are stated once at the top of `SKILL.md` so later edits cannot quietly erode them.

| # | Rule |
|---|---|
| 1 | **Evidence is capped at 3 items.** Maximum three — not four, not five. Standard mode, detailed mode, or a template that lists twelve. `[NEED]` rows carry no claim and don't count. |
| 2 | **Nothing in Evidence may be authored by the model.** Every claim, magnitude, source, date, type label and quote comes from the caller's input or a connected system. |
| 3 | **Unknowns are asked, never guessed.** Anything missing becomes `[NEED]` or `[ESCALATE]` with a named owner — including estimates, team sizes, dates, baselines and product decisions. |
| 4 | **The author byline comes from the logged-in identity**, or renders `[NEED: author]`. Never typed, never guessed. |

### How rule 2 is actually enforced

A prohibition alone doesn't stop fabrication, so there is a **check that runs before emit**. For each evidence item, name the specific artifact it came from — not the *type* of artifact, the actual one in this conversation: *"the attached research doc, the line about Q2 tickets"*, *"the telemetry table the caller pasted"*, *"clarify-gate answer 2"*.

**If the artifact cannot be named, the item was authored, and it does not ship.** It becomes a `[NEED]` or it is dropped. With the section capped at three, there is no volume pressure that could justify keeping a doubtful item.

Valid sources are exactly two: **the caller's input** (prompt, evidence brief, pasted transcript, research doc, uploaded file, clarify-gate answers) and **the system** (a connected signal, dataset, ticket export or review corpus; the project's knowledge base or knowledge graph; a document attached to the project or conversation).

Supporting rules: never invent a source name · never invent a date or period · never construct a quote from remembered substance (verbatim or it isn't a quote) · never restate a supplied number as a rounded, rescaled or extrapolated one · **an assumption is not evidence** — assumptions live in Hypothesis, Risks, the riskiest-assumption box, or a tagged step in a projected-impact chain · never pad a thin section.

### Illustrative mode

Run with no real inputs — a demo, a template fill, a worked example — the page carries a visible label under the byline reading *Illustrative example — evidence is fictional, not sourced*, and every evidence type label is prefixed `EXAMPLE`. There is no silent illustrative mode: a document that looks sourced must be sourced.

---

## Structure (house format)

`Context → Problem → Evidence → Users → Goal → Hypothesis → Requirements → Risks` — the document ends at Risks.

- **Title** — 4–8 words, reasoned rather than assembled. Names the change, not the essay about the change. Must fit a roadmap row and still mean something.
- **Context** — *reason it out, then write the delta.* Answer two questions before writing: what must a VP already hold in their head for this document to land, and what do they already know that you must not spend a word on? Context is the gap between those and nothing else. The reader works at the company — no company or product explainer. ~70 words.
- **Problem** — opens on the user's concrete situation, one number sizing it, then a single trailing clause on business cost. Must survive being lifted out and read alone. ~60 words.
- **Evidence** — max 3, mixed types, provenance-checked. Under the cap, keep the items that do distinct work: one that sizes the problem, one that shows the failure in the user's own words, one that corroborates independently.
- **Users** — two maximum. More length never buys more personas.
- **Goal** — one primary metric with formula and baseline. Projected impact is filled with an assumption chain and confidence tag, or renders as the **designed blank slot** — never faked. Guardrails separate.
- **Hypothesis** — one plain-English sentence: *If we do X, then Y will happen, which moves Z.* No restatement of the problem.
- **Requirements** — table with `Happy path / Edge case / Failure` types. These tags are load-bearing: `implementation-spec` inherits them to decide which EARS branches are mandatory.
- **Risks** — in the body, closing the document. Named risks, then exactly one riskiest assumption in its boxed callout with a 3-line pre-mortem.

Retired from the house format: the Appendix and its User input needed list (v4.8 — unknowns render inline as `[NEED]`/`[ESCALATE]` where they occur), plus Non-goals, Alignment, Rollout, Done-when (v4.4). They return only if an adopted template asks for them.

---

## Length modes

| Mode | Budget (excluding the Requirements table) | Trigger |
|---|---|---|
| **Standard** | ~750 words | default |
| **Detailed** | longer, with more material | **only** on explicit request |

**Detailed is never inferred.** A rich input pile, a big codebase or a complex product are not requests. Input volume has no bearing on output length.

Detailed buys *material*, not adjectives: long-mode requirement columns (`Priority / Signal / Acceptance`), a mitigation or detection signal per risk, thresholds on guardrails, more of the mechanic in Context. **Evidence does not grow** — the 3-item cap is hard in detailed mode too. Users stays at two.

**If there is no more grounded material, the PRD stays standard length and says so.** Padding to reach a word count is the same failure as inventing a source.

---

## Template adoption

When the caller supplies a template — a blank company form, a filled PRD to match, an exported doc, a named house format — **adopt it.** The template governs the shape; the skill governs whether what's in it is true.

**Step 1 — build the correspondence map (internal).** Read the template first and work out, section by section, *what are they calling what?* Every house concept either has a home under a different name or has no home at all. Typical: their "TL;DR" carries the problem, "User Stories" may be carrying requirements, "Potential Challenges" is risks, "Success Metrics" is the goal split across buckets.

**Step 2 — adopt their form of expression, not just their headings.** If they express behavior as user stories, write user stories. As a bulleted functional list with inline priorities, write that. As narrative prose, write prose. The house requirements table is a house convention; it does not travel into a template that doesn't use tables.

**Step 3 — keep house rigor inside their form.** Adopt their vocabulary and format; keep our reasoning and standard of evidence. Whatever form requirements take, **Happy path / Edge case / Failure coverage must still be there** — as failure stories, edge bullets, or prose naming what happens when the thing breaks.

**Not adopted:** the visual system (unless brand tokens are supplied), every judgment rule, and **the evidence capacity** — a template expecting eight research entries still gets three.

**Conflicts:** a template section with no grounded material renders carrying `[NEED]` + owner, never deleted and never invented. A required judgment element with no slot is placed at the nearest logical point with a note saying it was added.

**Estimate sections are the fabrication trap.** Templates routinely ask for project estimate, team size, phase durations and launch dates. These are the single most common place a generated PRD invents something authoritative-looking, and they are owned by engineering, not derivable from a problem statement. They render `[NEED: … — owner: Eng lead]` unless supplied. Sequencing *is* derivable from dependencies and should be stated even when duration cannot be.

---

## What changed — and why

| Version | Change | Why |
|---|---|---|
| **4.3** | **Evidence provenance** as a hard rule | Assumptions were entering Evidence wearing invented source names and dates — the one section where a reader stops checking and starts believing |
| 4.3 | Illustrative mode | Demo output looked sourced; anything unsourced must say so |
| **4.4** | Risks moved into the body | Reader sees what we're building, then what breaks it, then what they must decide |
| 4.4 | Non-goals, Alignment, Rollout, Done-when retired | Appendix furniture that approvers skipped |
| 4.4 | User input needed → Appendix | Keeps the argument uninterrupted |
| 4.4 | Cold-reader test → **informed-insider test** | The reader works here; a paragraph explaining the company tells a VP nothing |
| 4.4 | Length budgets, detailed mode opt-in | Prevents length inflation from rich inputs |
| **4.5** | Template adoption | Company templates were being only partially honored |
| **4.6** | Template **semantic mapping** (3 steps) | Adopting headings without adopting *form* produced hybrids — their section names over our table |
| 4.6 | Title reasoned, 4–8 words | Titles were absorbing the Problem section |
| 4.6 | Context reasoned, not formulaic | A word count doesn't tell you *which* 70 words |
| 4.6 | Evidence cap made hard at 3, all modes | Detailed mode had been allowed to raise it to 6 |
| **4.7** | **Hard-rules block at the top of `SKILL.md`** | Four rules had been restated across seven scattered locations and were eroding one edit at a time |
| **4.7** | **Pre-emit provenance check, per item** | A prohibition doesn't stop fabrication; naming the supplying artifact does |
| 4.7 | Provenance sources widened | Now explicitly includes project knowledge bases, knowledge graphs, and attached documents |
| 4.7 | Evidence cap added to template "not adopted" list | A template expecting eight entries still gets three |
| **4.8** | **Appendix (User input needed) retired** | Owner decision 2026-08-14: the open-items list read as unfinished work shipped inside the document; unknowns stay inline as `[NEED]`/`[ESCALATE]`, and only a company template that defines an open-items section renders one |

### Known open items

- **Detailed mode has no reliable multiplier.** Capping evidence at 3 removed the largest honest expansion lever; detailed now lands around 1.3× standard rather than the 1.5–2× originally specified. Defining detailed by *content added* rather than word ratio is the likely fix.
- **Template-shaped PRDs carry no type pills.** Where a template uses stories or prose instead of a table, Happy/Edge/Failure coverage is present but not machine-readable. `implementation-spec` currently inherits those branches by reading pills, so it must either learn to classify from prose or receive an invisible annotation.
- **The house length budget does not govern adopted templates.** An eleven-section corporate template naturally runs well past the floor; an adopted template arguably defines its own length.
- **`examples/` still shows the v4.2 shape** — retired appendix sections, cold-reader Context, no illustrative labels. Replacements should come from a clean v4.7 run.

---

## Contents

- `SKILL.md` — the authoritative spec
- `templates/prd-template.html` — Part A skeleton with `{{placeholders}}`
- `assets/prd.css` — canonical stylesheet, injected into the empty `<style>` block at save time
- `examples/01-perch.html` · `02-tandem.html` · `03-copperline.html` — **stale, v4.2 shape** (see open items)

## Visual system

Spectral title · Inter body · IBM Plex Mono for IDs, formulas, tags and the byline. A white Word-style document page, green accent `#1A6B47`, obviously editable — dashed hover outlines, green caret — with print stripping the affordances. Requirements table with colour-coded type pills. `[ESCALATE]`/`[NEED]` as tinted mono tags with owners. Hypothesis on a green left rule. Riskiest assumption boxed with a red-brown left rule and its pre-mortem inside. A blank Projected impact renders as a dotted slot — the honesty rule made visible. Full token spec in `SKILL.md`.
