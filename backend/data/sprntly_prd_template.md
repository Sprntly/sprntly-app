# [Surface] — [What we're shipping] PRD

Replace bracket text. Title under 12 words. Format: [Surface] — [What we're shipping].

**TL;DR** — [Three sentences max. Sentence 1: the problem. Sentence 2: the proposed fix. Sentence 3: the projected impact with concrete numbers.]

A senior reading only the TL;DR should know whether to read the rest. Lead with numbers.

Author: [Name] | Status: [Draft / In Review / Approved] | Target ship: [Date]

────────────────────────────────────────────────────────────

## 1. Context

1–2 short paragraphs. Why now? What does the reader need to know about the business, surface, and customer? Don't explain the problem yet — that's section 2.

[Paragraph 1 — the relevant product surface, customer segment, what's true today.]

[Paragraph 2 — what changed recently or why it's timely. Optional.]

────────────────────────────────────────────────────────────

## 2. Problem

### User problem

Frame from the user's POV. What are they trying to do? Where does it fail? What's the cost? Use their language. One short paragraph — a beat-by-beat narrative works well.

[A [user persona] is trying to [goal]. They [step-by-step what happens]. They run into [friction] which causes [pain]. As a result, [behavioral consequence].]

### Business problem

Translate to business terms. Quantify wherever possible.

| Dimension | Impact |
| --- | --- |
| Affected user volume | [# users / claims / sessions per month or year] |
| Cost per affected user | [$ / churn pp / NPS pts] |
| Annualized business cost | [$X/yr] |
| Trajectory | [Growing / stable / shrinking; why] |

────────────────────────────────────────────────────────────

## 3. Evidence

3 to 4 cuts of data. Each cut is a separate H3 with a one-sentence headline finding. For each cut, choose the presentation format that best communicates the data — an infographic (bar / line / pie / stat), a table, or prose.

If a cut has visual shape, present it visually using a fenced `chart` block (see "How to embed an infographic" at the bottom of this template). If a cut is a logical or causal argument that doesn't reduce to a clean visual, write the paragraph. If it's a flat list of values, use a markdown table.

End the section with Qualitative signals (bullets) and In their own words (user quotes). The whole section should be readable in 90 seconds.

### Cut 1: [One-sentence headline finding with the number]

Place the presentation first — chart, table, or paragraph. Then 1–3 sentences interpreting it: state what the data rules in and what it rules out.

[Presentation goes here — embed a chart block, a markdown table, or a paragraph.]

Rules in: [the hypothesis the cut supports]. Rules out: [hypotheses the cut eliminates].

### Cut 2: [Headline finding]

[Presentation goes here.]

[1–3 sentence interpretation.]

### Cut 3: [Headline finding]

[Presentation goes here.]

[1–3 sentence interpretation.]

### Cut 4 (optional): [Headline finding — cross-check ruling out a competing explanation]

Optional. Use when there's a tempting alternative hypothesis worth pre-empting.

[Presentation goes here.]

[1–3 sentence interpretation.]

### Qualitative signals

Bullets only. 3–5 lines. Volume, source, trend if known.

- [Source 1 — e.g., "X monthly Zendesk tickets matching [theme]; +Y% YoY"]
- [Source 2 — e.g., "~Z one-star App Store reviews citing [theme]"]
- [Source 3 — e.g., "[theme] is the #N support call reason"]

### In their own words

3–5 short, real user quotes. Attribute by source channel. Never invent quotes — if you don't have one, leave the bullet out.

- "[Verbatim user quote, 1–2 sentences max. Keep customer language; don't sanitize.]" — [Source channel — Zendesk / App Store / Reddit / Gong]
- "[Quote 2 — ideally from a different channel for breadth.]" — [Source]
- "[Quote 3 — ideally one that names the symptom or root cause directly.]" — [Source]
- "[Quote 4 — if you have a particularly strong call/transcript moment.]" — [Source]

────────────────────────────────────────────────────────────

## 4. Hypothesis

One paragraph. Format: "If we [X], then [Y will move from current to target], because [causal mechanism from Section 3]. [Optional secondary effect or future-proofing claim]."

[If we [proposed change], then [primary metric will move from current to target], because [the mechanism described in Section 3]. [Secondary benefit].]

────────────────────────────────────────────────────────────

## 5. Solution Requirements

What the system must do. Not how. Three subsections: Functional, Configuration, Telemetry. If multi-component, add a 4th per component.

[1 sentence at the highest level. Often: "Insert [X] into the existing [Y] flow. User-facing UX [does/does not] change."]

### Functional requirements

What the feature does. Each bullet = one verifiable behavior.

- [Behavior 1 — the core happy-path action]
- [Behavior 2 — quantified target / threshold]
- [Behavior 3 — algorithmic detail or fallback]
- [Behavior 4 — edge case (skip / no-op)]
- [Behavior 5 — platform constraint (threading, queue)]
- [Behavior 6 — dependency constraint (no third-party / specific library)]
- [Behavior 7 — error handling (replace silent failure)]

### Configuration

Feature flags, remote config, defaults. Always include the default at launch and the safe-value range.

- [Feature flag — e.g., feature_x_enabled (boolean, default off until rollout)]
- [Remote config — e.g., feature_x_threshold (numeric, default Y, range A–B)]

### Telemetry

Events the implementation must emit. Specify event name + field schema.

- [event_started — fields: input_size, device_model, os_version, ...context]
- [event_completed — fields: input_size, output_size, duration_ms, ...result]
- [event_skipped — fields: input_size, reason]
- [event_failed — fields: input_size, device_model, os_version, error_code]

────────────────────────────────────────────────────────────

## 6. Acceptance Criteria

Each row = one Given/When/Then. Engineering treats these as the contract for completion. Cover happy path, edge cases (offline, low memory, slow network), feature flag behavior (on/off), and external contracts (metadata, schema).

| # | Given / When / Then | Verified by |
| --- | --- | --- |
| AC1 | [Happy path — "Given a [target user/device], when the user does X, then [primary observable behavior]"] | [Integration test / unit test / QA] |
| AC2 | [Performance bound — "Given any [supported device], when [action] runs, then it completes in <Xms at P95"] | [Performance test in CI] |
| AC3 | [External contract — "Given [output] reviewed by [downstream system/team], then accuracy/quality is within [bound] of baseline"] | [Audit / external team sign-off] |
| AC4 | [Error handling — "Given a failure, when it occurs, then user sees explicit error with retry CTA"] | [QA with simulated failures] |
| AC5 | [Edge case — offline mode, low memory, very small/very large input] | [Scenario test] |
| AC6 | [Skip / no-op — "Given input already meets target, when processing runs, then it is skipped"] | [Unit test] |
| AC7 | [Feature flag off — always include this row] | [QA with flag toggled] |
| AC8 | [Data preservation — metadata, encoding, ordering preserved through transformation] | [Unit test against external contract] |

────────────────────────────────────────────────────────────

## 7. Metrics

One table. Three categories: Primary (the one being moved), Secondary (leading indicators), Guardrails (must-not-degrade). Always specify current and target.

| Category | Metric | Current | Target |
| --- | --- | --- | --- |
| Primary | [The one metric the hypothesis is trying to move] | [X%] | [Y%] |
| Secondary | [Leading indicator 1] | [X%] | [Y%] |
| Secondary | [Leading indicator 2] | [X/mo] | [Y/mo] |
| Secondary | [Leading indicator 3] | [X] | [Y] |
| Guardrail | [Quality bound] | [Baseline] | [Within Xpp] |
| Guardrail | [Reliability bound] | [Baseline] | [Within X%] |
| Guardrail | [Performance bound] | [Baseline] | [≤ baseline] |

────────────────────────────────────────────────────────────

## 8. Definition of Done (for coding agent)

Checklist a coding agent uses to know when to stop. Every bullet objectively verifiable.

The change is ready to merge to release branch when ALL of the following are true:

- All [N] acceptance criteria pass in CI
- [Implementation lives in specific file/module]
- [Threading/queue requirement]
- [Library/dependency constraint]
- [Algorithm specifics]
- [Edge-case branches]
- [External contract verified]
- [Feature flags wired through remote-config service; readable at decision time]
- [All telemetry events emit with the schema specified in section 5]
- [UI surface — explicitly stated]
- [Memory bound verified via profiler]
- [Performance bound verified via CI test]
- [Unit tests cover the new logic paths]
- [Integration test against staging endpoint passes]
- [Sentry release tagged with feature flag name]
- [PR description includes link to this PRD and the ticket number]

────────────────────────────────────────────────────────────

## 9. Test Plan

Three subsections: Pre-launch (validation), Rollout (A/B + staged %), Post-launch (monitoring). Each bullet has a duration, scope, and exit criterion.

### Pre-launch

- [Internal dogfood — duration, audience, exit criterion]
- [Closed beta — duration, sample size, exit criterion including external sign-off if applicable]

### Rollout

- [A/B design — control vs treatment ratio, sample size, MDE, duration]
- [Rollout schedule — e.g., 1% → 10% → 50% → 100% over [N] days]
- [Kill criteria — always specify automatic rollback triggers]

### Post-launch

- [Monitoring — dashboard owner and review cadence]
- [30-day retro — always include]
- [60- or 90-day check if applicable — for reputational metrics that lag]

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
