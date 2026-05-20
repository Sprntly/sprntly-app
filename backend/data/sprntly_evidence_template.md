# [Finding stated as a consequence — what is happening and why it matters]

[Subtitle: the specific behavior observed + the scale of the problem in one sentence — e.g. '57% of screen repair claimants walk away at the deductible screen and never come back']

:::context-chip
[Product area]  ·  [Customer segment]  ·  [Period]  ·  [N records analyzed]  ·  Confidence: [High | Medium | Low]
:::

────────────────────────────────────────────────────────────

## Impact at a glance

:::hero
[
  {
    "label": "[e.g. Revenue at risk]",
    "value": "[$143M / yr]",
    "delta": "[+18% YoY | optional — drop the field if N/A]",
    "baseline": "[vs. $1.2B category | optional]",
    "tone": "negative | neutral | positive"
  },
  {
    "label": "[e.g. Affected users]",
    "value": "[218k / mo]",
    "delta": "[+9% QoQ]",
    "baseline": "[12% of monthly actives]",
    "tone": "negative"
  },
  {
    "label": "[e.g. Retention impact]",
    "value": "[-4.2 pp]",
    "delta": "[steady]",
    "baseline": "[goal: +1 pp by Q4]",
    "tone": "negative"
  }
]
:::

────────────────────────────────────────────────────────────

## The 30-second story

[3–5 sentence paragraph that buttresses the title with concrete substance: scale of the journey → where it works → exact step where it breaks → the headline number plainly stated. This is the most important paragraph in the document for a reader who will only read one paragraph.]

[Paragraph framing the headline chart: tell the reader what to look for in the next chart in one or two sentences.]

```chart
{
  "kind": "bar" | "line" | "pie" | "donut" | "stat" | "gauge",
  "title": "Complete-sentence takeaway as the title",
  "subtitle": "optional source line",
  "data": [{"label": "string", "value": "<number-or-string>"}]
}
```

:::callout type="rules"
**Supports:** [One sentence: the hypothesis this chart supports.]
**Rules out:** [One sentence: the alternative this chart eliminates.]
:::

