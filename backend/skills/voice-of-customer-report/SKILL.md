---
name: voice-of-customer-report
description: Turn CURATED, direct-access customer feedback — user interviews, recorded customer/CSM calls and their notes, support tickets and complaint logs, internal feedback collection (in-product feedback, surveys/NPS you ran), and any user data you hold (spend, usage, churn, segment) — into one clean, report-style artifact that goes straight from voice of customer to recommendations. It reads like a document: a written TL;DR that names the source ("synthesized from 17 calls with ~15 accounts last quarter") and describes the findings in prose; a themes section laid out as cards (2–3 per row), each sized, described, impact-quantified, and backed by 2–3 strong real quotes; and a short list of recommendations. It is for feedback where you have DIRECT ACCESS to the user — it deliberately EXCLUDES public, anonymous, volume-based channels (app-store / Play reviews, Twitter/X, Reddit, public review sites, social listening). It never invents counts or quotes, never overstates the voice, never guesses root cause, and adapts how it ranks to the data on hand. Use when the user says "synthesize these calls / tickets / interviews", "voice of customer", "what are the themes", "what should we build", or pastes curated first-party feedback.
---

# Voice of Customer Report

## What it does
Takes curated, first-party feedback and produces **one report-style artifact**, read top to bottom:
1. **TL;DR (written).** A short prose opening that (a) **names the source and method** — "synthesized from 17 recorded CSM calls across ~15 accounts, Apr–Jun 2026" — and (b) **describes and enumerates the findings in a paragraph**, so a reader gets the whole story before any visuals.
2. **Themes — what we're seeing.** The findings as a **grid of cards (2–3 per row)**. Each theme: a **size** (how many / what share), a plain description, an **impact read** sized by whatever user data exists (accounts, % of base, % of high-spend users, $ at risk, churn), and **2–3 strong, real quotes** that make the pain vivid.
3. **Recommendations.** The most important actions (**max ~5**) — do X, Y, Z — each tied to the metric it moves and a route (→ `prd-author`). No separate deep-dive: themes lead straight here.

It answers, in order: **where did this come from · what are users saying · what's the impact · what should we do.** And it **adapts how it ranks to the data on hand** (see *Recommendation basis*).

## Sources — curated, direct-access only
**Built for (any one, or a combination):**
- **User / customer interviews** — discovery, problem, solution, win/loss.
- **Recorded customer & CSM calls + their notes/transcripts** — check-ins, QBRs, save calls, expansion calls (e.g. Fireflies notes).
- **Churn / exit interviews.**
- **Support tickets & complaint logs you own** — Zendesk, Intercom, Help Scout.
- **Internal feedback collection** — in-product feedback, feedback forms, surveys and NPS/CSAT verbatims you ran.
- **Your own sales / success call notes.**
- **Optional — user data you hold, to size impact:** spend / ARR, seat & usage data, churn status, account segment, plan tier.

**Out of scope — deliberately NOT this skill:**
- Public **app-store / Play Store** reviews.
- **Twitter/X, Reddit**, forums, social listening.
- Public review sites (**G2, Trustpilot, Capterra**) and any **scraped, anonymous, or volume-based public channel**.

> Why the line: this skill is for feedback where you **have direct access to the user** and can attach who said it and what they're worth. Anonymous public volume is a different job (review-mining) — not handled here, so the voice stays curated and the impact stays real.

## The discipline (non-negotiable)
- **Voice only when real.** Quotes are real, sourced, verbatim — never invented, never a paraphrase in quotation marks, never overstated. If a theme lacks a strong quote, say so.
- **Counts only with a denominator.** Sizes are real (you own the corpus); never a count without a denominator.
- **No root-cause guessing.** Report what customers say, not a guessed "why."
- **Rank by impact, not raw volume.** A quiet, costly theme can outrank a loud, cheap one — show why.
- **Adapt, don't fabricate.** Use the richest data you actually have; label what's missing rather than invent it.

## Method
0. **Declare source · N · window · the team's goals & metrics · data on hand.** Which curated sources, how many items/accounts, the window. **Pull the team's goals and the metrics they actually prioritize on** — the North Star and the company's tracked metrics (e.g. activation rate, gross/net retention, expansion/NRR, time-to-value, engagement) from `business-context` / the stated goal. Note which signals exist (volume always; frustration from language; behavior = churn/usage if you hold it; commercial = spend/ARR/value if you hold it). This sets both the *recommendation basis* and the *goal lens* recommendations are selected against.
1. **Normalize every item** (verbatim · date · source · account/segment · sentiment). De-dupe; state the rule.
2. **Group into themes & enumerate exhaustively** (a theme = one thing users raise), with classification-coverage %. Could be 2, could be 10+.
3. **Size each theme** — count / accounts / share of feedback.
4. **Impact-size each theme against the metrics the company cares about.** Beyond churn / volume / revenue, map each theme to the **team's own tracked metrics** — which goal metric it moves and by roughly how much: accounts & % of base; % of highest-spend users / $ at risk (if you hold billing); churn link (if you hold it); and the company metric in play (e.g. "drags team-activation rate," "threatens gross retention," "gates net-revenue-retention / expansion"). **Label any missing signal — never invent.**
5. **Pull 2–3 strong real quotes per theme** that make the pain vivid and convey the frustration. Gaps flagged, never fabricated.
6. **Triangulate for ranking** — Voice (volume × frustration) vs Behavior (churn/usage) vs Commercial (value × goal-fit), scored independently; flag silent killers (quiet but costly) and vocal minorities (loud but low-impact); never average a divergence away.
7. **Choose the recommendation basis adaptively** (below) and rank.
8. **Select the most important ~5 by GOAL FIT, not raw volume.** Recommendations are chosen for how much they move the metrics this team prioritizes — a loud theme that doesn't advance a goal metric is named but **not** auto-promoted; a quieter theme that directly moves the North Star is **elevated**. Each recommendation states the goal metric it moves and routes to `prd-author`.

