# <Feature> — Product Requirements Document

<One sentence: who this is for and what changes.>

**Author:** <Name> · **Status:** <Draft | In Review | Approved> · **Last updated:** <Date> · **Linked evidence:** <Evidence-Page-ID or "—">

## 1. Problem & evidence
<Problem — the user, the job, the pain. NO embedded solution.> **Evidence:** <signals that establish it — metrics, quotes, source_type; gaps you can't ground = `[NEED: …]`.>

## 2. Goals & metrics
- **Primary:** <metric> baseline <x> → target <y> by <when> (unknown baseline → `[NEED: …]`)
- **Guardrail(s):** <must-not-regress metric> — <bound>
- **Strategic fit:** <goal / North-Star input it ladders up to>

## 3. Non-goals
- <explicitly out of scope this cycle>

## 4. Users & scenarios
- **Primary user:** <who> — **Scenario:** <situation + job, incl. states: empty / error / edge>

## 5. Requirements
One row per verifiable behavior; tag load-bearing branches inline (`[edge case]`, `[failure]`); every row links to a signal or carries `[ASSUMPTION → T0]` / `[ESCALATE]`.

| ID | Requirement | Priority | Signal / Source | Acceptance (Given/When/Then) |
|----|-------------|----------|-----------------|------------------------------|
| R1 | <core behavior> | <P0–P3> | <signal / [ASSUMPTION → T0]> | Given <state>, when <action>, then <result> |

## 6. Risks & riskiest assumption
- **Risks:** <risk → mitigation>; <risk → mitigation>
- **Riskiest assumption:** <the load-bearing belief> — *we believe* <x>; *it fails if* <y>; *first signal* <z>. **De-risk by:** <cheapest test>

## 7. Open questions
- <real unknown> — **owner:** <name> (product/external decisions = `[ESCALATE]`)

## 8. Rollout & measurement
<Phasing (flag / % / cohort), how & when the §2 metrics are read, kill criteria. Detail → appendix.>

## 9. Done-when
<A single testable exit condition for the cycle.>