[Optional additional beat — only if a single chart can't carry the story. Each beat = framing paragraph → chart → rules callout. Cap at 3 beats in this section; deeper cuts belong in Section 2.]

────────────────────────────────────────────────────────────

## 1. Evidence at a glance

[3–4 row mini-index — one row per cut below. Each row is a one-sentence takeaway with its confidence. Helps a reader decide which cuts to read fully and which to skim. Use this exact block syntax so the frontend can render it as a compact scannable list.]

:::cuts-index
[
  { "n": 1, "headline": "[One-sentence takeaway with the number]", "confidence": "High" },
  { "n": 2, "headline": "[One-sentence takeaway with the number]", "confidence": "High" },
  { "n": 3, "headline": "[One-sentence takeaway with the number]", "confidence": "Medium" },
  { "n": 4, "headline": "[Cross-check ruling out a competing explanation]", "confidence": "Medium" }
]
:::

────────────────────────────────────────────────────────────

## 2. Evidence

The data-science slicing of the data — why we got to the conclusion above. Every cut is visualized with the chart type that best communicates the shape of the data. Each cut is self-contained: provenance chip row → chart → rules callout.

### Cut 1 — [One-sentence headline finding with the number]

:::source
[
  { "kind": "tool",  "label": "[Mixpanel | Snowflake | Amplitude | Zendesk | App Store | Gong | G2]" },
  { "kind": "period","label": "[Q1–Q3 2025]" },
  { "kind": "sample","label": "[n = 14,200 sessions]" },
  { "kind": "confidence","label": "High | Medium | Low" }
]
:::

```chart
{ "kind": "...", "title": "Complete-sentence takeaway", "data": [...] }
```

:::callout type="rules"
**Supports:** [One sentence: the hypothesis this cut supports.]
**Rules out:** [One sentence: the competing hypothesis this eliminates.]
:::

### Cut 2 — [One-sentence headline with the number]

:::source
[
  { "kind": "tool",  "label": "[Source]" },
  { "kind": "period","label": "[Period]" },
  { "kind": "sample","label": "[n = ...]" },
  { "kind": "confidence","label": "High | Medium | Low" }
]
:::

```chart
{ "kind": "...", "title": "Complete-sentence takeaway", "data": [...] }
```

:::callout type="rules"
**Supports:** [...]
**Rules out:** [...]
:::

### Cut 3 — [One-sentence headline with the number]

:::source
[
  { "kind": "tool",  "label": "[Source]" },
  { "kind": "period","label": "[Period]" },
  { "kind": "sample","label": "[n = ...]" },
  { "kind": "confidence","label": "High | Medium | Low" }
]
:::

```chart
{ "kind": "...", "title": "Complete-sentence takeaway", "data": [...] }
```

:::callout type="rules"
**Supports:** [...]
**Rules out:** [...]
:::

### Cut 4 (optional) — [Cross-check ruling out a competing explanation]

:::source
[
  { "kind": "tool",  "label": "[Source]" },
  { "kind": "period","label": "[Period]" },
  { "kind": "sample","label": "[n = ...]" },
  { "kind": "confidence","label": "High | Medium | Low" }
]
:::

```chart
{ "kind": "...", "title": "Complete-sentence takeaway", "data": [...] }
```

:::callout type="rules"
**Supports:** [...]
**Rules out:** [...]
:::

### Qualitative signals

Format each bullet: `[Source] — "[theme keyword]" — [volume: X tickets/month or X reviews] — [trend: +Y% YoY or stable]`. Real channels, real volume, real trend.

- [Source 1] — "[theme keyword]" — [volume] — [trend]
- [Source 2] — "[theme keyword]" — [volume] — [trend]
- [Source 3] — "[theme keyword]" — [volume] — [trend]
- [Source 4 — 3P] — "[theme]" — [volume] — [trend]

### In their own words

Verbatim customer quotes rendered as cards. Real quotes only — never invent. 1–2 sentences each, max 3 quotes. Use this block syntax so the frontend can render each as a card with oversized open-quote, italic body, and channel chip.

:::quote
{
  "body": "[Verbatim quote — keep customer language, do not sanitize.]",
  "channel": "[Zendesk | App Store | Reddit | Gong | G2]",
  "context": "[optional: ticket date / rating / call segment]"
}
:::

:::quote
{
  "body": "[Verbatim quote from a different channel for breadth.]",
  "channel": "[Source]",
  "context": "[optional]"
}
:::

:::quote
{
  "body": "[Verbatim quote that names the symptom or root cause directly.]",
  "channel": "[Source]",
  "context": "[optional]"
}
:::

────────────────────────────────────────────────────────────

## 3. What the data says together

[Paragraph 1: synthesize the cuts into one causal story. Name the mechanism plainly. 3–4 sentences.]

[Paragraph 2: what the qualitative signals add. How do customer voices confirm, sharpen, or add nuance to the quantitative picture? 2–3 sentences.]

[Synthesis statement: one or two sentences that state, plainly, what the PM or engineer now knows that justifies action.]

────────────────────────────────────────────────────────────

## 4. If nothing changes

[One-sentence projection: extrapolate the current trend 90 days out, grounded in cuts above. Skip this whole section if the cuts don't contain a real trend to extrapolate — write `:::forecast omitted="no trend basis"` and stop.]

```chart
{
  "kind": "line",
  "title": "Projected [metric] through [date] if no intervention",
  "subtitle": "Linear projection from [base period]",
  "data": [
    { "label": "[period 1]", "value": "[actual]" },
    { "label": "[period 2]", "value": "[actual]" },
    { "label": "[period 3 — projected]", "value": "[projected]" }
  ]
}
```

[One sentence stating the cumulative cost of inaction over the projection window — e.g. '~$36M ARR lost and 12,400 additional abandons through Q1 2026.']

────────────────────────────────────────────────────────────

## How to use this template

Delete this section before sharing with stakeholders.

| Rule | What it means |
| --- | --- |
| Title = consequence, not label | Title names what is happening to users and what it costs. Never a noun-phrase label. |
| Subtitle = behavior + scale | Most important sentence for a senior reader. |
| Hero strip = 3 numbers, 5 seconds | The `:::hero` block is the dashboard moment. Three cards max — pick the numbers a senior reader needs to internalize before scrolling. |
| Confidence is always visible | Chip on every cut + on the context chip up top. If overall confidence is not High, say what would change it. |
| Cuts index before evidence | The `:::cuts-index` block lets readers skim. Headlines are takeaways, not labels. |
| Quote cards, max 3 | Quotes are emotional anchors. Three striking quotes beats six bullet-list quotes. Never invent. |
| Semantic blocks (`:::name`) are first-class | The frontend renders each named block as a real component. Do not collapse a `:::hero` into a markdown table or a `:::quote` into a bullet — the rendering depends on the block. |
| Forecast section may be omitted | If cuts don't support a trend extrapolation, write `:::forecast omitted="<reason>"` rather than fabricating a projection. |
| Never invent numbers, quotes, sources | Every figure must trace to the insight or corpus. Drop anything you can't ground. |
