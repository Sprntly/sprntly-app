# public-feedback-report

Mines **public feedback** about a company from wherever people post it, and turns it into a report a product team can act on — plus a queryable record of everything found.

Built by **Sprntly**.

## What makes it different

Most review-mining output is a mood board: 60% of it is pricing gripes and support rage, and a PM reading it cannot act on any of it. This skill fixes that with two rules.

**1. It separates product feedback from everything else, and only analyses the product half.**
Every piece of feedback is tagged: could a product, design or engineering team change this? Only those drive the analysis and the recommendations. Pricing, billing, support, sales, marketing and company feedback is still captured, still counted, still shown — in its own short section, with the team that owns it. It never crowds the build list, and its true proportion is always stated.

**2. It never invents a number.**
Every percentage is computed over the feedback actually collected, and the report says so in plain words: *"these percentages are out of the 52 posts we found, not out of our users."* No share-of-mention figures, no fabricated trend lines, no counting someone as leaving because they sounded annoyed.

## How it runs — two passes

**Pass 1 — Capture.** Every distinct piece of feedback becomes one record: the quote, where it came from, when it was posted, whether it is product or not, who owns it, how the person felt, and whether they said they were leaving. Nothing is merged or deduplicated — three people saying the same thing is three records, because how often something comes up is the whole point. Output: `<company>-feedback-records.json`.

**Pass 2 — Analyse.** The report is built over the product records only. Output: a self-contained HTML report.

## What the report contains

1. **Count strip** — how much feedback, how much is product-actionable, how many sources, how many said they are leaving.
2. **TL;DR, five points** — the three biggest problems, plus what people are actually leaving over and what is brand new this period.
3. **24 months of volume and sentiment** — green, amber, red, with the months we found nothing marked as gaps rather than smoothed over.
4. **The problems people are running into** — written in their voice, with a plain gloss naming the fix and the owner.
5. **What's new · what's stuck · what's fixed** — in that order, beside the feedback mix.
6. **By platform** — what surfaces where, and why the audiences differ.
7. **How we compare** — external ratings, what users love about each rival, where we win and lose, and who said they are leaving.
8. **Recommendations** — product-actionable only, each led by its user problem.
9. **Also worth a look — not for the product team** — the top 3 non-product items and who owns them.
10. **Next steps** — one shared block: use Sprntly to turn these into a PRD, or add them to your backlog.
11. **Coverage note** — collapsed by default, plus a machine-readable metadata block.

## Asking it questions

The report carries a metadata block, and the record file sits next to it, so the skill answers follow-ups without re-running:

- "What's the feedback from the App Store?"
- "How long has the flagging problem been raised?"
- "Show me everything from March 2026."
- "What's new since last time?" · "What have people stopped complaining about?"
- "Why are people leaving?" · "What do people actually like?"
- "How many complained about X?"

Answers come from the captured records only. If a source returned nothing, it says so rather than letting silence read as satisfaction. Counts are always framed as posts found, never as a share of users.

## When to use / when not

**Use it** for online reputation, review mining, "what are people saying about us," public sentiment trends, or any follow-up question about feedback already captured.

**Don't use it** when you have direct access to users — that's `voice-of-customer-report` (calls, tickets, interviews). For a single meeting, `meeting-summary`. For a competitor deep-dive, `competitive-intelligence-review`.

## Sources

Unbounded by design. App stores and marketplaces · Reddit and niche forums · review sites · X, YouTube, TikTok, LinkedIn · Discord and community platforms · GitHub and developer forums · blogs, newsletters, podcasts, press · comparison and alternatives sites · comment sections anywhere. A source is never rejected for being low quality — it is recorded and read in light of what it is.

## Routes to

`prd-author` for the items worth specifying · `prioritize` for the backlog set · `competitive-intelligence-review` for rival findings. Non-product items route to the teams that own them, not to the backlog.

## Files

| File | What it is |
|---|---|
| `SKILL.md` | The spec the agent runs from |
| `README.md` | This file |
| `references/capture-spec.md` | Governs pass 1 — the capture rules and record shape |
| `references/query-guide.md` | How to answer follow-up questions from the record set |
| `examples/strava-public-feedback-report.html` | A real worked run on Strava, July 2026 |
| `examples/strava-feedback-records.json` | The 52 records that report was built from |
