---
name: implementation-spec
description: Turn a human PRD (Part A from `prd-author`) into an LLM-readable, agent-executable Implementation Spec (Part B) a coding agent builds and tests against without ambiguity. Use when the user says "turn this PRD into a spec", "make this agent-ready", "implementation spec", "spec-driven", "generate the build spec", or hands a Part A PRD (Context / Problem / Evidence / Users / Goal / Hypothesis / Requirements) and wants the machine-readable half. Derived ONLY from a finished Part A; opens with a B0 derivation header naming its source. Consumes the PRD's typed requirements (Happy path / Edge case / Failure) and produces EARS requirements traced to Part A IDs, interface contracts, dependency-ordered tasks, acceptance tests, and a Definition of Done, with an independent no-hallucination check. Never invents a requirement, rule, or contract — unknowns are split into research-resolvable vs. must-escalate. Pairs with `prd-author` (PRD = what & why; this = how it's built, verified, and done).
---

# Implementation Spec — Part B, the LLM-readable half of a PRD

## What it does
Consumes a **human PRD** (Part A — the *what & why*, authored by `prd-author`) and emits the **agent-executable spec** (Part B — the *how, verified, and done*): the Spec-Driven-Development artifact a coding agent implements and tests against. The PRD declares intent and typed requirements; this skill converts each requirement into testable EARS criteria, binds every fact to a source or labels it, orders the work, and owns completion — because the human PRD deliberately carries no "done when," **done-ness lives here.**

## Derivation rule (hard)
Part B is derived **ONLY from a finished Part A** — never directly from raw signals. The spec opens with a **B0 Derivation header** naming the source Part A (its title + author byline). Every B3 requirement **traces to a Part A requirement ID**; content with no Part A anchor may not appear. If invoked with no Part A available, the Part A PRD must be authored first (via `prd-author`) — never skip to Part B. This keeps the human-approved document the single source of truth: what the agent builds is exactly what a person signed off on.

## Requirement-type inheritance (Part A → Part B)
Part A's Requirements table tags every row `Happy path` / `Edge case` / `Failure`. These tags are load-bearing and inherited verbatim: a **Happy path** row needs only its happy-path EARS line; an **Edge case** row must yield an edge EARS branch + test; a **Failure** row must yield a failure EARS branch + test. (Legacy inline `[edge case]` / `[failure]` tags map to the same branches.)

It **never invents** a requirement, business rule, contract, or metric. Each unknown is either `[ASSUMPTION → T0]` (a coding agent can resolve it by reading the code) or `[ESCALATE]` (a product/external decision no research step can settle — the only places a human is genuinely required).

## When to use / when NOT to use
- **Use** to make a finished PRD buildable by a coding agent (Claude Code, Cursor) without re-interrogating a human mid-run.
- **Do NOT use** to *write* the PRD (that's the PRD skill — run it first), to *critique* a PRD (`prd-critique`), to design deep system architecture (`tech-spec` — this *references* contracts, that *designs* them), or to cut tracker tickets (`user-stories`, which can *inherit* this spec's requirements).

## Inputs
- **Required:** a human PRD with at least a Problem, a Goal/primary metric, and a Requirements list. The PRD's inline tags are load-bearing: **untagged requirement → happy-path EARS; `[edge case]` → an edge branch is mandatory; `[failure]` → a failure branch is mandatory.**
- **Optional grounding (each raises fidelity, none required):** codebase, design system / Figma, prototype, problem evidence, the project **constitution** (engineering standards/conventions/prior decisions), constraints (platform, compliance, timeline). *Missing optional inputs become labeled gaps — derived from the PRD or marked `[ASSUMPTION]` — never invented, never asked of a human mid-run.*
- **Degrades to:** PRD-only — produces a complete spec from the PRD alone, with every contract labeled `[ASSUMPTION → T0]`. Fewer artifacts → more labeled assumptions, never more invention.

## Method (methodology)
Implements Spec-Driven Development (Kiro 3-artifact + EARS, Spec-Kit constitution + self-check, BMAD structured handoff) with independent verification. Steps:

