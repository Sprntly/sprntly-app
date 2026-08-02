# voice-of-customer-report

A PM-agent skill that turns **curated, direct-access customer feedback** into a clean, shareable **report** — from voice of customer straight to recommendations, prioritized against the team's own goals.

## What it is
One skill that does the whole job: synthesize what customers are saying, read how much it hurts and how loudly, size how much it matters against the metrics the company tracks, and end in a short list of what to do. It replaces two older skills (`third-party-feedback` + `voc-volume-severity`) with a single report-style output.

## What it does — the report, top to bottom
1. **Title + date range.** Titled "Voice of Customer Report" with the explicit window it covers (`6 January 2026 – 30 June 2026`), plus a scope line naming the sources, accounts and any filters applied.
2. **Written TL;DR (problems first).** Names the source, then enumerates the findings **`#1` / `#2` / `#3`, each on its own line and each written as a problem** — who is stuck, with what, and why they can't fix it themselves — before any business framing.
3. **Problems at a glance** — a table: problem · accounts · **frustration (1–5)** · **how they sound** · **metric impacted**. The metric column names which tracked goal metric each problem sits on, and says "none identified" where none is — which is what justifies leaving a theme out of the recommendations.
4. **Volume vs frustration** — a radar chart overlaying reach and heat, so it's immediately visible which problems are wide-but-calm and which are small-but-furious.
5. **Themes** — a card grid (2–3 per row); each theme sized, described, impact-quantified, tone-scored, and backed by **2–3 real quotes**.
6. **Recommendations** — the most important ~5, **selected by fit to the team's goals**, plus an explicit **deliberately-not-recommended** list so the filter is auditable.

## It scopes itself to the request
Ask for a slice and you get that slice, with the denominator re-derived and the applied scope stated in the report:
- **By window** — "last week", "last month", "last quarter", "March 1 to June 30". No window given → last full quarter, and it says which default it chose.
- **By source** — "just the CSM calls", "only the tickets".
- **By account or segment** — "only enterprise", "just Northwind", "only churned accounts" (with the survivorship skew flagged).

## Sources it takes (curated, direct-access)
User & churn/exit interviews · recorded customer/CSM calls + notes/transcripts · support tickets & complaint logs you own · internal feedback collection (in-product feedback, surveys, NPS/CSAT you ran) · your own sales/success notes. Plus, optionally, **user data to size impact**: spend/ARR, seat & usage, churn status, segment, plan tier.

## Sources it does NOT take (out of scope)
Public app-store / Play reviews · Twitter/X · Reddit / forums · public review sites (G2, Trustpilot, Capterra) · social listening · any scraped, anonymous, or volume-based public channel. Those are review-mining — a different job.

## How it reads sentiment
Every theme carries a **1–5 frustration score** and a short plain-language tone read ("weary, resigned"; "exposed, defensive"; "mild, wishful"). Both are derived from the language customers actually used — escalation words, blame or cancellation framing, repeat contacts, whether they describe having given up. It is an observable read, not a business-impact measure, and the report says so: it is analyst-assigned and two readers might differ by a point.

## How it prioritizes (goal-aware + adaptive)
- **Goal-aware:** it pulls the team's goals and tracked metrics and **selects recommendations by how much they move those metrics** — a wide theme that moves no goal metric is named but not promoted; a quiet theme that moves the North Star is elevated.
- **Adaptive basis** (internal, not printed): **volume only** → rank by volume × frustration; **+ behavior** (churn/usage) → triangulate, surface silent killers & vocal minorities; **+ commercial** (spend/ARR) → impact-size in dollars and rank by business impact. Never fabricates a signal to climb a tier. If the run is volume-only, that limitation is stated in one line under the recommendations.

## Guardrails
Real quotes only (never invented or overstated) · counts only with a denominator, re-derived inside the applied scope · frustration read from real language, never invented · no root-cause guessing · rank by goal impact, not raw volume or raw heat.

## When to use / not
- **Use** to turn calls/tickets/interviews/internal feedback into a decision-ready report, for any window, source or account slice.
- **Don't use** for public review/social mining (`feedback-synthesis` or a review-miner), a single meeting's minutes (`meeting-summary`), or a competitor study (`competitive-intelligence-review`).

## Files
- `SKILL.md` — the spec the agent runs from.
- `README.md` — this file.
- `examples/kindling-voc-report.html` — a worked example the skill references for the exact look.

## Vendored in Sprntly

v3 — 2026-07-23. Two-stage capture→report rewrite: origin-tier counting
(`references/CAPTURE.md`, injected into the prompt), request scoping with an
"Asked:" line, frustration 1–5 + tone reads, volume-vs-frustration radar,
goal-fit recommendations with a required deliberately-not-recommended block.
`app/voc_report.py` pins the report design as a deterministic template (model
emits data only); `examples/` are the design reference for maintainers and are
not injected into the prompt.
