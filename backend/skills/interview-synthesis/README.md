# interview-synthesis

A PM-agent skill that turns a set of **qualitative interviews** into a synthesis that **opens with the customer's own voice** and ends in clear recommendations — built so it reads like the customers talking, not a corporate report.

## What it is
The synthesis skill for **interviews specifically** — 1:1 calls, in-person sessions, roundtables/focus groups, contextual inquiry, diary studies, usability sessions, win/loss and churn-exit interviews. It accounts for *how* the research was done because method changes interpretation, and it keeps real customer quotes at the centre.

## What it does — voice-led, top to bottom
1. **Voices — in their words (FIRST)** — opens with a hero of the most powerful real customer quotes, large and attributed. The customer speaks before any analysis; it should feel like the customers talking, not a corporate report.
2. **TL;DR** — one line on what the voices add up to, then `#1/#2/#3`, each with a real quote + the problem + what it means to us + what we can do.
3. **Themes (quote-led)** — each *opens with its real quotes, large*, then a compact read beneath: signal strength (n of N), the job/pain, persistent-vs-new, **cross-source convergence** (does it also show up in reviews/support/other feedback?), severity, and metric correlation if real data exists.
4. **Top pain points** *(when volume is large enough)* — participants, severity, persistent/new, and — only with real data — correlation to a core metric.
5. **Surprises · disconfirming · what we still don't know** — the rigor layer.
6. **About this research** — methodology and context (n, method, segments, when, who, recruiting, saturation, bias flags), placed *after* the voice rather than gating it.
7. **Recommendations** — action cards with **Create brief** (→ evidence-brief / prd-author) or **Add to backlog** (→ prioritize).

## Why it's more than "list the themes"
- **Methodology & context** up front, with **method-bias flags** (roundtable groupthink, CSM/sales-led rapport bias, exit-interview survivorship).
- **Signal strength as counts + saturation** ("7 of 9", not "78%").
- **Persistent vs new** and **cross-source convergence** — an interview theme that also appears in reviews/support is far stronger; shown explicitly, never forced.
- **Say-do gap, segment differences, surprises, disconfirming evidence, open questions.**
- **Metric correlation only with real data** (churn/activation/$), labeled qualitative-only otherwise.

## Guardrails
Real quotes only (never fabricated/overstated) · small-n reported as counts, not survey percentages · no fabricated metrics · correlation ≠ causation · convergence only from real independent sources · disconfirming evidence surfaced, not buried.

## When to use / not
- **Use** to synthesize interviews into themes, strength, and the decision they support.
- **Don't use** to design interviews (`interview-guide`), build the opportunity map (`opportunity-tree`), mine public reviews (`public-feedback-report`), or synthesize curated multi-channel feedback at scale (`voice-of-customer-report`).

## Files
- `SKILL.md` — the spec the agent runs from.
- `README.md` — this file.
- `examples/sprntly-interview-synthesis.html` — an illustrative worked example (17 qualitative calls → synthesis) showing the exact look.
