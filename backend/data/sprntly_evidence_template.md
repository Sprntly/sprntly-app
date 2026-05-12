# [Finding stated as a consequence — what is happening and why it matters]

[Subtitle: the specific behavior observed + the scale of the problem — e.g. '57% of screen repair claimants walk away at the deductible screen and never come back']

[Product area]  ·  [Analysis series name]  ·  [Period]  ·  [N records analyzed]

| Meta | Value |
| --- | --- |
| Analyst / Team | [Analyst name or team — e.g. Data Science & Product Analytics] |
| Analysis period | [Date range — e.g. Q1–Q3 2025 / trailing 12 months] |
| Data volume | [N events / users / sessions / records analyzed] |

────────────────────────────────────────────────────────────

## Estimated impact

Two to three highlighted business numbers — what this is costing, and what changes if it's fixed. Pick the ones a senior reader needs to internalize in five seconds.

| Metric | Value |
| --- | --- |
| [e.g. Revenue at risk] | [$X M / yr] |
| [e.g. Retention impact] | [+/-X pp] |
| [e.g. Affected users] | [N / month] |

────────────────────────────────────────────────────────────

## Bottom line

[Paragraph that buttresses the title with concrete substance: 3–5 sentences that establish the scale of the journey, where it works, and the exact step where it breaks — with the headline number stated plainly. The title states the problem; this paragraph makes it whole. Mirror the depth of a PRD's TL;DR + Context combined, not just a one-liner.]

[Introduce the first chart: tell the reader what to look for — e.g. 'The chart below shows the full funnel. Four steps lose fewer than 5% of users each. One step loses 57%.']

```chart
{
  "kind": "bar" | "line" | "pie" | "stat",
  "title": "Complete-sentence takeaway as the title",
  "subtitle": "optional source line",
  "data": [{"label": "string", "value": <number-or-string>}]
}
```

Rules in: [One sentence: the hypothesis this chart supports.] Rules out: [One sentence: the alternative this chart eliminates.]

[Beat 2 — Describe what happens to users after the drop-off point. Focus on downstream behavior: do they return, do they churn, how fast? Compare to users who completed the flow.]

```chart
{ "kind": "...", "title": "...", "data": [...] }
```

Rules in: [...]. Rules out: [...].

[Beat 3 — Name the cause explicitly. Do not hedge. Use data to show why users behave this way — price gap, UX failure, bug, missing feature, competitor alternative. Quantify the gap wherever possible.]

```chart
{ "kind": "...", "title": "...", "data": [...] }
```

Rules in: [...]. Rules out: [...].

[Beat 4 (optional) — Rule out the natural alternative: 'maybe these users were already low-intent.' Show tenure, payment history, NPS, or other pre-event signals that prove the drop-off event itself is the cause, not pre-existing intent.]

```chart
{ "kind": "...", "title": "...", "data": [...] }
```

Rules in: [...]. Rules out: [...].

[Beat 5 (optional) — Additional signal: competitive landscape, seasonal pattern, platform breakdown, or a cross-check from a second data source.]

```chart
{ "kind": "...", "title": "...", "data": [...] }
```

Rules in: [...]. Rules out: [...].

[Synthesis: state the root cause in one sentence, plainly. No hedging. Then state the business impact if nothing changes. E.g. 'This is not a UX problem — it is a pricing strategy problem. Until the gap closes, $143M walks out annually. The rest of this document traces the full evidence chain and ends with a testable hypothesis.']

────────────────────────────────────────────────────────────

## 1. Business context

[Paragraph 1 — the product surface, the customer segment, and the current state. 3–5 sentences. Do not explain the problem yet — that is Section 2.]

[Paragraph 2 — what changed recently or why this analysis is timely. Optional. Cut if not strictly needed.]

────────────────────────────────────────────────────────────

## 2. Business impact

[One sentence: name the business goal this analysis connects to. E.g. 'This finding directly impacts 30-day retention and ARR, two of the three core business goals for H2 2025.']

| Dimension | Impact |
| --- | --- |
| Affected user / event volume | [# users / sessions / events per month or year — be specific] |
| Cost per affected user | [$X / churn pp / LTV lost / NPS pts] |
| Annualized business cost | [$X/yr — revenue at risk, LTV gap, efficiency loss] |
| Trajectory | [Growing / stable / shrinking — one sentence on direction and why] |
| Business goal linked | [Retention / Revenue / Activation / Engagement / Cost reduction / NPS] |

────────────────────────────────────────────────────────────

## 3. Evidence

The data-science slicing of the data — why we got to the conclusion above. Every cut is visualized with the infographic type that best communicates the shape of the data (bar / line / pie / stat). Self-explanatory titles, no labels.

Evidence confidence: [High / Medium / Low]   |   If not High, state what additional data would change the rating.

### Cut 1 — [One-sentence headline finding with the number]

Source: [Tool/system]   |   Period: [date range and sample size]

**Chart brief**

| Field | Spec |
| --- | --- |
| Type | [funnel / line / grouped bar / comparison bar / scatter] |
| X-axis | [specific x-axis label] |
| Y-axis | [specific y-axis label] |
| Highlight | [name the one bar or data point that carries the story] |
| Color logic | [red = problem state   |   green = healthy state   |   two colors max] |

```chart
{ "kind": "...", "title": "Complete-sentence takeaway", "data": [...] }
```

Rules in: [One sentence: the hypothesis this cut supports.] Rules out: [One sentence: the competing hypothesis this eliminates.]

### Cut 2 — [One-sentence headline with the number]

Source: [Tool/system]   |   Period: [date range and sample size]

**Chart brief**

| Field | Spec |
| --- | --- |
| Type | [chart type] |
| X-axis | [x-axis] |
| Y-axis | [y-axis] |
| Highlight | [highlight element] |
| Color logic | [color logic] |

```chart
{ "kind": "...", "title": "Complete-sentence takeaway", "data": [...] }
```

Rules in: [...]. Rules out: [...].

### Cut 3 — [One-sentence headline with the number]

Source: [Tool/system]   |   Period: [date range and sample size]

**Chart brief**

| Field | Spec |
| --- | --- |
| Type | [chart type] |
| X-axis | [x-axis] |
| Y-axis | [y-axis] |
| Highlight | [highlight element] |
| Color logic | [color logic] |

```chart
{ "kind": "...", "title": "Complete-sentence takeaway", "data": [...] }
```

Rules in: [...]. Rules out: [...].

### Cut 4 (optional) — [Cross-check ruling out a competing explanation]

Source: [Tool/system]   |   Period: [date range and sample size]

**Chart brief**

| Field | Spec |
| --- | --- |
| Type | [chart type] |
| X-axis | [x-axis] |
| Y-axis | [y-axis] |
| Highlight | [highlight element] |
| Color logic | [color logic] |

```chart
{ "kind": "...", "title": "Complete-sentence takeaway", "data": [...] }
```

Rules in: [...]. Rules out: [...].

### Qualitative signals

Format each bullet: `[Source] — "[theme keyword]" — [volume: X tickets/month or X reviews] — [trend: +Y% YoY or stable]`. Real channels, real volume, real trend.

- [Source 1] — "[theme keyword]" — [volume] — [trend]
- [Source 2] — "[theme keyword]" — [volume] — [trend]
- [Source 3] — "[theme keyword]" — [volume] — [trend]
- [Source 4 — 3P] — "[theme]" — [volume] — [trend]

### In their own words

Real quotes only. Never invent. 1–2 sentences each. Attribute by channel, not individual. 3–5 max.

- "[Verbatim quote — keep customer language, do not sanitize.]" — [Zendesk / App Store / Reddit / Gong / G2]
- "[Verbatim quote from a different channel for breadth.]" — [Source]
- "[Verbatim quote that names the symptom or root cause directly.]" — [Source]

────────────────────────────────────────────────────────────

## 4. What the data says together

[Paragraph 1: summarize what the quantitative cuts collectively establish. Name the mechanism. Do not just list the charts — synthesize them into one causal story. 3–4 sentences.]

[Paragraph 2: add what the qualitative signals contribute. How do customer voices and 3P signals confirm, sharpen, or add nuance to the quantitative picture? 2–3 sentences.]

[Synthesis statement: one or two sentences that state, plainly, what the PM or engineer now knows that justifies action. E.g. 'Taken together, the data establishes that the deductible screen is a pricing problem, not a product problem — and that fixing it is worth $143M ARR.']

────────────────────────────────────────────────────────────

## 5. Hypothesis

If we [specific change], then [primary metric] will move from [current] to [target], because [mechanism from the evidence above]. [Optional secondary effect].

────────────────────────────────────────────────────────────

## How to use this template

Delete this section before sharing with stakeholders.

| Rule | What it means |
| --- | --- |
| Title = consequence, not label | The title names what is happening to users and what it costs. Never use a noun-phrase label ('Checkout Analysis'). Always use a finding-as-consequence ('Users are abandoning checkout at payment and not returning'). |
| Subtitle = behavior + scale | The subtitle states the specific behavior observed and the scale of the problem. It is the most important sentence in the document for a senior reader. |
| Bottom line = narrative with evidence | Each beat introduces a chart. Write the paragraph first, then drop the chart in. The paragraph tells the reader what to look for; the chart proves it. Delete beats you don't need; add beats if the story requires them. |
| Beat count is flexible | 1 beat for a simple finding, 5–6 for a complex multi-dimensional problem. Every chart must be introduced by a paragraph that frames it and followed by Rules in / Rules out. |
| Qualitative confirms quantitative | Section 3's qualitative bullets are not decoration. They should either confirm the quantitative story or add a dimension the data can't capture. |
| Synthesis is for the skipper | Section 4 is for a senior reader who skipped the evidence. Write it as if the reader saw nothing else. |
| Hypothesis is the deliverable | Everything in this document builds toward Section 5. If the hypothesis is not specific enough to design an A/B test from, the analysis is not finished. |
| Business goal is always named | The impact table must name which business goal (Retention / Revenue / Activation / Engagement / Cost / NPS) this finding connects to. |
| Never invent quotes | If you do not have a real quote, drop the bullet. Invented quotes destroy credibility. |
