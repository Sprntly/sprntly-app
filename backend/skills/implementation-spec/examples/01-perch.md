# Implementation Spec (for your coding agent) — Automatic Invoice Follow-Up
## B0. Derivation
Derived ONLY from Part A: "Automatic Invoice Follow-Up" (01-perch--part-a.html) · Author: David Mumuni. Every B3 requirement traces to a Part A ID.

## B1. Context for the agent
Perch has invoice send + payment-recording flows. This adds a reminder engine downstream of "sent." No codebase provided — interfaces are [ASSUMPTION → T0].

## B2. Stakes gate
Medium. Worst failure: reminding a client who already paid (relationship damage). Payment-halt path (B-R3) gets the deepest test coverage.

## B3. Requirements (EARS, traced to Part A IDs)
- **B-R1 ← R1:** WHEN an invoice passes due+N days (N ∈ configured schedule) and remains unpaid, the system SHALL send the reminder for that step.
- **B-R2 ← R2:** WHEN a user completes reminder setup, the system SHALL store one tone selection and apply it to all reminder sends.
- **B-R3 ← R3:** WHEN any payment is recorded against an invoice, the system SHALL cancel all pending reminders for it within 60 seconds [ASSUMPTION → T0: payment events available].
- **B-R4 ← R4:** IF a reminder bounces or the recipient opts out, THEN the system SHALL stop the sequence and SHALL flag the invoice "manual follow-up."
- **B-R5 ← R5:** WHEN the invoice list renders, the system SHALL display reminder status per invoice (scheduled / sent / stopped / manual).

## B4. Interface contracts
- [ASSUMPTION → T0] Event bus emits `payment.recorded {invoiceId, amount, ts}`.
- [ASSUMPTION → T0] Email service exposes bounce + unsubscribe webhooks.
- New: `reminder_schedule {invoiceId, steps[], tone, status}`; `reminder.sent` / `reminder.stopped` events.

## B5. Escalations (carried from Part A)
- [ESCALATE] Default-on vs opt-in per invoice — owner: PM.
- [ESCALATE] "Firm" tone late-fee language — owner: PM + Legal.

## B6. Cross-cutting checklist
- [ ] Auth/tenancy on reminder data · [ ] Privacy: client emails not exposed cross-tenant · [ ] Telemetry: send/stop/bounce events · [ ] i18n on templates · [ ] Accessibility of status indicators · [ ] Error states: send failure retries then flags · [ ] Performance: schedule scan is a background job.

## B7. Tasks (dependency-ordered; [P] = parallel-safe)
- T0 — Research gate: verify payment events, email webhooks, invoice schema.
- T1 — Reminder schedule model + scheduler job.
- T2 [P] — Tone templates + setup flow.
- T3 — Payment-halt listener (B-R3).
- T4 [P] — Bounce/opt-out handling (B-R4).
- T5 — Invoice-list status column (B-R5).

## B8. Acceptance tests & Definition of Done (merged)
| Req | Given | When | Then (expected) |
|---|---|---|---|
| B-R1 | Unpaid invoice at due+3 | Scheduler runs | Step-1 reminder sent in stored tone |
| B-R2 | User selected "friendly" | Any reminder sends | Friendly template used |
| B-R3 | Pending reminders exist | payment.recorded fires | All pending reminders cancelled ≤60s |
| B-R4 | Reminder bounces | Webhook received | Sequence stopped; invoice flagged "manual follow-up" |
| B-R5 | Mixed invoice states | List renders | Each invoice shows correct reminder status |

**Done when ALL hold:** every test passes · every B3 requirement has ≥1 passing test incl. failure branches · no open [ESCALATE] · cross-cutting addressed or waived · B9 passes.

## B9. Independent verification
Separate checker: no API used that T0 didn't confirm; B-R3 cancellation tested with partial payment; Part A ↔ B3 traceability intact.
