---
name: prd-author
description: >
  Author a Product Requirements Document from a problem, a set of signals, and
  (optionally) a codebase or prior spec. Produces a two-part artifact: Part A, a
  lean human-readable PRD, and Part B, a machine-readable Implementation Spec an
  engineering agent can execute. Use when the user says "write a PRD", "draft a
  spec for this feature", "turn this brief into a PRD", or hands you an evidence
  brief / problem statement and asks for a definition document. Do NOT use to
  critique an existing PRD (that's prd-critique), to produce the engineering
  ticket breakdown alone (user-stories), or to write the deep technical design
  (tech-spec).
triggers:
  - "write a PRD" / "draft a PRD" / "author a PRD"
  - "spec out this feature" / "turn this brief into a PRD"
  - "I have a problem + signals, define the product"
when_not_to_use:
  - Reviewing or grading an existing PRD            -> prd-critique
  - Breaking a PRD into agent/human tickets         -> user-stories
  - Deep technical/architecture design              -> tech-spec
  - Pure prioritization of multiple initiatives     -> prioritize
inputs:
  required:
    - problem: a problem statement, brief, or evidence brief
  optional:
    - signals: quantitative + qualitative evidence (links, metrics, quotes)
    - codebase: repo access or file tree (enables real contracts instead of assumptions)
    - prior_spec: an existing Part B to inherit acceptance criteria from
  degradation:
    - No signals       -> write the PRD, flag every metric/baseline with [NEED: …]
    - No codebase      -> label all interface/data contracts [ASSUMPTION → T0]
    - No prior_spec    -> generate Part B fresh; mark unresolved product calls [ESCALATE]
outputs:
  - Part A: human PRD (lean body, operational detail in appendices)
  - Part B: Implementation Spec (EARS requirements, dependency-ordered tasks, spec-first tests, verification)
guardrails:
  - NO INVENTED NUMBERS. If a baseline, target, or rate isn't in the signals, write [NEED: …]. Never fabricate.
  - SIGNAL-LINKING IS NON-NEGOTIABLE. Every requirement and metric traces to a signal, a decision, or an explicit [ASSUMPTION]/[ESCALATE]. No orphan claims.
  - NAME THE RISKIEST ASSUMPTION. Exactly one, with a three-line pre-mortem. Don't bury it in a risk table.
  - PROTECT INTENT, RICHNESS, AND VOICE. Lean ≠ generic. Preserve the author's domain insight; cut bloat, not substance.
  - REQUIREMENTS GO IN A TABLE. Always. (ID | Requirement | Priority | Signal/Source | Acceptance.)
  - DON'T GUESS PRODUCT DECISIONS. Mark them [ESCALATE] and route to a human owner.
---

---

# prd-author

Author a PRD that a human can approve and an agent can execute. The output is
**two parts**: a lean human PRD (Part A) and a machine-readable Implementation
Spec (Part B). Part A is for the reader; Part B is a schema that enforces
traceability and completeness — its structure matters for that reason, not for
looks.

## Operating modes

**Prose mode (default).** You have a problem and some signals, no prior spec.
Write Part A in full, then generate Part B fresh.

**Spec-aware mode.** A prior Part B (or tech-spec) exists. *Inherit* acceptance
criteria from it rather than re-deriving them — trace each requirement back to
its prior task/requirement ID. Add `[ESCALATE]` decision tickets for anything
the prior spec left unresolved.

## Method

1. **Restate the problem with evidence.** One or two sentences, then the signals
   that establish it. If a signal type is missing, say so — don't pad. Surface
   the single interpretation assumption you're making about scope up front, so it
   can be corrected before everything downstream inherits it.
2. **Set goals and metrics.** Split a **primary metric** from **guardrail
   metrics** (the things you must not break to move the primary). Give each a
   formula and an explicit baseline. Unknown baseline → `[NEED: …]`. Do not
   collapse primary and guardrails into one list.
3. **Declare non-goals.** What this explicitly does not do this cycle. A PRD with
   no non-goals is under-scoped.
4. **Users & scenarios.** Who, in what situation, hitting what. Concrete, not
   personas-for-decoration.
5. **Requirements — in a table.** `ID | Requirement | Priority | Signal/Source |
   Acceptance`. Every row links to a signal or carries an explicit
   `[ASSUMPTION → T0]` / `[ESCALATE]`. This is the spine; keep the prose around
   it thin.
6. **Risks + the riskiest assumption.** A short risk table, then **one** named
   riskiest assumption with a three-line pre-mortem (what we believe / how it
   fails / what we'd see first if it's wrong).
7. **Open questions.** Real unknowns with owners, not rhetorical ones.
8. **Rollout & measurement.** Phasing, the read-out plan, and how you'll know it
   worked. Detailed mechanics go to an appendix.
9. **Done-when.** The crisp exit condition for the cycle.

Keep the body lean. A/B mechanics, tech-stack notes, competitor scans, and
detailed rollout schedules move to **appendices** — present but out of the
critical reading path.

## Output spec

```
PART A — <Feature> PRD
  One-line summary
  Authors · Status · Priority · Last updated [NEED if absent]
  1. Problem (with evidence)
  2. Goals & Metrics  (primary | guardrails, with formulas + baselines)
  3. Non-goals
  4. Users & scenarios
  5. Requirements  (TABLE: ID | Requirement | Priority | Signal/Source | Acceptance)
  6. Risks + Riskiest Assumption (3-line pre-mortem)
  7. Open questions (owners)
  8. Rollout & measurement
  9. Done-when
  Appendices: A/B plan, tech notes, competitor scan, full rollout, risk detail
```

## Quality bar (self-check before returning)

- [ ] Every metric has a formula and a baseline (or a `[NEED: …]` flag) — zero invented numbers.
- [ ] Every requirement traces to a signal, decision, or explicit assumption/escalation.
- [ ] Primary metric is separated from guardrails.
- [ ] Non-goals are present.
- [ ] Exactly one riskiest assumption is named with a pre-mortem.
- [ ] Requirements are in a table.
- [ ] Body is lean; operational detail is in appendices.
- [ ] Part B EARS requirements all trace to Part A IDs.
- [ ] Every product decision the spec couldn't make is `[ESCALATE]`, not guessed.
- [ ] Author's intent, domain richness, and voice are preserved.

## Worked example (abridged)

**Input:** a brief for emoji support in the Facebook Boost Post composer, no
metric baselines available, no codebase access.

**Output:** Part A in the 9-section structure — interpretation assumption stated
up front ("emoji *inside* the composer's creative fields", not a new emoji ad
unit); all metric baselines flagged `[NEED: …]`; riskiest assumption =
cross-placement emoji rendering consistency, with a three-line pre-mortem;
requirements in a table. Part B — EARS requirements traced to Part A IDs, all
interface contracts `[ASSUMPTION → T0]` (no codebase), four product decisions
`[ESCALATE]`, spec-first tests covering success and failure branches, and a
verification section confirming zero invented contracts.