1. **Inventory artifacts & set grounding.** Record which inputs are present (PRD required; codebase/design system/prototype/evidence optional). Each section below grounds against the relevant artifact if present, or labels the gap if absent.
2. **Classify stakes → set the autonomy gate.** Reversible / low-blast-radius → fully autonomous, no human gate. Irreversible / data-loss / billing / security / migration → insert ONE explicit human checkpoint before implementation, naming exactly what it gates. "No human input" means *no unnecessary* input, not *never*.
3. **State the constitution.** Project-wide constraints (stack, conventions, security/compliance, architecture, prior decisions) from context; if absent, state minimal assumed ones as `[ASSUMPTION]`. These bound every requirement so the agent never re-asks.
4. **Convert each PRD requirement into EARS.** `WHEN <event> THE SYSTEM SHALL …` / `WHILE <state> …` / `IF <condition> THEN …` / `WHERE <feature> …` / ubiquitous `THE SYSTEM SHALL …`. **Inherit the PRD tag:** an untagged requirement needs only its happy path; a `[edge case]` requirement must yield an edge EARS line; a `[failure]` requirement must yield a failure EARS line. Each EARS line **traces back to its PRD requirement ID** and **binds to an artifact** (or is labeled `[ASSUMPTION]`).
5. **Design & contracts.** Inputs/outputs, pre/postconditions, invariants, interface/API contracts, data models, state machines, constraints. **Facts must bind verbatim** to the codebase/design system/PRD — anything not literally supported is `[ASSUMPTION → T0]` or `[ESCALATE]`, never invented.
6. **Out of scope / not constrained / unresolved.** Enumerate what the spec deliberately leaves open (the silent gaps that break agents), then split unknowns into `[ASSUMPTION → T0]` vs `[ESCALATE]`. Pull every PRD Open Question into one of these buckets.
7. **Cross-cutting checklist — address or explicitly waive each:** auth/permissions, idempotency, error taxonomy, observability/logging, migration/rollback, performance budget, concurrency. Forces the silent 40% to be named.
8. **Tasks (dependency-ordered).** Atomic, individually verifiable; `[P]` marks parallelizable; **T0 is a mandatory research-before-coding gate** that resolves every `[ASSUMPTION → T0]` and clears the stakes gate; each task maps to the requirement it satisfies.
9. **Acceptance tests & Definition of Done (merged).** For each requirement, a Given/When/Then test *derived before implementation* (so the agent implements to pass tests it did not author) — **including every inherited edge/failure branch.** Then ONE Definition of Done: done = every acceptance test passes AND every requirement has a passing test AND no `[ESCALATE]` is open AND the cross-cutting checklist is addressed/waived AND independent verification passes.
10. **Independent verification (checker ≠ generator; NOT self-grading).** A separate adversarial pass: find any claim no artifact supports; find unstated assumptions; surface cross-artifact conflicts; confirm verbatim binding for every fact/contract; confirm every PRD requirement (and every inherited tag) has a tracing EARS line, a task, and a test. Report PASS + the open `[ESCALATE]` items.

## Output spec (B0–B9 — normative; skeleton in `templates/implementation-spec-template.md`)
A single Markdown document headed **Implementation Spec**, in this order:
1. **B0 Derivation header** — names the source Part A (title + author) it was derived from; asserts every B3 requirement traces to a Part A ID.
2. **B1 Context for the agent** — what exists, what's being added, constraints/constitution.
3. **B2 Stakes gate** — what happens if built wrong; sets verification depth and any single human checkpoint for irreversible work.
4. **B3 Requirements (EARS)** — each traced to a Part A requirement ID (`WHEN <trigger>, the system SHALL <response>`; IF-failure branches included), tags inherited, each bound to an artifact or labeled.
5. **B4 Interface contracts** — APIs/schemas/events; no codebase → `[ASSUMPTION → T0]`; facts bind verbatim.
6. **B5 Escalations** — carried over from Part A's `[ESCALATE]` items; the agent must not decide them.
7. **B6 Cross-cutting checklist** — auth, privacy, telemetry, i18n, accessibility, error states, performance — addressed or explicitly waived.
8. **B7 Tasks** — dependency-ordered; `[P]` parallel-safe; T0 is always the research gate for `[ASSUMPTION → T0]`.
9. **B8 Acceptance tests & Definition of Done (merged)** — Given/When/Then per B3 requirement incl. failure branches, derived BEFORE implementation; DoD = all tests pass + every requirement has a passing test + no open `[ESCALATE]` + cross-cutting addressed or waived + B9 passes.
10. **B9 Independent verification** — separate checker pass: no hallucinated APIs, every requirement has a passing test, Part A ↔ B3 traceability intact.

Emit Markdown only. May be appended below the human PRD (separated by a horizontal rule) so the pair travels together.

## Integration & degradation
- **Inputs from upstream:** the human PRD (required); a `business-context` / knowledge-graph **constitution** so the agent inherits engineering constraints; connected codebase/design system/prototype as grounding.
- **Outputs downstream:** the spec is handed to the coding agent; `user-stories` can **inherit** its EARS requirements, acceptance tests, and dependency-ordered tasks rather than re-deriving them; `[ESCALATE]` items become the only human/agent decisions raised.
- **Degrades to:** PRD-only — full spec from the PRD alone, every optional-artifact gap labeled.

