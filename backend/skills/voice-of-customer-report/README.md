# voice-of-customer-report

A PM-agent skill that turns **curated, direct-access customer feedback** into a clean, shareable **report** — from voice of customer straight to recommendations, prioritized against the team's own goals.

## What it is
One skill that does the whole job: synthesize what customers are saying, size how much it matters against the metrics the company tracks, and end in a short list of what to do. It replaces two older skills (`third-party-feedback` + `voc-volume-severity`) with a single report-style output.

## What it does — the report, top to bottom
1. **Written TL;DR (user first).** Names the source ("synthesized from 17 CSM calls across ~15 accounts last quarter"), then enumerates the findings **`#1` / `#2` / `#3`, each on its own line, in the customer's voice** — before any business framing. The user comes first; that's the point.
2. **Problems at a glance** — a table: problem · volume · churn · $ at risk · **the company metric it moves** · severity.
3. **Themes** — a card grid (2–3 per row); each theme sized, described, impact-quantified, and backed by **2–3 real quotes**.
4. **Recommendations** — the most important ~5, **selected by fit to the team's goals**, each naming the goal metric it moves and routing to `prd-author`.

## Sources it takes (curated, direct-access)
User & churn/exit interviews · recorded customer/CSM calls + notes/transcripts · support tickets & complaint logs you own · internal feedback collection (in-product feedback, surveys, NPS/CSAT you ran) · your own sales/success notes. Plus, optionally, **user data to size impact**: spend/ARR, seat & usage, churn status, segment, plan tier. Any one, or a combination.

## Sources it does NOT take (out of scope)
Public app-store / Play reviews · Twitter/X · Reddit / forums · public review sites (G2, Trustpilot, Capterra) · social listening · any scraped, anonymous, or volume-based public channel. Those are review-mining — a different job. This skill keeps the voice curated and the impact real.

## How it prioritizes (goal-aware + adaptive)
- **Goal-aware:** it pulls the team's goals and tracked metrics and **selects recommendations by how much they move those metrics** — a loud theme that moves no goal metric is named but not promoted; a quiet theme that moves the North Star is elevated.
- **Adaptive basis** (declared in the run line): **volume only** → rank by volume × frustration; **+ behavior** (churn/usage) → triangulate, surface silent killers & vocal minorities; **+ commercial** (spend/ARR) → impact-size in dollars and rank by business impact. Never fabricates a signal to climb a tier — missing data is labeled.

## Guardrails
Real quotes only (never invented or overstated) · counts only with a denominator · no root-cause guessing · rank by goal impact not raw volume · declare the recommendation basis.

## When to use / not
- **Use** to turn calls/tickets/interviews/internal feedback into a decision-ready report.
- **Don't use** for public review/social mining (`feedback-synthesis` or a review-miner), a single meeting's minutes (`meeting-summary`), or a competitor study (`competitive-intelligence-review`).

## Files
- `SKILL.md` — the spec the agent runs from.
- `README.md` — this file.
- `examples/sprntly-voc-report.html` — a worked example (17 CSM calls → report) the skill references for the exact look.
