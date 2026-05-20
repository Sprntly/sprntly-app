# [Surface] — [What we're shipping]

[Subtitle: one sentence stating who this is for and what changes, e.g. 'For US iPhone screen-repair claimants, move deductible disclosure to step 1 of the claim funnel.']

:::context-chip
[Surface]  ·  Author: [Name]  ·  Status: [Draft | In Review | Approved]  ·  Target ship: [Date]  ·  Linked evidence: [Evidence-Page-ID or "—"]
:::

────────────────────────────────────────────────────────────

## TL;DR

Problem → Fix → Impact triptych. Numbers only; no adjectives. Anyone reading just this block should know whether to read the rest.

:::tldr
{
  "problem": "[One sentence: the user pain + key number, e.g. '57% of screen-repair claimants abandon at the deductible step']",
  "fix": "[One sentence: the proposed change, e.g. 'Move deductible disclosure to step 1; frame as part of upfront price']",
  "impact": "[One sentence: projected concrete numbers, e.g. 'Funnel completion 43% → 58%, +$143M ARR']"
}
:::

────────────────────────────────────────────────────────────

## 1. Context

[Paragraph 1 — the relevant product surface, customer segment, what is true today. 3–5 sentences. Do not explain the problem yet — that is Section 2.]

[Paragraph 2 — what changed recently or why this is timely. Optional. Cut if not strictly needed.]

────────────────────────────────────────────────────────────

## 2. Problem

:::problem
{
  "user_story": "[A [user persona] is trying to [goal]. They [step-by-step what happens]. They run into [friction] which causes [pain]. As a result, [behavioral consequence]. 3–5 sentences.]",
  "impact": [
    { "label": "Affected users",    "value": "[# users / month]",       "tone": "negative" },
    { "label": "Cost per user",     "value": "[$ / churn pp / NPS pts]","tone": "negative" },
    { "label": "Annualized cost",   "value": "[$X / yr]",                "tone": "negative" },
    { "label": "Trajectory",        "value": "[Growing +X% QoQ | Stable | Shrinking]", "tone": "neutral" }
  ]
}
:::

────────────────────────────────────────────────────────────

## 3. Hypothesis

:::hypothesis
{
  "if_we": "[Specific change — e.g. 'Move the deductible disclosure to step 1 of the claim funnel']",
  "then_metric": {
    "name": "[e.g. Funnel completion rate]",
    "current": "[43%]",
    "target": "[58%]"
  },
  "because": "[The mechanism from the problem above — one sentence]",
  "secondary": "[Optional second-order effect — e.g. 'Expect a 1–2pp dip in step-1 entry rate as low-intent users self-select out earlier']"
}
:::

────────────────────────────────────────────────────────────

## 4. Solution Requirements

Each row is a verifiable behavior, flag, config, or telemetry event. One row per requirement. Not how — only what.

:::requirements
[
  { "behavior": "[Behavior 1 — core happy-path action]",            "category": "functional", "detail": "[What the system does]" },
  { "behavior": "[Behavior 2 — quantified target or threshold]",    "category": "functional", "detail": "[Concrete number / bound]" },
  { "behavior": "[Behavior 3 — algorithmic detail or fallback]",    "category": "functional", "detail": "[Decision rule / fallback]" },
  { "behavior": "[Behavior 4 — edge case]",                          "category": "functional", "detail": "[Skip / no-op condition]" },
  { "behavior": "[Behavior 5 — error handling]",                     "category": "functional", "detail": "[Explicit error state]" },
  { "behavior": "[flag_name_enabled]",                               "category": "flag",        "detail": "boolean, default: false, safe range: on/off" },
  { "behavior": "[config_threshold]",                                "category": "config",      "detail": "[numeric, default: X, range: A–B, updated by: team]" },
  { "behavior": "[event_started]",                                    "category": "telemetry",   "detail": "[fields: user_id, device, os, context_field_1]" },
  { "behavior": "[event_completed]",                                  "category": "telemetry",   "detail": "[fields: user_id, output_field, duration_ms, result_field]" },
  { "behavior": "[event_failed]",                                     "category": "telemetry",   "detail": "[fields: user_id, device, os, error_code]" }
]
:::

────────────────────────────────────────────────────────────

## 5. Acceptance Criteria

