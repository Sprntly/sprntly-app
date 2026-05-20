# Claims funnel — Move deductible disclosure to step 1

For US iPhone screen-repair claimants, surface the deductible inline with the plan-tier price at step 1 instead of revealing it post-confirmation at step 4.

:::context-chip
Claims · Author: A. Jain · Status: Draft · Target ship: 2026-Q3 · Linked evidence: EV-0142 (screen-repair deductible abandonment)
:::

────────────────────────────────────────────────────────────

## TL;DR

:::tldr
{
  "problem": "57% of US iPhone screen-repair claimants abandon at the deductible step; 81% never re-enter the funnel.",
  "fix": "Move deductible disclosure from step 4 to step 1 and frame it as part of the upfront plan-tier price.",
  "impact": "Funnel completion 43% → 58% (+15pp), recovers ~$143M of annualized revenue at current traffic."
}
:::

────────────────────────────────────────────────────────────

## 1. Context

The Asurion screen-repair claim funnel is a five-step flow: start claim → verify device → confirm address → see deductible → schedule repair. Screen repair is the single largest claim type with 142,400 starts in Q1–Q3 2025, and >90% of users complete steps 1–4. The deductible amount (typically $29–$249 depending on device tier) is currently revealed only at step 4, after the user has spent ~3 minutes in the flow.

The current step-4 disclosure dates back to a 2022 design when device tiers were simpler and deductibles were uniformly $29. As device-tier pricing has fragmented over the last two years, the surprise at step 4 has grown linearly with the dollar amount shown.

────────────────────────────────────────────────────────────

## 2. Problem

:::problem
{
  "user_story": "A US iPhone claimant with a cracked screen starts a claim from the Asurion app. They complete identity verification (step 2) and address confirmation (step 3) in under three minutes. At step 4 they see a $149 deductible they didn't expect — neither the plan-purchase flow nor the in-app coverage card showed it. 57% abandon at this step. 81% of those abandoners never re-enter any claim funnel within 30 days; the most common outcome is they walk to a third-party repair shop.",
  "impact": [
    { "label": "Affected users",   "value": "81k abandons / mo",         "tone": "negative" },
    { "label": "Cost per user",    "value": "~$1,765 LTV gap",            "tone": "negative" },
    { "label": "Annualized cost",  "value": "$143M / yr",                 "tone": "negative" },
    { "label": "Trajectory",        "value": "Growing +9% QoQ",            "tone": "negative" }
  ]
}
:::

────────────────────────────────────────────────────────────

## 3. Hypothesis

:::hypothesis
{
  "if_we": "Move the deductible disclosure from step 4 to step 1 of the claim funnel and frame it as part of the upfront plan-tier price.",
  "then_metric": {
    "name": "Screen-repair funnel completion rate",
    "current": "43%",
    "target": "58%"
  },
  "because": "Cuts 1–3 of EV-0142 show abandonment rises monotonically with the deductible amount shown, and Cut 4 rules out a pre-existing-low-intent explanation. Removing the surprise eliminates the abandonment trigger.",
  "secondary": "Expect a 1–2pp dip in step-1 entry rate as low-intent users self-select out earlier — this is a feature, not a bug; the abandoned step-4 attempts have zero downstream value today."
}
:::

────────────────────────────────────────────────────────────

## 4. Solution Requirements

:::requirements
[
  { "behavior": "Deductible displayed inline with plan-tier price at step 1", "category": "functional", "detail": "Render `$X / mo + $Y deductible per claim` on every visible tier card; both numbers same weight" },
  { "behavior": "Step 4 deductible screen removed when flag is on",            "category": "functional", "detail": "Skip directly from address confirmation to schedule repair; no separate disclosure modal" },
  { "behavior": "Disclosure copy varies by device tier",                       "category": "functional", "detail": "Pull deductible from `device_tier_deductible_table`; fall back to range label if device tier unknown" },
  { "behavior": "Unknown device tier shows range, not single number",         "category": "functional", "detail": "If `device_tier` is null/unknown, show `$29–$249 deductible depending on device` and force device-verification before continuing" },
  { "behavior": "Error state if deductible lookup fails",                     "category": "functional", "detail": "Show explicit retry CTA; never silently fall back to legacy step-4 flow" },
  { "behavior": "deductible_disclosure_upfront_enabled",                       "category": "flag",        "detail": "boolean, default: false, scope: per-region (US iPhone first), safe range: on/off" },
  { "behavior": "deductible_disclosure_format",                                "category": "config",      "detail": "enum {inline, modal_on_tap}, default: inline, range: {inline, modal_on_tap}, updated by: growth-team" },
  { "behavior": "claim_funnel_step1_entered",                                   "category": "telemetry",   "detail": "fields: user_id, device_tier, device_model, deductible_shown_cents, plan_tier_visible_count" },
  { "behavior": "claim_funnel_step1_completed",                                 "category": "telemetry",   "detail": "fields: user_id, device_tier, deductible_shown_cents, tier_selected, time_on_step_ms" },
  { "behavior": "claim_funnel_abandoned",                                       "category": "telemetry",   "detail": "fields: user_id, last_step_reached, deductible_shown_cents, time_to_abandon_ms" }
]
:::

────────────────────────────────────────────────────────────

## 5. Acceptance Criteria

