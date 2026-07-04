# Implementation Spec (for your coding agent) — First-Session Value for Solo Workspaces
## B0. Derivation
Derived ONLY from Part A: "First-Session Value for Solo Workspaces" (02-tandem--part-a.html) · Author: David Mumuni. Every B3 requirement traces to a Part A ID.

## B1. Context for the agent
Tandem's onboarding assumes teams. This adds a solo-first routing path, goal-based templates, and a deferred invite placement. No codebase provided — interfaces are [ASSUMPTION → T0].

## B2. Stakes gate
Medium. Worst failure: R4 data loss when a workspace upgrades to collaborative mode mid-session. That path gets the deepest coverage.

## B3. Requirements (EARS, traced to Part A IDs)
- **B-R1 ← R1:** WHEN a signup completes onboarding with zero invites sent, the system SHALL route them to the solo-first path.
- **B-R2 ← R2:** WHEN a solo user selects a goal, the system SHALL create a workspace pre-populated with the matching template [ASSUMPTION → T0: template storage exists].
- **B-R3 ← R3:** WHEN the solo workspace renders, the system SHALL place the invite CTA in the persistent-quiet slot and SHALL NOT block any first-value action with an invite prompt.
- **B-R4 ← R4:** WHEN a teammate accepts an invite to a solo workspace, the system SHALL upgrade it to collaborative mode with zero data loss.
- **B-R5 ← R5:** IF template fetch fails, THEN the system SHALL land the user in a usable blank workspace and SHALL log the failure — never a dead screen.

## B4. Interface contracts
- [ASSUMPTION → T0] Onboarding emits `signup.completed {userId, invitesSent}`.
- [ASSUMPTION → T0] Workspace model supports a `mode: solo|collaborative` flag with lossless transition.
- New: `template.applied`, `solo_path.entered`, `invite.deferred_shown` analytics events.

## B5. Escalations (carried from Part A)
- [ESCALATE] Solo path pricing/limits messaging: own or inherited? — owner: PM.
- [ESCALATE] Which 3–5 goal templates ship in V1? — owner: PM + Design.

## B6. Cross-cutting checklist
- [ ] Auth on workspace mode transitions · [ ] Privacy: solo content not exposed on upgrade beyond invited members · [ ] Telemetry: events above · [ ] i18n on templates · [ ] Accessibility: invite CTA reachable by keyboard in quiet slot · [ ] Error states: B-R5 · [ ] Performance: template apply < 1.5s p95 [ASSUMPTION → T0].

## B7. Tasks (dependency-ordered; [P] = parallel-safe)
- T0 — Research gate: verify signup events, workspace mode flag, template storage.
- T1 — Solo detection + routing (B-R1).
- T2 [P] — Goal picker + template application (B-R2).
- T3 — Deferred invite placement (B-R3).
- T4 — Solo→collaborative upgrade path (B-R4).
- T5 [P] — Template-failure fallback (B-R5).

## B8. Acceptance tests & Definition of Done (merged)
| Req | Given | When | Then (expected) |
|---|---|---|---|
| B-R1 | Signup with 0 invites | Onboarding completes | User lands on solo-first path |
| B-R2 | Solo user picks "plan an event" | Workspace creates | Matching template content present |
| B-R3 | Solo workspace open | First-value action taken | No blocking invite prompt encountered |
| B-R4 | Solo workspace with content | Teammate accepts invite | Collaborative mode; all content intact |
| B-R5 | Template service down | Solo user picks a goal | Usable blank workspace; failure logged |

**Done when ALL hold:** every test passes · every B3 requirement has ≥1 passing test incl. failure branches · no open [ESCALATE] · cross-cutting addressed or waived · B9 passes.

## B9. Independent verification
Separate checker: no API used that T0 didn't confirm; B-R4 tested with non-empty workspace state; Part A ↔ B3 traceability intact.
