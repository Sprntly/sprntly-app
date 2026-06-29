# [Surface] — [What we're shipping]

[One sentence stating who this is for and what changes — e.g. "For US iPhone screen-repair claimants, move deductible disclosure to step 1 of the claim funnel."]

**Author:** [Name]  ·  **Status:** [Draft | In Review | Approved]  ·  **Last updated:** [Date]  ·  **Linked evidence:** [Evidence-Page-ID or "—"]

## 1. Problem & evidence

[The problem — the user, the job they're doing, the pain. No embedded solution. 3–5 sentences.] **Evidence:** [the signals that establish it — metrics, quotes, source_type/provenance. Gaps you cannot ground become `[NEED: …]`, never invented.]

## 2. Goals & metrics

- **Primary:** [the one metric this moves] — baseline [x] → target [y] by [when]. Unknown baseline → `[NEED: …]`.
- **Guardrail(s):** [the metric(s) that must not regress to move the primary] — [bound, e.g. "p95 latency ≤ baseline"].
- **Strategic fit:** [the goal / North-Star input this ladders up to].

## 3. Non-goals

- [What this explicitly does NOT do this cycle. A PRD with no non-goals is under-scoped.]

## 4. Users & scenarios

- **Primary user:** [who] — **Scenario:** [the situation and the job they hit, including the load-bearing states: empty / error / edge]. Concrete, not personas-for-decoration.

## 5. Requirements

One row per verifiable behavior — what the system does, not how. Tag load-bearing branches inline so the Implementation Spec inherits them: `[edge case]`, `[failure]`. Every row links to a signal or carries an explicit `[ASSUMPTION → T0]` / `[ESCALATE]`.

| ID | Requirement | Priority | Signal / Source | Acceptance (Given/When/Then) |
|----|-------------|----------|-----------------|------------------------------|
| R1 | [core happy-path behavior] | [P0–P3] | [signal source, or `[ASSUMPTION → T0]` / `[ESCALATE]`] | Given [state], when [action], then [observable result] |
| R2 | [quantified target or threshold] | [P0–P3] | [signal] | Given [state], when [action], then [result] |
| R3 | [edge case] [behavior at the boundary] | [P0–P3] | [signal] | Given [edge state], when [action], then [degraded-but-explicit result] |
| R4 | [failure] [behavior when a dependency fails] | [P0–P3] | [signal] | Given [failure], when it occurs, then [explicit error + recovery] |

## 6. Risks & riskiest assumption

- **Risks:** [risk → mitigation (instrumentation, rollback trigger, or scope cut)]; [risk → mitigation]. A risk without a mitigation is an unowned threat.
- **Riskiest assumption:** [the one load-bearing belief the whole PRD rests on] — *we believe* [x]; *it fails if* [y]; *the first signal we're wrong* is [z]. **De-risk by:** [the cheapest test].

## 7. Open questions

- [A real unknown phrased as a decision to be made] — **owner:** [name]. Product/external decisions the PRD cannot make are `[ESCALATE]`.

## 8. Rollout & measurement

[Phasing (flag / % / cohort), the read-out plan (how and when the metrics in §2 are read), and the kill criteria / automatic rollback triggers. Detailed schedules and A/B mechanics go to an Appendix.]

## 9. Done-when

[A single testable exit condition for this cycle — observable, not aspirational.]