:::acceptance-criteria
[
  { "id": "AC1", "kind": "happy-path",
    "given_when_then": "Given a US iPhone claimant whose device tier resolves, when they reach plan selection, then deductible appears inline on every tier card before any tap",
    "verified_by": "Integration test in claims-app/src/funnel/Step1.test.tsx" },
  { "id": "AC2", "kind": "performance",
    "given_when_then": "Given any supported device, when plan selection renders, then deductible-table lookup completes in <80ms at P95",
    "verified_by": "Perf test in claims-service CI" },
  { "id": "AC3", "kind": "error-handling",
    "given_when_then": "Given the deductible-table lookup fails, when the user reaches plan selection, then a retry CTA appears (no silent fallback to step 4)",
    "verified_by": "QA — simulated 5xx from device-tier service" },
  { "id": "AC4", "kind": "flag-off",
    "given_when_then": "Given deductible_disclosure_upfront_enabled=false, when a user starts a claim, then legacy step-4 disclosure renders unchanged",
    "verified_by": "QA — flag toggled in pre-prod" },
  { "id": "AC5", "kind": "edge-case",
    "given_when_then": "Given an unknown device tier, when the user reaches plan selection, then a deductible range ($29–$249) is shown and device-verification is forced before continuing",
    "verified_by": "Scenario test in QA matrix" }
]
:::

────────────────────────────────────────────────────────────

## 6. Metrics

:::metrics
{
  "primary": { "name": "Screen-repair funnel completion rate", "current": "43%", "target": "58%" },
  "secondary": [
    { "name": "Step-1 → step-2 conversion", "current": "97%", "target": "95%" },
    { "name": "Deductible-table render success rate", "current": "n/a", "target": "≥99.9%" },
    { "name": "30-day re-entry rate after abandonment", "current": "19%", "target": "no change" }
  ],
  "guardrails": [
    { "name": "Plan-tier mix shift to lowest tier", "baseline": "23%", "bound": "≤27% (≤4pp shift)" },
    { "name": "Plan-purchase conversion (upstream)", "baseline": "12.4%", "bound": "≥12.0%" },
    { "name": "Plan-selection page load P95", "baseline": "780ms", "bound": "≤900ms" },
    { "name": "Customer-support deductible-related tickets", "baseline": "1,840 / mo", "bound": "≤1,840 / mo (must not grow)" }
  ]
}
:::

────────────────────────────────────────────────────────────

## 7. Risks & Open Questions

:::risks
[
  { "risk": "Lowest-tier plan becomes more attractive once deductibles are visible upfront; tier mix shifts down and LTV erodes.",
    "severity": "medium",
    "mitigation": "Tier-mix is the headline guardrail; kill rollout if shift exceeds 4pp within first 7 days. Run a tier-mix monitor with 1h cadence during rollout." },
  { "risk": "Plan-purchase conversion drops as prospective customers see deductibles and decide insurance isn't worth it.",
    "severity": "high",
    "mitigation": "Plan-purchase conversion is a guardrail. Limit initial rollout to existing-customer claim funnel only; defer plan-purchase surface to a follow-up PRD." },
  { "risk": "iOS app review delays the mobile rollout while web is already shipping; data is split across two cohorts.",
    "severity": "low",
    "mitigation": "Stratify A/B analysis by platform; require both platforms shipped before reading combined results." },
  { "risk": "Open question — should deductible appear as a separate line item or be baked into a single 'effective cost' headline?",
    "severity": "low",
    "mitigation": "Design owns the decision by 2026-06-15. Default to line-item per current spec; revisit after pre-launch usability tests." }
]
:::

────────────────────────────────────────────────────────────

## 8. Rollout & Test Plan

:::milestones
[
  { "phase": "Pre-launch", "items": [
      "Internal dogfood — 1 week, claims-team only (50 users), exit: no P0/P1 bugs, completion rate not worse than control",
      "Closed beta — 2 weeks, 500 invited iPhone users with active screen-repair coverage, exit: completion rate ≥48% AND no guardrail tripped"
  ]},
  { "phase": "Rollout", "items": [
      "A/B 50/50, n=12,000 sessions per arm, MDE 4pp on completion rate, duration 3 weeks at current traffic",
      "Ramp: 1% → 10% → 50% → 100% over 4 days once A/B reads positive",
      "Kill criteria: tier-mix shift >4pp OR plan-purchase conversion drop >0.4pp OR page load P95 >900ms"
  ]},
  { "phase": "Post-launch", "items": [
      "Dashboard owner: data-platform team; weekly review for first 4 weeks then monthly",
      "30-day retro — always include; cover both quantitative results and customer-support ticket trend",
      "90-day LTV check — confirm tier-mix shift did not erode lifetime value beyond projection"
  ]}
]
:::

────────────────────────────────────────────────────────────

## 9. Definition of Done

:::dod
[
  "All acceptance criteria pass in CI",
  "Implementation lives in claims-app/src/funnel/Step1.* and claims-service/src/handlers/deductible.ts",
  "Feature flag wired through Optimizely and readable at decision time (no caching of stale values)",
  "All telemetry events (step1_entered, step1_completed, abandoned) emit with the schemas in Section 4",
  "P95 latency verified in CI performance test against staging device-tier service",
  "Unit tests cover unknown-device-tier branch, table-lookup failure branch, and flag-off branch",
  "Integration test against staging claims-service passes for 5 device tiers",
  "PR description links to this PRD (PRD-0331) and the ticket (JIRA CLAIMS-4421)"
]
:::
