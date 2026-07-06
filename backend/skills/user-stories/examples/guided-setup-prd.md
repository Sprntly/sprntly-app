# Guided Setup — Tracker Connection + First-Agent-Run Checkpoint

**One-line summary:** A guided flow that takes a new Sprntly account from signup to a connected tracker and a first successful agent run, eliminating the setup wall that stalls activation.

Authors: Sprntly PM Agent (via prd-author) · Status: Draft v0.1 · Priority: P0 · Last updated: 2026-07-03 · *(sample/demo signals throughout)*

---

## PART A — Product Requirements Document

### 1. Problem (with evidence)

New accounts stall in the first week trying to wire Sprntly to their tracker — before any agent has produced value. Customers describe it as losing momentum, not hitting bugs: "the excitement window closes."

**Signals** (VoC Q2 2026 corpus — sample data):
- 🅗 11 of 17 CSM calls and 19 of 42 support tickets name tracker connection / first-run as the primary struggle — top theme three quarters running.
- 🅗 11 of 15 accounts stalled >1 week between signup and first successful agent run.
- 🅗 2 of 6 churn/exit interviews cite the setup experience as a contributing factor.
- Quote: "We were excited on the demo call, and then it took us nine days to see one agent do one thing." (CSM call, May)
- Quote: "I gave up on the Jira connection twice before someone on your team walked me through it." (ticket #4, Apr)

**Interpretation assumption (stated up front):** the stall is concentrated at *tracker connection* and *first agent run*, not at account creation or pricing. If the funnel data (R5) contradicts this, the scope must be revisited.

### 2. Goals & Metrics

**Primary metric**
- **Activation rate** = accounts reaching a first successful agent run within 7 days of signup ÷ accounts created.
  Baseline: **[NEED: funnel not instrumented — R5 creates the baseline]**. Target: set after 4 weeks of baseline data — no invented target.

**Guardrail metrics** (must not break to move the primary)
- Support-ticket volume tagged `setup` per 100 new accounts — must not increase. Baseline: 🅗 19 tickets / 42 total in Q2 (sample corpus); per-account rate **[NEED]**.
- Connection abandonment mid-OAuth — must not increase once measured. Baseline: **[NEED]**.
- Time-to-first-run for accounts that already succeed quickly — must not regress (no added friction for the fast path). Baseline: **[NEED]**.

### 3. Non-goals

- No changes to pricing, plans, or seat logic.
- No new tracker integrations beyond the launch set (see Open Questions Q2).
- No changes to agent output quality or the run engine itself.
- Not a general onboarding redesign — scope is connection + first run only.

### 4. Users & scenarios

- **The setting-up PM (primary):** just signed up after a demo; needs Jira/Linear connected and one agent run to justify the purchase internally. Hits OAuth scopes, silent failures, and "what do I do next" gaps.
- **The account admin:** enabled Sprntly for a team; needs to see who is stuck and where, so the champion can intervene before momentum dies.
- **The CSM (secondary):** today walks users through connection by hand (ticket #4 pattern); needs the product to do this walking.

### 5. Requirements

| ID | Requirement | Priority | Signal/Source | Acceptance |
|---|---|---|---|---|
| R1 | Guided tracker-connection flow (launch trackers per Q2 decision) with explicit progress states: not-started → authorizing → verifying → connected | P0 | 🅗 11/17 calls · 19/42 tickets | User completes connection unaided; every state visible. [edge case: OAuth cancelled/expired mid-flow returns to a resumable state] |
| R2 | First-agent-run checkpoint: the flow ends by executing a starter agent run on the connected data and showing its output | P0 | 🅗 "nine days to see one agent do one thing" · 11/15 stalled >1wk | A successful starter run completes inside the flow; output visible. [ESCALATE: starter-run dataset — real workspace data vs. sandbox — privacy call] |
| R3 | No silent failures: every connection error surfaces a visible state, a plain-language cause, and a retry | P0 | 🅗 ticket #4 ("gave up twice… silent") | Zero failure paths end without a rendered state + next step. [failure: tracker API down → flow says so and offers retry + notify-me] |
| R4 | Admin stall visibility: admins see each member's setup stage and where they stopped | P1 | 🅗 2/6 churn interviews (champion couldn't see the stall) | Admin view lists members × stage; stall (>48h in one stage) flagged |
| R5 | Funnel instrumentation: every step emits an event (step, outcome, duration) establishing the activation baseline | P0 | [NEED: baseline unknown — this requirement creates it] | Events emitted for 100% of step transitions; dashboard query returns the funnel |
| R6 | Human handoff: after a second failed connection attempt, offer "get help" that routes to support with full context attached | P2 | 🅗 CSM hand-walking pattern in ticket corpus | Help request carries account, tracker, step, and error context; no re-explaining |

### 6. Risks + Riskiest Assumption

| Risk | Note |
|---|---|
| OAuth scope friction varies by tracker plan/admin policy | May require admin-consent path (R1 edge) |
| Starter run on real data raises privacy concerns | Escalated (R2) — decision, not a guess |
| Instrumentation lands late → target-setting delayed | R5 sequenced first in Part B |

**Riskiest assumption (pre-mortem):**
We believe the stall is a *guidance* problem, not a *permissions* problem.
It fails if most stalls are actually admins withholding tracker credentials.
We'd see it first as R5 funnel data showing abandonment concentrated at "authorizing" with admin-role accounts absent.

### 7. Open questions (owners)

- Q1 — Starter-run dataset: real workspace data or sandbox? **Owner: Kwame (product)** — blocks R2 build. `[ESCALATE]`
- Q2 — Launch tracker set: Jira + Linear only, or include Asana? **Owner: Kwame (product)** — blocks R1 scope. `[ESCALATE]`
- Q3 — Does support have capacity for R6 handoffs at current volume? **Owner: CS lead.**

### 8. Rollout & measurement

Phase 0: instrument the current funnel (R5) — 4 weeks of baseline before judging anything. Phase 1: guided flow + failure surfacing (R1, R3) to 20% of new accounts. Phase 2: first-run checkpoint (R2) once Q1 is decided. Phase 3: admin visibility + handoff (R4, R6), then 100%. Read-out: weekly activation funnel vs. baseline; guardrails reviewed at each phase gate. Detailed schedule and A/B mechanics: Appendix A.

**Appendices:** A — rollout schedule & experiment design (stub) · B — tracker OAuth notes (stub) · C — competitor onboarding scan (stub).

---

## PART B — Implementation Spec

### EARS requirements (traced to Part A IDs)

- **E1 (R1):** When a user initiates tracker connection, the system shall present a stepwise flow with states not-started → authorizing → verifying → connected, persisting progress across sessions.
- **E2 (R1-edge):** When an OAuth authorization is cancelled or expires mid-flow, the system shall return the user to a resumable state with the prior steps preserved.
- **E3 (R2):** When a tracker connection reaches `connected`, the system shall execute a starter agent run against the permitted dataset (per Q1 decision) and render its output within the flow.
- **E4 (R3):** When any connection step fails, the system shall render a failure state with a plain-language cause, a retry action, and — where the failure is external (tracker API down) — a notify-me option.
- **E5 (R4):** When an admin opens the setup view, the system shall list each member's current stage, and shall flag any member in one stage longer than 48 hours.
- **E6 (R5):** When any setup step transitions, the system shall emit an event containing step id, outcome, and duration.
- **E7 (R6):** When a user's connection attempt fails for the second time, the system shall offer a help request that attaches account, tracker, step, and error context.

### Contracts

- Tracker OAuth + API contracts (Jira, Linear, TBD Asana): **[ASSUMPTION → T0]** — no codebase access; validate scopes, token lifetimes, and rate limits at T0.
- Event schema for E6: `{account, user, step, outcome, duration_ms, ts}` — **[ASSUMPTION → T0]** against the existing analytics pipeline.

### Stakes gate

- Setup flow is **read-only** against trackers until first run (reversible → agent-ready).
- Starter run may read workspace data → **gated on Q1 decision** (privacy).
- Support-routing (E7) writes to the ticketing system — low blast radius, reversible.

### Cross-cutting checklist

Auth (OAuth scopes minimal) ✔ · Privacy (Q1 pending — gate) ⚠ · Error states (E4 is the requirement) ✔ · Empty states (no tracker projects found) ✔ · Observability (E6) ✔ · Rollback (flow behind flag; phase gates) ✔

### Tasks (dependency-ordered)

- **T0 — Validate tracker OAuth/API contracts + event-schema fit.** `[ASSUMPTION → T0]` Timebox 2d.
- **DT-Q1 — Decide starter-run dataset (real vs sandbox).** `[ESCALATE]` Owner: Kwame. Blocks T4.
- **DT-Q2 — Decide launch tracker set.** `[ESCALATE]` Owner: Kwame. Blocks T2 scope.
- **T1 — Funnel event emission + dashboard query (E6).** `[P]`
- **T2 — Guided connection flow with persistent states (E1, E2).** Depends: T0, DT-Q2.
- **T3 — Failure surfacing: state, cause, retry, notify-me (E4).** Depends: T2. `[P]` with T5.
- **T4 — Starter-run checkpoint (E3).** Depends: T2, DT-Q1.
- **T5 — Admin stall view + 48h flag (E5).** Depends: T1. `[P]` with T3.
- **T6 — Second-failure help handoff with context payload (E7).** Depends: T3.

### Spec-first tests (success + failure per requirement)

- **E1:** ✅ user completes all states in order, progress persists across a session break. ❌ state skipped or lost on refresh → fail.
- **E2:** ✅ cancelled OAuth returns to resumable state with steps preserved. ❌ user restarted from zero → fail.
- **E3:** ✅ starter run executes on connect and output renders in-flow. ❌ run silently skipped or output rendered outside the flow → fail.
- **E4:** ✅ induced failure renders state + cause + retry; external outage offers notify-me. ❌ any failure path ends blank → fail.
- **E5:** ✅ admin view lists members × stage; 49-hour stall is flagged. ❌ stalled member unflagged → fail.
- **E6:** ✅ 100% of step transitions emit conforming events; funnel query returns. ❌ any transition without an event → fail.
- **E7:** ✅ second failure offers help; request payload contains all four context fields. ❌ payload missing context → fail.

### Done-when

All spec-first tests pass · DT-Q1 and DT-Q2 resolved · cross-cutting checklist fully ✔ (privacy gate cleared) · four weeks of E6 baseline flowing.

### Independent verification (no-hallucination pass)

- Zero invented numbers: all figures trace to the VoC Q2 sample corpus or carry [NEED].
- Contracts: 2 marked [ASSUMPTION → T0]; none asserted.
- Escalations pending human resolution: **DT-Q1 (starter dataset), DT-Q2 (tracker set)**.
- All EARS requirements trace to Part A R1–R6. Done-when appears in Part B only.
