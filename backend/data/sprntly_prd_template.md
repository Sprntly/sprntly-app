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

## 3. Evidence

Evidence confidence: [High / Medium / Low]   |   If not High, add one sentence on the data gap.

### Cut 1 — [One-sentence headline finding with the number]

Source: [Tool/system]   |   Date range: [e.g. Q1–Q3 2025 / trailing 12 months]

**Chart brief**

| Field | Spec |
| --- | --- |
| Type | [funnel / line / grouped bar / comparison bar / scatter] |
| X-axis | [specific axis label — e.g. 'Claim step', not just 'Steps'] |
| Y-axis | [specific axis label — e.g. 'Completion rate (%)', not just '%'] |
| Highlight | [name the one bar or data point that carries the story] |
| Color logic | [red = problem state   |   green = healthy state   |   two colors max] |

[Infographic goes here — full content width. Build after filling chart brief above.]

Rules in: [one sentence — the hypothesis this cut supports]. Rules out: [one sentence — the competing hypothesis this eliminates].

### Cut 2 — [One-sentence headline with the number]

Source: [Tool/system]   |   Date range: [range]

**Chart brief**

| Field | Spec |
| --- | --- |
| Type | [chart type] |
| X-axis | [x-axis] |
| Y-axis | [y-axis] |
| Highlight | [highlight element] |
| Color logic | [color logic] |

[Infographic goes here — full content width.]

Rules in: [one sentence]. Rules out: [one sentence].

### Cut 3 — [One-sentence headline with the number]

Source: [Tool/system]   |   Date range: [range]

**Chart brief**

| Field | Spec |
| --- | --- |
| Type | [chart type] |
| X-axis | [x-axis] |
| Y-axis | [y-axis] |
| Highlight | [highlight element] |
| Color logic | [color logic] |

[Infographic goes here — full content width.]

Rules in: [one sentence]. Rules out: [one sentence].

### Cut 4 (optional) — [Use only to pre-empt a tempting competing explanation]

Source: [Tool/system]   |   Date range: [range]

**Chart brief**

| Field | Spec |
| --- | --- |
| Type | [chart type] |
| X-axis | [x-axis] |
| Y-axis | [y-axis] |
| Highlight | [highlight element] |
| Color logic | [color logic] |

[Infographic goes here — full content width.]

Rules in: [one sentence]. Rules out: [one sentence].

### Qualitative signals

Format each bullet: `[Source] — "[theme keyword]" — [volume: X/month or X reviews] — [trend: +Y% YoY or stable]`.

- [Source 1 — theme — volume — trend]
- [Source 2 — theme — volume — trend]
- [Source 3 — theme — volume — trend]

### In their own words

Real quotes only. Never invent. 1–2 sentences. Attribute by channel, not individual. 3–5 max.

- "[Verbatim quote.]" — [Zendesk / App Store / Reddit / Gong]
- "[Verbatim quote.]" — [Source]
- "[Verbatim quote.]" — [Source]

────────────────────────────────────────────────────────────

## 4. Hypothesis

If we [proposed change], then [primary metric will move from X to Y], because [causal mechanism from Section 3]. [Optional secondary benefit.]

────────────────────────────────────────────────────────────

## 5. Solution Requirements

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

## 6. Acceptance Criteria

| # | Given / When / Then | Verified by |
| --- | --- | --- |
| AC1 | Happy path — Given [target user], when [action], then [primary behavior] | Integration test |
| AC2 | Performance — Given any supported device, when [action] runs, then completes in <Xms at P95 | Perf test in CI |
| AC3 | Error handling — Given a failure, when it occurs, then user sees explicit error + retry | QA simulated failure |
| AC4 | Feature flag off — Given flag=false, when user reaches [surface], then legacy behavior renders | QA flag toggled |
| AC5 | Edge case — [offline / low memory / very large input] behaves as specified | Scenario test |

────────────────────────────────────────────────────────────

## 7. Metrics

| Category | Metric | Current | Target |
| --- | --- | --- | --- |
| Primary | [the one metric the hypothesis moves] | [X%] | [Y%] |
| Secondary | [leading indicator 1] | [X%] | [Y%] |
| Secondary | [leading indicator 2] | [X] | [Y] |
| Guardrail | [must-not-degrade metric] | [baseline] | [within Xpp] |
| Guardrail | [reliability or performance bound] | [baseline] | [≤ baseline] |

────────────────────────────────────────────────────────────

## 8. Definition of Done

Ready to merge when ALL of the following are true:

