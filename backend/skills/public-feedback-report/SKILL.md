---
name: public-feedback-report
description: Mine PUBLIC, external feedback about a company across every platform where users leave it — App Store & Google Play reviews, Reddit, G2/Capterra/Trustpilot, X/Twitter, Facebook, YouTube comments, forums — and turn it into a clean, report-style read of what users are saying, how it's trending over time, what's new, what's chronic and never getting fixed, and what to do. It tracks sentiment and volume over time, surfaces high-level themes with relative volume + sentiment + trend, splits issues into long-standing / new-and-emerging / improving-or-resolved, breaks feedback down by platform and audience, captures what users LOVE (not just complaints), flags switching-to-competitor signals, and ends in impact-ranked recommendations tied to the metrics the company cares about (rating, acquisition, retention, reputation). Because public data is a biased, partial sample, it reports RELATIVE share and direction, always labeled "sampled" — never fabricated precise counts, never invented quotes, never guessed root cause. This is the PUBLIC/external counterpart to `voice-of-customer-report`; it does NOT use calls, support tickets, interviews, or internal feedback (those are `voice-of-customer-report` / `meeting-summary`). Use when the user says "what are people saying about us online", "review mining", "app-store/Reddit/G2/Twitter feedback", "online reputation", "public sentiment", "what's trending in our reviews", or "how is sentiment changing over time".
---

# Public Feedback Report (external review & social mining → trends → recommendations)

## What it does
Reads public feedback about a company from across the open web and produces **one report-style artifact** that answers: **what are users saying · how is it trending · what's new · what's chronic and unresolved · what should we do.** It is built around **change over time** — a single snapshot is the weak version; the value is the trend.

It reads top to bottom:
1. **TL;DR (written, user-first)** — what users are saying right now in their own voice, enumerated `#1/#2/#3`, with the sentiment & volume direction and the headline recommendation.
2. **Sentiment & volume over time** — the trend lines: overall sentiment, mention volume, and average star-rating (App Store / Play / G2) this period vs prior, with spikes tied to releases/events.
3. **Themes** — high-level themes, each with **relative volume (sampled), sentiment, a trend arrow, and 2–3 real sourced quotes.**
4. **Long-standing · New/Emerging · Improving-or-Resolved** — the temporal cut: chronic issues recurring for a long time and still unresolved; what just appeared this period; what's declining (possibly fixed).
5. **By platform** — where the feedback lives and how sentiment differs by audience (Reddit power users vs App Store mass vs G2 buyers vs X).
6. **What users love** — the praise/strength themes to protect and amplify, not just the complaints.
7. **Recommendations** — the highest-impact actions, ranked by **impact × severity × fit to the company's metrics**, each routed (→ `prd-author` / `competitive-intelligence-review` / marketing).
8. **Coverage & integrity** — platforms, window, sample caveats.

## Sources — public / external only
**In scope:** App Store & Google Play reviews · Reddit · G2 / Capterra / Trustpilot / TrustRadius · X (Twitter) · Facebook · YouTube comments · public forums & communities · Hacker News / Product Hunt where relevant. Any platform where users leave **public** feedback.
**Out of scope (different skills):** support tickets, sales/CSM calls, user interviews, NPS/CSAT you collected, in-product feedback → those are curated/first-party = **`voice-of-customer-report`**. A single meeting → `meeting-summary`. A competitor study → `competitive-intelligence-review`.

## The discipline (non-negotiable)
- **Public = a biased, partial sample.** Report **relative frequency / share of mentions and direction**, always **labeled "sampled."** **Never present a fake precise count** ("240 complaints") from public data — you don't own the denominator.
- **Real quotes only**, sourced and platform-attributed — never invented, never a paraphrase in quotation marks.
- **No root-cause guessing** — report what users say, not a guessed why.
- **Sentiment is modeled, not certain** — state the method; spot-check; don't over-trust a percentage to the decimal.
- **Trends need a volume floor** — don't call a 3→5 mention move a "spike"; normalize (mentions/week) before claiming a trend.
- **Strengths count** — surface praise, not only problems.

