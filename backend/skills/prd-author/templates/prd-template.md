# <Feature> — PRD + Implementation Spec

# Part A — Product Requirements Document (human-readable)
*For alignment and decisions. Author/DRI · Status · Date.*

## 1. Problem & evidence
<Problem — user, job, pain. NO embedded solution.> **Evidence:** <data/research; gaps = [ASSUMPTION: …]>

## 2. Goals & success metrics
- **Primary:** <metric> baseline <x> → target <y> by <when>
- **Guardrail(s):** <must-not-regress>
- **Strategic fit:** <goal / North-Star input>

## 3. Non-goals
- <out of scope this release>

## 4. Users & key scenarios
- **Primary user:** <who> · **Scenarios:** <1–3 jobs>

## 5. Requirements / key flows
- <happy path · states: empty/loading/error/edge · prioritized functional reqs>

## 6. Risks & riskiest assumption
- **Pre-mortem:** "If this fails in 6 months, the likely reason is ___."
- **Riskiest assumption:** <…> → **de-risk by:** <cheapest test>

## 7. Open questions · 8. Rollout & measurement · 9. Done when
<questions+owners · rollout (flag/%/cohort) + how/when metrics are read · testable completion>

---

# Part B — Implementation Spec (LLM-readable / agent-executable)
*The Spec-Driven-Development artifact a coding agent implements and tests AGAINST. Generated autonomously; no fabrication. Source: Part A above.*

## B0. Available artifacts & grounding
*PRD required; others optional grounding. Present → grounds the spec; absent → labeled gap, never invented.*
| Artifact | Present? | Grounds |
|---|---|---|
| PRD | ✅ required | requirements, scope |
| Problem evidence | / | requirement validity |
| Design system / Figma | / | UI behavior, components |
| Prototype | / | flows, states |
| Codebase | / | contracts, research gate |

## B1. Stakes & autonomy gate
<Reversible/low-blast-radius → autonomous, no gate. Irreversible/data/billing/security/migration → ONE human checkpoint before implementation.> **This work:** <classification → gate>

## B2. Constitution (constraints every requirement honors)
- <stack / convention / security / architecture / prior decision — or [ASSUMPTION] if absent>

## B3. Requirements (EARS)
| ID | Requirement (EARS: WHEN/WHILE/IF-THEN/WHERE/ubiquitous) | Traces to (Part A) | Bound to (artifact / [ASSUMPTION]) |
|---|---|---|---|
| R1 | WHEN <event> THE SYSTEM SHALL <behavior> | <goal> | <artifact> |
*Each covers happy path, edge, AND failure.*

## B4. Design & contracts
*Facts bind verbatim to a source, else [ASSUMPTION → T0] / [ESCALATE]. No invented APIs.*
Inputs · Outputs · Pre/postconditions · Invariants · Interfaces/API contracts · Data model · State machine · Constraints.

## B5. Out of scope / not constrained / unresolved
- **Not constrained:** <what the spec deliberately leaves open>
- **[ASSUMPTION → T0 research]:** <answerable by reading code>
- **[ESCALATE]:** <product/external decision a research step cannot settle — the only required human/agent input>

## B6. Cross-cutting checklist (address or explicitly waive)
auth/permissions · idempotency · error taxonomy · observability · migration/rollback · performance budget · concurrency.

## B7. Tasks (dependency-ordered)
| # | Task | Depends on | `[P]` | Verifies (Req) |
|---|---|---|---|---|
| T0 | [research] read relevant code/artifacts, produce plan, resolve [ASSUMPTION → T0]; no coding | — | — | — |
| T1 | <task> | T0 | | R1 |

## B8. Acceptance tests & Definition of Done (merged)
*Tests derived from B3 BEFORE implementation — the agent implements to pass tests it did not author. The Definition of Done wraps them with the non-behavioral gates.*

**Acceptance tests (Given/When/Then, one per requirement incl. failure branches):**
| Req | Given | When | Then (expected) |
|---|---|---|---|
| R1 | <state> | <event> | <observable result> |

**Definition of Done — the implementation is done when ALL hold:**
- [ ] Every acceptance test above passes.
- [ ] Every requirement (B3) has ≥1 passing test, including failure branches.
- [ ] No `[ESCALATE]` item (B5) is open.
- [ ] Cross-cutting checklist (B6) is addressed or explicitly waived.
- [ ] Independent verification (B9) passes.

## B9. Independent verification report (checker ≠ generator; NOT self-grading)
- [ ] Every requirement traces to Part A (no orphans).
- [ ] Every fact/contract binds verbatim to a source; nothing invented; unknowns split [ASSUMPTION → T0] vs [ESCALATE].
- [ ] Every requirement has a verifying task + test; failure modes covered.
- [ ] Cross-artifact conflicts surfaced; cross-cutting items addressed/waived.
- **Result:** <PASS + open [ESCALATE] items = the only human input required>
