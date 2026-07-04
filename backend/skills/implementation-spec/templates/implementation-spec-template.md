# Implementation Spec (for your coding agent) — {{title}}
## B0. Derivation
Derived ONLY from Part A: "{{Part A title}}" · Author: {{Part A byline}}. Every B3 requirement traces to a Part A ID; nothing here lacks a Part A anchor.

## B1. Context for the agent
{{what exists, what's being added, constraints}}

## B2. Stakes gate
{{what happens if built wrong → verification depth}}

## B3. Requirements (EARS, traced to Part A IDs)
- **B-R1 ← R1:** WHEN {{trigger}}, the system SHALL {{response}}. IF {{failure}}, THEN the system SHALL {{fallback}}.

## B4. Interface contracts
{{APIs / schemas / events; no codebase → label [ASSUMPTION → T0]}}

## B5. Escalations (carried from Part A)
- [ESCALATE] {{decision}} — owner: {{who}}

## B6. Cross-cutting checklist
- [ ] Auth · [ ] Privacy · [ ] Telemetry · [ ] i18n · [ ] Accessibility · [ ] Error states · [ ] Performance budget

## B7. Tasks (dependency-ordered; [P] = parallel-safe)
- T0 — Research gate: verify all [ASSUMPTION → T0] items before implementation.
- T1 … Tn

## B8. Acceptance tests & Definition of Done (merged)
*Tests derived from B3 BEFORE implementation — the agent implements to pass tests it did not author.*
| Req | Given | When | Then (expected) |
|---|---|---|---|
| B-R1 | {{state}} | {{event}} | {{observable result}} |

**Done when ALL hold:** every test passes · every B3 requirement has ≥1 passing test incl. failure branches · no open [ESCALATE] · cross-cutting addressed or waived · B9 passes.

## B9. Independent verification
{{separate checker: no hallucinated APIs · every requirement has a passing test · Part A ↔ B3 traceability intact}}