## Method
0. **Scope:** the company (+ product names, handles, app IDs, G2 page), the **platforms** to cover, the **window + prior window** for trend, the **goal & metrics the company tracks** (rating, installs→activation, retention, reputation), and any prior run (for "what changed"). **Competitors:** either the user names them, or the skill **reasons and picks the top 2** — the rivals most mentioned in the corpus, the closest substitutes, and the names that show up in "switched to ___" mentions. State which 2 and why they were chosen.
1. **Collect** mentions per platform across the window (and prior window). Capture verbatim · date · platform · rating (if any) · author-type if visible. De-dupe cross-posts; state the rule.
2. **Classify** each mention: theme, sentiment (pos/neg/neutral) + intensity, type (bug / UX / pricing / performance / feature-request / support / praise / churn-or-switching signal), and platform.
3. **Trend over time:** mentions/week and sentiment by period; **average rating trend** per store; detect spikes/anomalies and tie them to releases or events. Compare to the prior window.
4. **Theme map:** every theme with **relative share (sampled)**, net sentiment, **trend arrow** (rising/flat/falling), first-seen → last-seen, and 2–3 real sourced quotes.
5. **Temporal classification:** **Long-standing** (recurring across many periods, still live) · **New/Emerging** (appeared this window) · **Improving/Resolved** (declining; cross-check changelog if available) · **No-longer-raised** (used to appear regularly but is now absent — fixed or aged out; show what it was and roughly when it disappeared). The "gone" list matters: it's proof of progress and stops old issues haunting the narrative.
6. **Platform & audience cut:** sentiment and top themes per platform; note where the audiences differ (e.g., Reddit churn rage vs G2 buyer concerns).
7. **Strengths:** the praise themes — what users love about **us** — to protect/amplify.
8. **Competitive comparison (us vs the top 2):**
   - **Sentiment over time, us vs each competitor** — overlay the trend so the reader sees if we're pulling ahead or falling behind.
   - **What users love about each competitor** — their praised strengths, side by side with ours, so our real differentiator (and their pull) is explicit.
   - **Complaints head-to-head** — for the key dimensions (e.g. reliability, pricing/value, support, UX, integrations), is each competitor doing **better or worse than us** in public sentiment, and who's ahead. Plus **switching signals** — "switched to ___", "___ does this better" — as a churn-intent and share-of-voice read.
   Competitor data is **sampled** too — same honesty bar; never fabricate their counts or quotes.
9. **Recommend:** the highest-impact actions, ranked by **impact (reach × trend) × severity × fit to the company's metrics**; each names the metric/outcome it moves (rating, acquisition/conversion, retention, reputation) and routes onward. Cap at the most important ~5.
10. **Coverage & integrity:** platforms covered, window, approximate sample size **labeled sampled**, locales/languages, what was unavailable, sentiment-model caveat.