## Output spec (report-style — locked order: written TL;DR → at-a-glance table → themes → recommendations)
**A. Title + run line** — report title · sources · N · window · **the team's goal & metrics** · **recommendation basis** · confidence.
**B. TL;DR (written, prose — user first).** Lead with the **user's problems in their own voice** — what customers are experiencing — *before* any business framing. Open with a source/method sentence, then **enumerate the key findings with `#1`, `#2`, `#3` each starting on its own line** (the user problem + a touch of voice). **Only after** the user problems, a short "what this means for us" close (what we're losing/gaining). The customer comes first — that's the point of the report.
**C. Problems at a glance — table.** Every problem in one scannable table: problem · volume (accounts/share) · churn · $ at risk · **the company metric it moves** · severity. The quick index before the detailed cards.
**D. Themes — cards, 2–3 per row.** Every theme (long tail included): **size** · plain description · **impact read** (sized against the company's metrics; missing signals labeled) · **2–3 strong real quotes**. Ranked by goal impact. Minor one-offs grouped compactly.
**E. Recommendations.** The **most important ~5, selected by goal fit**, each = action + **the goal metric it moves** + route (→ `prd-author`). End in the single next step.
**F. Sources & integrity note.** Which curated sources were used, what data was/wasn't on hand, verified-vs-inferred, and — if volume-only — that the ranking would change with spend/churn data.

Render as a clean, **report / Word-document-style** artifact (generous margins, prose TL;DR, sectioned, easy to read and share). Fall back to structured Markdown if no rich surface. A worked example ships in `examples/sprntly-voc-report.html` — reference it for the exact look.

## Recommendation basis (adaptive)
Rank by the **richest data actually on hand**, and say so in the run line:
- **Tier 1 — volume only (just feedback).** Rank themes by **volume × frustration**; state "ranked on how often & how angrily it comes up; add usage/billing to weight by real impact."
- **Tier 2 — + behavior (churn / usage held).** Triangulate voice vs behavior; surface **silent killers** and **vocal minorities**.
- **Tier 3 — + commercial (spend / ARR / value held).** **Impact-size** each theme ($ at risk, % of highest-spend users) and rank by **business impact against the goal**.
Use the highest tier the data supports, theme by theme; never fabricate a signal to climb a tier; the run line states which tier the recommendation rests on.

## Quality checklist (the bar)
- [ ] **Reads as a report: written TL;DR → at-a-glance table → themes cards → recommendations.**
- [ ] TL;DR is **prose, user/voice FIRST** (before any business framing), names the source/method, and **enumerates findings with `#1`/`#2`/`#3` each on its own line.**
- [ ] **Problems-at-a-glance table** present — problem · volume · churn · $ · **company metric** · severity.
- [ ] Themes are **cards (2–3 per row)**, each with **size + description + impact read + 2–3 strong real quotes**.
- [ ] **Impact sized against the metrics the company cares about** (not just churn/volume/revenue); missing signals labeled, never invented.
- [ ] **Recommendations selected by GOAL FIT** (not raw volume), capped at ~5, each naming the goal metric it moves + a route.
- [ ] **Recommendation basis declared** and matches the data on hand.
- [ ] **Sources curated/direct-access only**; public volume channels excluded.
- [ ] **Voice never fabricated or overstated; no counts without a denominator; no root-cause guessing.**

## Merges / replaces
Absorbs the old `third-party-feedback` (exhaustive honest counting + real quotes) and `voc-volume-severity` (voice-vs-behavior-vs-commercial triangulation, silent-killer flags) into one curated-feedback → recommendations report. (The lighter `feedback-synthesis` remains for quick thematic passes.)

## Known gaps / limitations
- Only as complete as the curated corpus — gaps in what you collected become gaps here; stated, not hidden.
- Correlation ≠ causation: a churn-linked theme isn't proven to cause churn — flagged; an experiment settles it.
- Survivorship: feedback misses users who already left over a problem — trend/exit signals partially cover this.
- Triangulation weights are judgment; the decomposition makes them debatable, not objective.
