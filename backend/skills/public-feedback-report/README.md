# public-feedback-report

A PM-agent skill that mines **public, external feedback** about a company — across every platform where users leave it — and turns it into a clean, trended **report** of what users are saying and what to do about it.

## What it is
The **public/external** voice-of-customer report. Where `voice-of-customer-report` reads curated, first-party feedback (calls, tickets, interviews), this one reads the open web: App Store & Google Play reviews, Reddit, G2/Capterra/Trustpilot, X/Twitter, Facebook, YouTube comments, forums. Its defining feature is **change over time** — what's new, what's chronic and never fixed, and how sentiment and volume are trending.

## What it does — the report, top to bottom
1. **TL;DR (user-first)** — what users are saying now, enumerated `#1/#2/#3` in their own voice, with sentiment & volume direction and the headline recommendation.
2. **Sentiment & volume over time** — trend lines for sentiment, mention volume, and star-rating, this period vs prior, with spikes tied to releases/events.
3. **Themes** — high-level themes, each with relative volume (sampled), sentiment, a trend arrow, and 2–3 real quotes.
4. **Long-standing · New/Emerging · Improving-or-Resolved** — the temporal split, each item dated.
5. **By platform** — sentiment and top themes per platform/audience.
6. **What users love** — the praise/strength themes to protect and amplify.
7. **Competitive comparison** — sentiment over time vs your **top 2 competitors** (you pick them, or the skill reasons and chooses), **what users love about each competitor**, and a **head-to-head complaints** table (are they doing better or worse than us, per issue) + switching signals.
8. **Recommendations** — highest-impact, ranked by impact × severity × company-metric fit, presented as **action cards**: per recommendation, **Generate PRD** (→ prd-author) or **Move to backlog** (→ prioritize).
8. **Coverage & integrity** — platforms, window, sample caveats.

## Sources
**In:** App Store, Google Play, Reddit, G2/Capterra/Trustpilot/TrustRadius, X, Facebook, YouTube comments, public forums, Product Hunt / Hacker News where relevant.
**Out (use other skills):** support tickets, calls, interviews, internal feedback → `voice-of-customer-report`; one meeting → `meeting-summary`; competitor study → `competitive-intelligence-review`.

## The honest line (why it's trustworthy)
Public data is a **biased, partial sample**, so this report gives **relative share + direction, always labeled "sampled" — never fake precise counts**. Real, sourced quotes only; no root-cause guessing; sentiment is modeled and spot-checked, reported in bands not false precision; suspicious spikes (bots/astroturf) flagged.

## How it prioritizes
Recommendations are ranked by **impact (reach × trend) × severity × fit to the metrics the company tracks** — rating, installs→activation, retention, reputation — and capped at the most important ~5. Run it **monthly** for the most value: it diffs against the prior report ("what changed").

## When to use / not
- **Use** for online reputation, review mining, "what are people saying about us," public sentiment trends.
- **Don't use** when you have direct user access (`voice-of-customer-report`), for a single meeting (`meeting-summary`), or a competitor deep-dive (`competitive-intelligence-review`).

## Files
- `SKILL.md` — the spec the agent runs from.
- `README.md` — this file.
- `examples/lumio-public-feedback-report.html` — an **illustrative** worked example (sample data) showing the exact report look.