## Output spec (report-style — Word-document feel)
**A. Title + run line** — company · platforms · window (+ prior) · **the 2 competitors & how they were chosen** · goal & metrics · sample note (sampled) · confidence.
**B. TL;DR (written, user-first)** — the user voice first, findings enumerated `#1/#2/#3` each on its own line, the sentiment & volume direction, and the headline recommendation last.
**C. Sentiment & volume over time — us vs the top 2 competitors** — overlaid trend (our sentiment, each competitor's sentiment, and our volume), period vs prior, spikes annotated, so "are we pulling ahead or falling behind" is answered at a glance.
**D. Themes** — cards/table: theme · relative share (sampled) · net sentiment · trend arrow · 2–3 real quotes.
**E. Long-standing · New/Emerging · No-longer-raised** — clearly separated lists, each item dated (first→last seen). The "no-longer-raised / resolved" list is explicit — what used to be complained about and has now gone quiet.
**F. By platform** — per-platform sentiment + top theme + a one-line audience read.
**G. How we compare vs [Competitor A] & [Competitor B]** — the competitive block, together:
   1. **What users love — us vs each competitor** (strengths side by side).
   2. **Complaints head-to-head** — per dimension (reliability, pricing/value, support, UX, integrations…): our sentiment vs each competitor's, and **who's ahead / are they doing better or worse**.
   3. **Switching signal** — share-of-voice and "switched to ___" mentions.
**H. Recommendations** — the most important ~5, ranked by impact × severity × company-metric fit, **informed by the competitive gaps** (close where a rival is beating us; defend/amplify where we lead). Each is an **action card** naming the metric it moves and offering two actions: **Generate PRD** (route to `prd-author`) or **Move to backlog** (route to `prioritize`). Items sent to backlog drop into a backlog list; PRD-requested items are marked and handed off.
**I. Coverage & integrity** — platforms, window, sample caveat (sampled, biased — for us *and* competitors), locales, sentiment-model note, verified-vs-inferred.

Render as a clean, **report / Word-document-style** artifact. A worked example ships at `examples/lumio-public-feedback-report.html` — reference it for the exact look (trend vs competitors, comparison block, action-card recommendations).

## What makes this complete (the pieces often missed)
- **Trend over time, not a snapshot** — sentiment, volume, AND rating, vs the prior period.
- **Sentiment vs the top 2 competitors over time** — are we pulling ahead or falling behind, not just our own line.
- **Competitor strengths & head-to-head complaints** — what users love about *them*, and which issues they handle better or worse than us — so the report drives positioning, not just bug-fixing.
- **Spike/anomaly detection tied to releases/events** (a price change or a bad update shows up here).
- **Temporal split incl. "no-longer-raised"** — long-standing vs new vs resolved/gone, so chronic issues can't hide and progress is visible.
- **Per-platform/audience segmentation** — the same product reads differently on Reddit vs G2.
- **Strengths/praise**, not only complaints — what to protect and put in marketing.
- **Switching & competitor mentions** — churn-intent and share-of-voice.
- **Sampling honesty** — relative + sampled, never fake counts; the credibility of the whole report rests on this.
- **Recurring cadence / "what changed since last report"** — the report is most valuable run monthly with diffs.
- **Recommendations tied to the metrics the company tracks** and to the competitive gaps, ranked by impact × severity.

## Competitor selection
The 2 competitors are either **named by the user** or **reasoned by the skill** — picked from who's most mentioned alongside the company, the closest substitutes, and the names in "switched to ___" mentions. The report always states which 2 and why, and treats their public data with the same sampling honesty as ours.

## Relationship to neighbors
- **`voice-of-customer-report`** — the **curated/first-party** counterpart (calls, tickets, interviews, internal feedback, with user data). Use it when you have direct access to the user; use **this** for public/anonymous channels.
- **`feedback-synthesis`** — a quick thematic pass over a small pile; this is the exhaustive, trended, public-reputation report.
- **`competitive-intelligence-review`** — competitor mentions found here can feed it.

## Quality checklist (the bar)
- [ ] **Public/external sources only**; calls/tickets/interviews excluded.
- [ ] **Relative share + direction, labeled "sampled"** — no fabricated precise counts.
- [ ] **Trends over time**: sentiment + volume + rating, this period vs prior, spikes annotated.
- [ ] **Long-standing / new-emerging / no-longer-raised** split, each item dated (the "gone" list is explicit).
- [ ] Themes carry **relative volume + sentiment + trend + 2–3 real sourced quotes**.
- [ ] **Per-platform** sentiment & audience read included.
- [ ] **Competitive block present:** sentiment over time vs the **top 2 competitors** (named + why chosen), **what users love about each competitor**, and a **head-to-head complaints** read (better/worse per dimension) + switching signals.
- [ ] **Strengths/praise** surfaced for us; **switching/competitor** signals flagged.
- [ ] **Recommendations ranked by impact × severity × company-metric fit**, informed by competitive gaps, capped ~5, each with a metric + route.
- [ ] **User-first written TL;DR** with `#1/#2/#3` and the headline recommendation.
- [ ] **No invented quotes, no root-cause guessing**; sentiment-model + sampling caveats stated.

## Known gaps / limitations
- Public data is a **biased sample** — vocal, skewed to the unhappy and the highly-engaged; it shows *direction and themes*, not true prevalence. Stated, never hidden.
- Sentiment models err on sarcasm/mixed posts — spot-check; report bands, not false precision.
- Bots/astroturf/review manipulation exist — flag suspicious spikes; don't treat raw volume as truth.
- Coverage varies by platform API/access; gaps in collection become gaps in the report — state them.