## Quality checklist (the bar)
- [ ] **B0 derivation header present** — names the source Part A (title + author); nothing in Part B lacks a Part A anchor.
- [ ] Every Part A requirement maps to ≥1 EARS line that **traces to its Part A ID** and **binds to an artifact or is labeled**.
- [ ] **Tags inherited:** every `Edge case` (or legacy `[edge case]`) requirement has an edge EARS line + test; every `Failure` (`[failure]`) one has a failure EARS line + test.
- [ ] Contracts **bind verbatim**; nothing invented; unknowns split `[ASSUMPTION → T0]` vs `[ESCALATE]`; every PRD Open Question is placed in one bucket.
- [ ] Stakes gate set (one human checkpoint for irreversible work, naming what it gates).
- [ ] Cross-cutting checklist addressed or explicitly waived; out-of-scope / not-constrained stated.
- [ ] Tasks dependency-ordered with a research-first T0 that resolves the `[ASSUMPTION → T0]` set.
- [ ] **Acceptance tests + one Definition of Done present** — a Given/When/Then per requirement incl. edge/failure branches, derived before implementation.
- [ ] **Independent verification run by a checker separate from the generator** (not self-grading); result reports the open `[ESCALATE]` items as the only required human input.

## Known gaps / limitations
- Only as good as the PRD — a wrong requirement yields a precise-but-wrong spec; the checks catch fabrication and gaps, not bad intent (pair with `prd-critique` / discovery upstream).
- Verbatim binding catches invented facts, not facts faithfully drawn from a wrong source — source quality still matters.
- EARS removes ambiguity in stated behavior, not in unstated assumptions — hence the explicit "not constrained" section.
- `[ASSUMPTION → T0]` assumes the codebase answers the question; genuinely undecidable items must be `[ESCALATE]`.
- Acceptance tests are a real check only where executable; otherwise they're criteria, weaker than running code — and the DoD is only as strong as those tests plus the gates it references.

## Worked example (abridged — fed the Split PRD)
**Input:** the Split human PRD (PRD-only; no codebase, no design system). Requirements R1–R10, with R2/R10 tagged `[failure]` and R6/R8 tagged `[edge case]`.
**Output (abridged):**
- **Grounding:** PRD only → all contracts `[ASSUMPTION → T0]`.
- **Stakes:** moves money → **one human checkpoint** gating the fund-flow model + non-user invitation; the rest autonomous.
- **EARS (tags inherited):** R2→ `IF OCR confidence < threshold THEN THE SYSTEM SHALL offer manual entry and SHALL NOT block the split` `[failure ✓]`; R6→ `IF Σ(person shares) ≠ receipt total THEN THE SYSTEM SHALL block send and surface a reconciliation error` `[edge ✓]`; R7→ `WHEN the payer sends THE SYSTEM SHALL create exactly one request per person, idempotently`.
- **Contracts:** OCR + P2P request API + status delivery — all `[ASSUMPTION → T0]`. Money in integer minor units `[ASSUMPTION]`.
- **Unresolved:** `[ASSUMPTION → T0]` OCR provider, idempotency-key mechanism; `[ESCALATE]` fund-flow model, non-user invitation, rounding-remainder owner (from the PRD's Open Questions).
- **Cross-cutting:** idempotency keyed per (split, person) names the double-charge risk; observability logs time-to-create + per-person dispatch (feeds the PRD's guardrails).
- **Tasks:** T0 research/clear-gate → T1 data model → T2 OCR `[P]` → … → T7 dispatch (idempotent) → T9 status.
- **Acceptance & Done:** Given/When/Then per requirement incl. the R2/R6/R10 branches; **Done = all tests pass + the three `[ESCALATE]` decisions closed + cross-cutting addressed + verifier passes.**
- **Independent verification:** all 10 PRD requirements trace to EARS ✓; tags inherited (2 failure, 2 edge) ✓; 0 invented contracts (all labeled) ✓; 3 `[ESCALATE]` flagged ✓ — **PASS**, with those three as the only required human input.

## Contents
- `SKILL.md` — this method (B0–B9 output spec, derivation rule, tag inheritance, verification bar).
- `templates/implementation-spec-template.md` — the B0–B9 skeleton incl. the B0 derivation header and merged B8 tests + DoD.
- `examples/01-perch.md` · `examples/02-tandem.md` · `examples/03-copperline.md` — Part B specs derived from the matching `prd-author` Part A examples (same three cases): B0 names the source Part A, B3 traced to Part A IDs, copperline carries three `[ESCALATE]` items A→B.