:::acceptance-criteria
[
  { "id": "AC1", "kind": "happy-path",
    "given_when_then": "Given [target user], when [action], then [primary behavior]",
    "verified_by": "Integration test" },
  { "id": "AC2", "kind": "performance",
    "given_when_then": "Given any supported device, when [action] runs, then completes in <Xms at P95",
    "verified_by": "Perf test in CI" },
  { "id": "AC3", "kind": "error-handling",
    "given_when_then": "Given a failure, when it occurs, then user sees explicit error + retry",
    "verified_by": "QA simulated failure" },
  { "id": "AC4", "kind": "flag-off",
    "given_when_then": "Given flag=false, when user reaches [surface], then legacy behavior renders",
    "verified_by": "QA flag toggled" },
  { "id": "AC5", "kind": "edge-case",
    "given_when_then": "Given [offline / low memory / very large input], when [action], then [degraded but explicit behavior]",
    "verified_by": "Scenario test" }
]
:::

────────────────────────────────────────────────────────────

## 6. Metrics

:::metrics
{
  "primary": { "name": "[the one metric the hypothesis moves]", "current": "[X%]", "target": "[Y%]" },
  "secondary": [
    { "name": "[leading indicator 1]", "current": "[X%]", "target": "[Y%]" },
    { "name": "[leading indicator 2]", "current": "[X]",  "target": "[Y]" }
  ],
  "guardrails": [
    { "name": "[must-not-degrade metric]",            "baseline": "[X]",       "bound": "[within Xpp]" },
    { "name": "[reliability or performance bound]",   "baseline": "[baseline]", "bound": "[≤ baseline]" }
  ]
}
:::

────────────────────────────────────────────────────────────

## 7. Risks & Open Questions

:::risks
[
  { "risk": "[Risk 1 — what could go wrong]", "severity": "high | medium | low", "mitigation": "[Specific instrumentation, rollback trigger, or scope cut]" },
  { "risk": "[Risk 2]",                       "severity": "medium",              "mitigation": "[…]" },
  { "risk": "[Open question — phrased as a decision to be made]", "severity": "low", "mitigation": "[Owner + decision deadline]" }
]
:::

────────────────────────────────────────────────────────────

## 8. Rollout & Test Plan

:::milestones
[
  { "phase": "Pre-launch", "items": [
      "[Internal dogfood — duration, audience, exit criterion]",
      "[Closed beta — duration, sample size, exit criterion]"
  ]},
  { "phase": "Rollout", "items": [
      "[A/B design — 50/50, sample size, MDE, duration]",
      "[Schedule — 1% → 10% → 50% → 100% over N days]",
      "[Kill criteria — automatic rollback triggers]"
  ]},
  { "phase": "Post-launch", "items": [
      "[Monitoring — dashboard owner + review cadence]",
      "[30-day retro — always include]",
      "[90-day check — for metrics that lag]"
  ]}
]
:::

────────────────────────────────────────────────────────────

## 9. Definition of Done

:::dod
[
  "All acceptance criteria pass in CI",
  "Implementation lives in [specific file / module]",
  "Feature flags wired through remote-config service; readable at decision time",
  "All telemetry events emit with schema specified in Section 4",
  "P95 latency verified in CI performance test",
  "Unit tests cover new logic paths including edge cases",
  "Integration test against staging endpoint passes",
  "PR description links to this PRD and the ticket number"
]
:::

────────────────────────────────────────────────────────────

## How to use this template

Delete this section before sharing with stakeholders.

| Rule | What it means |
| --- | --- |
| Title = surface + change | Title under 12 words. Format: `[Surface] — [What we're shipping]`. |
| Subtitle = who + change | One sentence stating the user segment and the change in plain language. Most important line for a senior reader. |
| `:::tldr` is the 5-second read | Three sentences — problem, fix, impact. Numbers only. If you can't fill one of the three, the PRD isn't ready. |
| `:::problem` couples narrative + numbers | User story prose carries the empathy; impact cards carry the scale. Both required. |
| `:::hypothesis` is a moveable contract | `then_metric` must be specific enough to design an A/B from. If you can't pick a current and target, the PRD isn't ready. |
| `:::requirements` rows are behaviors, not how | One verifiable behavior per row. Categories: `functional` (default), `flag`, `config`, `telemetry`. |
| `:::acceptance-criteria` row = a passing test | Each AC is one Given/When/Then with a `verified_by` that names a real test type. |
| `:::metrics` separates primary from guardrail | Primary moves; secondary indicate movement; guardrails must not degrade. |
| `:::risks` requires mitigations | A risk without a mitigation is an unowned threat. Every row must have both. |
| `:::milestones` are testable | Each item names a duration / audience / exit criterion. "TBD" means the rollout isn't planned yet. |
| Semantic blocks (`:::name`) are first-class | The frontend renders each named block as a real component. Do not collapse a `:::tldr` into a paragraph or a `:::metrics` into a markdown table — the rendering depends on the block. |
| Never invent numbers, users, or sources | Every figure traces to the linked evidence or corpus. Drop anything you can't ground. |
