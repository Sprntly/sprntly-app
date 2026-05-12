# [Surface] — [What we're shipping]

Replace bracket text. Title under 12 words. Format: [Surface] — [What we're shipping].

Author: [Name] | Status: [Draft / In Review / Approved] | Target ship: [Date]

────────────────────────────────────────────────────────────

## TL;DR

Sentence 1: the problem with the key number. Sentence 2: the proposed fix. Sentence 3: the projected impact — concrete numbers only. No adjectives. A senior reading only this should know whether to read the rest.

[Sentence 1 — problem + key number.] [Sentence 2 — proposed fix.] [Sentence 3 — projected impact in concrete numbers.]

────────────────────────────────────────────────────────────

## 1. Context

[Paragraph 1 — the relevant product surface, customer segment, what is true today. 3–5 sentences max. Do not explain the problem yet — that is Section 2.]

[Paragraph 2 — what changed recently or why this is timely. Optional. Cut if not strictly needed.]

────────────────────────────────────────────────────────────

## 2. Problem

### User problem

[A [user persona] is trying to [goal]. They [step-by-step what happens]. They run into [friction] which causes [pain]. As a result, [behavioral consequence].]

### Business impact

| Dimension | Impact |
| --- | --- |
| Affected user volume | [# users / sessions / month] |
| Cost per affected user | [$ / churn pp / NPS pts] |
| Annualized business cost | [$X / yr] |
| Trajectory | [Growing / stable / shrinking — one sentence why] |

────────────────────────────────────────────────────────────

## 3. Hypothesis

If we [proposed change], then [primary metric will move from X to Y], because [the underlying mechanism from the problem above]. [Optional secondary benefit.]

────────────────────────────────────────────────────────────

## 4. Solution Requirements

[One sentence at the highest level — what is being inserted into which flow, and whether user-facing UX changes.]

| Requirement | Category | Detail |
| --- | --- | --- |
| [Behavior 1] | Functional | [Core happy-path action — what the system does] |
| [Behavior 2] | Functional | [Quantified target or threshold] |
| [Behavior 3] | Functional | [Algorithmic detail or fallback] |
| [Behavior 4] | Functional | [Edge case — skip / no-op condition] |
| [Behavior 5] | Functional | [Error handling — replace silent failure with explicit state] |
| [flag_name_enabled] | Feature flag | [boolean, default: false, safe range: on/off] |
| [config_threshold] | Remote config | [numeric, default: X, range: A–B, updated by: team] |
| [event_started] | Telemetry | [fields: user_id, device, os, context_field_1, context_field_2] |
| [event_completed] | Telemetry | [fields: user_id, output_field, duration_ms, result_field] |
| [event_failed] | Telemetry | [fields: user_id, device, os, error_code] |

────────────────────────────────────────────────────────────

## 5. Acceptance Criteria

| # | Given / When / Then | Verified by |
| --- | --- | --- |
| AC1 | Happy path — Given [target user], when [action], then [primary behavior] | Integration test |
| AC2 | Performance — Given any supported device, when [action] runs, then completes in <Xms at P95 | Perf test in CI |
| AC3 | Error handling — Given a failure, when it occurs, then user sees explicit error + retry | QA simulated failure |
| AC4 | Feature flag off — Given flag=false, when user reaches [surface], then legacy behavior renders | QA flag toggled |
| AC5 | Edge case — [offline / low memory / very large input] behaves as specified | Scenario test |

────────────────────────────────────────────────────────────

## 6. Metrics

| Category | Metric | Current | Target |
| --- | --- | --- | --- |
| Primary | [the one metric the hypothesis moves] | [X%] | [Y%] |
| Secondary | [leading indicator 1] | [X%] | [Y%] |
| Secondary | [leading indicator 2] | [X] | [Y] |
| Guardrail | [must-not-degrade metric] | [baseline] | [within Xpp] |
| Guardrail | [reliability or performance bound] | [baseline] | [≤ baseline] |

────────────────────────────────────────────────────────────

## 7. Definition of Done

Ready to merge when ALL of the following are true:

- All acceptance criteria pass in CI
- Implementation lives in [specific file / module]
- Feature flags wired through remote-config service; readable at decision time
- All telemetry events emit with schema specified in Section 4
- P95 latency verified in CI performance test
- Unit tests cover new logic paths including edge cases
- Integration test against staging endpoint passes
- PR description links to this PRD and the ticket number

────────────────────────────────────────────────────────────

## 8. Test Plan

| Phase | Detail |
| --- | --- |
| Pre-launch | [Internal dogfood — duration, audience, exit criterion]<br>[Closed beta — duration, sample size, exit criterion] |
| Rollout | [A/B design — 50/50, sample size, MDE, duration]<br>[Schedule — 1% → 10% → 50% → 100% over N days]<br>[Kill criteria — automatic rollback triggers] |
| Post-launch | [Monitoring — dashboard owner + review cadence]<br>[30-day retro — always include]<br>[90-day check — for metrics that lag] |

────────────────────────────────────────────────────────────

## How to use this template

Delete this section before sharing with stakeholders.

| Rule | What it means |
| --- | --- |
| Fill every section | Write `N/A — <one sentence>` if a section truly doesn't apply. Never leave brackets unfilled. |
| Numbers beat adjectives | 'Significantly' / 'substantially' / 'meaningful' are banned from TL;DR and Hypothesis. |
| Solution table = behaviors | Each row is one verifiable behavior. Not how, only what. One row per requirement. |
| 3–5 pages | Cut Context §1 if not needed. Never cut: TL;DR, business impact table, AC table, metrics table, DoD. |