- All acceptance criteria pass in CI
- Implementation lives in [specific file / module]
- Feature flags wired through remote-config service; readable at decision time
- All telemetry events emit with schema specified in Section 5
- P95 latency verified in CI performance test
- Unit tests cover new logic paths including edge cases
- Integration test against staging endpoint passes
- PR description links to this PRD and the ticket number

────────────────────────────────────────────────────────────

## 9. Test Plan

| Phase | Detail |
| --- | --- |
| Pre-launch | [Internal dogfood — duration, audience, exit criterion]<br>[Closed beta — duration, sample size, exit criterion] |
| Rollout | [A/B design — 50/50, sample size, MDE, duration]<br>[Schedule — 1% → 10% → 50% → 100% over N days]<br>[Kill criteria — automatic rollback triggers] |
| Post-launch | [Monitoring — dashboard owner + review cadence]<br>[30-day retro — always include]<br>[90-day check — for metrics that lag] |

────────────────────────────────────────────────────────────

## How to embed an infographic

When a data cut has visual shape, embed it as a fenced code block with language `chart`. The renderer parses the JSON inside and draws an SVG infographic. Allowed `kind` values: `bar`, `line`, `pie`, `stat`.

Schema:

```
```chart
{
  "kind": "bar" | "line" | "pie" | "stat",
  "title": "Complete-sentence takeaway as the title",
  "subtitle": "optional secondary line, e.g. source",
  "data": [
    { "label": "string", "value": number }
  ]
}
```
```

Examples:

```chart
{
  "kind": "bar",
  "title": "iPhone 15 Pro fails at 25% upload — every other device <2%",
  "subtitle": "Source: Upload_Failure_By_Device sheet, Apr 2026",
  "data": [
    { "label": "iPhone 15 Pro", "value": 24.6 },
    { "label": "iPhone 15 Pro Max", "value": 27.4 },
    { "label": "iPhone 14", "value": 0.9 },
    { "label": "Galaxy S24", "value": 0.7 }
  ]
}
```

```chart
{
  "kind": "pie",
  "title": "65% of abandoned filings deflect to support calls",
  "data": [
    { "label": "Calls support", "value": 65 },
    { "label": "Retries until success", "value": 22 },
    { "label": "Churns", "value": 13 }
  ]
}
```

```chart
{
  "kind": "line",
  "title": "Photo upload failures climbed 340% YoY since iPhone 15 Pro launch",
  "data": [
    { "label": "Q1'24", "value": 280 },
    { "label": "Q2'24", "value": 410 },
    { "label": "Q3'24", "value": 720 },
    { "label": "Q4'24", "value": 980 },
    { "label": "Q1'25", "value": 1240 }
  ]
}
```

```chart
{
  "kind": "stat",
  "title": "Estimated annualized impact",
  "data": [
    { "label": "MRR at risk", "value": "$14.2K/mo" },
    { "label": "Affected users", "value": "2.1K/wk" },
    { "label": "Support tickets", "value": "87/mo" }
  ]
}
```

Rules:
- Use `bar` for category comparisons (devices, segments, cohorts).
- Use `line` for time series (trend over weeks / months / quarters).
- Use `pie` for share-of-whole (must sum to ~100; otherwise use `bar`).
- Use `stat` for 2–4 hero numbers when there's no comparable axis.
- The `title` is a complete-sentence takeaway, not a label like "Failure rate".
- Numeric values must come from the corpus. Never invent data points.
- Don't manufacture a chart that doesn't carry information. Prefer prose if a sentence would say it faster.

────────────────────────────────────────────────────────────

## How to use this template

Delete this section before sharing with stakeholders.

| Rule | What it means |
| --- | --- |
| Fill every section | Write `N/A — <one sentence>` if a section truly doesn't apply. Never leave brackets unfilled. |
| Numbers beat adjectives | 'Significantly' / 'substantially' / 'meaningful' are banned from TL;DR and Hypothesis. |
| Evidence confidence first | If you can't rate Evidence as High, the PRD isn't ready. Go gather more data. |
| Charts are the default | Every cut with quantitative data → infographic. Fill chart brief before building. Prose only for logical arguments with no numbers. |
| Chart brief is mandatory | If you can't fill all five chart brief fields, you don't yet understand your data. Resolve before building. |
| Rules in / out = 2 sentences | One sentence each, labeled. If you need more, the cut is doing too much — split it. |
| Solution table = behaviors | Each row is one verifiable behavior. Not how, only what. One row per requirement. |
| Quotes: real only | Never invent. Drop the bullet if you don't have the quote. 3–5 max across channels. |
| 3–5 pages | Cut Context §1 if not needed. Cut Cut 4 if you only have 3 strong cuts. Never cut: TL;DR, business impact table, evidence charts, AC table, metrics table, DoD. |
