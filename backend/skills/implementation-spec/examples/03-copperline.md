# Implementation Spec (for your coding agent) — Parts-Availability Check at Scheduling
## B0. Derivation
Derived ONLY from Part A: "Parts-Availability Check at Scheduling" (03-copperline--part-a.html) · Author: David Mumuni. Every B3 requirement traces to a Part A ID.

## B1. Context for the agent
Copperline is a field service platform (dispatch, scheduling, invoicing). This adds a parts-availability check inside the existing scheduling flow. No codebase provided — interfaces are [ASSUMPTION → T0].

## B2. Stakes gate
Medium-high. A false "parts available" is worse than no check. Verification depth: full failure-branch coverage; staleness handling tested explicitly.

## B3. Requirements (EARS, traced to Part A IDs)
- **B-R1 ← R1:** WHEN a dispatcher adds a job with ≥1 line-item part, the system SHALL query connected inventory sources and display per-part availability (truck / warehouse / unavailable) before schedule confirmation.
- **B-R2 ← R2:** WHEN any required part is unavailable, the system SHALL flag the job "at risk — parts" on the dispatch board and SHALL suggest the earliest all-parts date [ASSUMPTION → T0: supplier lead-time data exists].
- **B-R3 ← R3:** IF an inventory source is unreachable or returns data older than the staleness threshold, THEN the system SHALL display "availability unknown" for affected parts and SHALL NOT display an available state.
- **B-R4 ← R4:** WHEN a job has no parts list, the system SHALL schedule normally with no check and no flag.
- **B-R5 ← R5:** WHEN availability is displayed, the system SHALL log the check result and the dispatcher's decision to the events pipeline.

## B4. Interface contracts
- [ASSUMPTION → T0] `GET /api/v1/inventory/availability?partIds=…&truckId=…` → `{partId, status: "truck"|"warehouse"|"unavailable"|"unknown", asOf}` — verify first-party inventory vs third-party connectors.
- [ASSUMPTION → T0] Job schema includes structured `parts[]` line items (if free text today, T0 scopes normalization or cuts to [ESCALATE]).
- [ASSUMPTION → T0] Dispatch board supports job-level badges via existing `jobFlags`.
- New event: `parts_check.completed {jobId, results[], dispatcherAction, latencyMs}`.

## B5. Escalations (carried from Part A)
- [ESCALATE] Staleness threshold (proposed 4h) — owner: PM.
- [ESCALATE] "Schedule anyway" confirmation step vs one click — owner: PM + Design.
- [ESCALATE] V1 source scope: truck + warehouse only, or supplier feeds — owner: PM.

## B6. Cross-cutting checklist
- [ ] Auth: availability scoped to tenant · [ ] Privacy: no supplier pricing to technician role · [ ] Telemetry: B-R5 + latency · [ ] i18n: status labels externalized · [ ] Accessibility: flag not color-only · [ ] Error states: all failure paths render "unknown," never "available" · [ ] Performance: check < 2s p95 or degrades to async badge [ASSUMPTION → T0].

## B7. Tasks (dependency-ordered; [P] = parallel-safe)
- T0 — Research gate: verify all [ASSUMPTION → T0] items.
- T1 — Availability adapter layer.
- T2 [P] — Structured parts array (if T0 confirms gap).
- T3 — Scheduling-flow UI states + confirm step per [ESCALATE] resolution.
- T4 [P] — Dispatch-board "at risk — parts" badge.
- T5 — Earliest-available-date suggestion (only if lead-time data confirmed).
- T6 [P] — Telemetry (B-R5).
- T7 — Failure/staleness handling (B-R3).

## B8. Acceptance tests & Definition of Done (merged)
| Req | Given | When | Then (expected) |
|---|---|---|---|
| B-R1 | Job with 3 parts, all truck stock | Schedule confirmation opens | All 3 show "truck" before confirm enabled |
| B-R2 | 1 part unavailable | Job scheduled | "At risk — parts" flag; earliest date if lead-time data exists |
| B-R3 | Source timing out | Confirmation opens | Affected parts "unknown"; no available state |
| B-R3b | Data older than threshold | Availability displayed | "Unknown" shown; staleness reason logged |
| B-R4 | Job with no parts | Dispatcher schedules | No check, no flag, flow unchanged |
| B-R5 | Any completed check | Dispatcher acts | parts_check.completed captured with action |

**Done when ALL hold:** every test passes · every B3 requirement has ≥1 passing test incl. failure branches · no open [ESCALATE] · cross-cutting addressed or waived · B9 passes.

## B9. Independent verification
Separate checker: no API T0 didn't verify; no available state reachable from stale/failed paths; Part A ↔ B3 traceability intact.
