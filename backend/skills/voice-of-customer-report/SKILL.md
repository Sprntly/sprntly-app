---
name: voice-of-customer-report
description: Turn CURATED, direct-access customer feedback — user interviews, recorded customer/CSM calls and their notes, support tickets and complaint logs, internal feedback collection (in-product feedback, surveys/NPS you ran), and any user data you hold (spend, usage, churn, segment) — into one clean, report-style artifact that goes straight from voice of customer to recommendations. It reads like a document, titled "Voice of Customer Report" with the exact date range it covers: a written TL;DR that names the source and states the findings AS PROBLEMS the customer can't solve; a problems-at-a-glance table carrying volume, a 1–5 frustration score and a plain-language read of how customers sound; a volume-vs-frustration radar chart; themes as cards (2–3 per row) each sized, described, impact-quantified and backed by 2–3 strong real quotes; and a short list of goal-fit recommendations. It SCOPES ITSELF TO THE REQUEST — honoring any window ("last week", "last month", "Mar 1 to Jun 30"), source filter ("just the CSM calls"), or account/segment filter ("only enterprise", "just Acme") the user asks for, and echoing the applied scope in the report. It is for feedback where you have DIRECT ACCESS to the user — it deliberately EXCLUDES public, anonymous, volume-based channels (app-store / Play reviews, Twitter/X, Reddit, public review sites, social listening). It runs a CAPTURE STAGE first (see CAPTURE.md) that reads every source, judges whether each artifact is a customer or internal discussion, records one record per mention with an origin tier, and decides what is even eligible to enter the report — so mixed, messy, multi-source input is filtered on evidence quality before any counting happens. It never invents counts or quotes, never overstates the voice, never guesses root cause, and adapts how it ranks to the data on hand. Use when the user says "synthesize these calls / tickets / interviews", "voice of customer", "VoC report for last quarter", "what are the themes", "what should we build", or pastes curated first-party feedback.
---

# Voice of Customer Report

## What it does
Takes curated, first-party feedback and produces **one report-style artifact**, read top to bottom:
1. **Title + scope.** Titled **"Voice of Customer Report"** with the **explicit date range** it covers, plus a run line stating which sources, accounts and window were included — including any filter the user asked for.
2. **TL;DR (written).** Prose. Names the source and method, then states the findings **as problems** — what the customer is stuck with and can't fix themselves — before any business framing.
3. **Problems at a glance.** One table: problem · accounts · **frustration (1–5)** · **how they sound** (the sentiment read) · **metric impacted**.
4. **Volume vs frustration.** A radar chart overlaying the two, so it's visible at a glance which problems are wide-but-calm and which are small-but-furious.
5. **Themes.** A grid of cards (2–3 per row), each sized, described, impact-read, and backed by 2–3 strong real quotes.
6. **Recommendations.** The most important ~5, selected by goal fit, plus an explicit **deliberately-not-recommended** list.

It answers, in order: **what period is this · what are users stuck with · how much does it hurt and how loudly · what's the impact · what should we do.**

## Two stages — capture, then report
This skill runs in two stages and **the order is not negotiable.**

**Stage 1 — Capture (`CAPTURE.md`).** Read every source in full. For each artifact: judge what kind it is and whether it is a customer or internal discussion (from content, never from file names or invite titles). For each mention: write **one record** — verbatim, normalized statement, type, origin tier, account, intensity, stated business risk, owning team. Never merge, never count, never rank. Anything not captured gets a `reason_code`. Output is a flat, individually attributed record list with no summary.

**Stage 2 — Report (this document).** Consume that record list: group into themes, count in accounts, score frustration, size impact, rank, recommend.

**Do not let analysis leak backward into capture.** A record's value is not a capture-time decision. If you are weighing importance while still reading a transcript, stop and finish capturing.

### What Stage 1 buys the report
- **Mixed sources become comparable.** A ticket, a QBR transcript and an internal Slack summary all reduce to the same record shape, so a report can span them without one format's conventions dominating.
- **Internal artifacts become usable.** A colleague writing "Acme said SSO is blocking them" is real evidence about Acme — captured as `relayed` and **attributed to Acme, not the colleague.**
- **Manufactured demand gets caught.** `asserted` claims (a rep saying "customers keep asking for this" with no named source) and `speculative` ones do **not** count toward theme sizes. `elicited` claims — answers to our own leading questions — count but are flagged.
- **Repetition survives.** One record per mention means the same complaint from one account five times is five records, deduped to one account **at report time**, so themes are sized in accounts and no single loud customer can inflate a count.
- **Discoverability stays distinct from feature request.** "I couldn't find a way to export" is tagged `discoverability`. Scoping it as a missing feature is the most common way a roadmap fills with things that already exist.
- **Ownership is pre-tagged.** A theme owned by `pricing`, `enablement` or `marketing` is reported but routed out of the product recommendations rather than competing for a roadmap slot.

### The counting rule (applies to every count in the report)
| Origin tier | Counts toward theme size | Quotable |
|---|---|---|
| `direct` · `relayed` | Yes | Yes |
| `elicited` | Yes — flagged as prompted | Yes, marked prompted |
| `asserted` · `speculative` · `undetermined` | **No** | No |

Excluded records are not deleted and their exclusion is **disclosed**: the run line states **records captured vs counted**, and a note beneath the at-a-glance table gives the reason breakdown. A theme surviving only on `asserted` records is reported as an internal belief with no customer evidence behind it — a finding in itself, not a gap.

## Scoping — the skill obeys the request
Before anything else, read what the user actually asked for and **scope the run to it**. Then state the applied scope in the report's run line so the reader knows what they're looking at (and, just as importantly, what they're not).

| The user says | The skill does |
|---|---|
| "last week" / "last month" / "last quarter" / "H1" | Resolve to concrete dates against today's date; title and run line carry the resolved range. |
| "from March 1 to June 30" | Use exactly that window. |
| *(no window given)* | Default to the last full quarter, or the natural span of the supplied corpus — and **say which default was chosen**. |
| "just the CSM calls" / "only the tickets" | Restrict to those sources; name the excluded sources in the run line. |
| "only enterprise accounts" / "just Northwind" | Restrict to those accounts/segments; state the reduced denominator. |
| "only churned accounts" | Restrict to that status; flag the resulting survivorship skew explicitly. |
| Filters that leave too little to analyze | Run anyway, state the sample is small, and lower the confidence of every ranking claim rather than refusing. |

**Scope changes the denominator, so it changes every percentage.** Re-derive counts, shares and the frustration ratings inside the filtered set — never carry over figures from an unfiltered run.

## Sources — curated, direct-access only
**Built for (any one, or a combination):**
- **User / customer interviews** — discovery, problem, solution, win/loss.
- **Recorded customer & CSM calls + their notes/transcripts** — check-ins, QBRs, save calls, expansion calls.
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
- **Counts only with a denominator.** Sizes are real (you own the corpus); never a count without a denominator, and the denominator reflects the applied scope.
- **Sentiment is read, not invented.** Frustration scores and tone descriptions come from the language customers actually used, and the report says so.
- **No root-cause guessing.** Report what customers say, not a guessed "why."
- **Rank by impact, not raw volume.** A quiet, costly theme can outrank a loud, cheap one — show why.
- **Adapt, don't fabricate.** Use the richest data you actually have; label what's missing rather than invent it.

## Method
0a. **Capture** — run `CAPTURE.md` over every supplied source before any analysis. Produce the flat record list, including the not-captured list with reason codes.
0b. **Scope the run** (see *Scoping* above) — resolve the window to concrete dates, apply any source/account/segment filter, and fix the denominator inside that scope. Then declare source · N · window · the team's goals & metrics · data on hand. **Pull the team's goals and the metrics they actually prioritize on** — the North Star and tracked metrics (activation, gross/net retention, expansion/NRR, time-to-value, engagement) from `business-context` / the stated goal. Note which signals exist: volume always; frustration & sentiment from language; behavior = churn/usage if held; commercial = spend/ARR if held.
1. **Normalize every item** (verbatim · date · source · account/segment · sentiment). De-dupe; state the rule.
2. **Group into themes & enumerate exhaustively** (a theme = one thing users raise), with classification-coverage %. Could be 2, could be 10+.
3. **Size each theme** — accounts / count / share of the scoped denominator.
4. **Score sentiment and frustration per theme.** Rate **frustration 1–5 from observable language only** — escalation words, blame or cancellation framing, repeat contacts, whether they describe having given up, whether they've built a manual workaround. Alongside the number, write a short **plain-language tone read** ("weary, resigned"; "exposed, defensive"; "mild, wishful") so a reader understands the number without a rubric. State that the score is analyst-assigned and inter-rater variance is about a point.
5. **Impact-size each theme** against the metrics the company cares about — accounts & % of base; % of highest-spend accounts / $ at risk (if billing held); churn link (if held); which goal metric it moves. **Label any missing signal — never invent.**
6. **Pull 2–3 strong real quotes per theme** that make the pain vivid. Gaps flagged, never fabricated.
7. **Triangulate for ranking** — Voice (volume × frustration) vs Behavior (churn/usage) vs Commercial (value × goal-fit), scored independently; flag **silent killers** (quiet but costly) and **vocal minorities** (wide but calm and unevidenced); never average a divergence away.
8. **Choose the ranking basis adaptively** (below) — internal, not rendered.
9. **Select the most important ~5 by GOAL FIT, not raw volume or raw frustration.** A wide theme that doesn't advance a goal metric is named in the *deliberately not recommended* list; a quieter theme that moves the North Star is elevated. Each recommendation states the goal metric it moves.

## Output spec (locked order)
**A. Title + scope line.**
- Title is literally **"Voice of Customer Report"**, with the product/team as an eyebrow above it.
- Directly beneath: the **explicit date range** — "6 January 2026 – 30 June 2026 · 176 days · last two quarters". A one-line italic deck naming the headline finding is optional.
- An **"Asked:"** line quoting the user's request verbatim, plus one line on how it was honored (what was filtered, what ordering was applied). This is what makes a filtered run auditable.
- Run line: **Scope** (filters applied, or "no filters applied") · **what the filter excluded**, named · sources · coverage (accounts of base, and **records captured vs counted**) · the team's goal & metrics.

**B. TL;DR (written, prose — problems first).** A source/method sentence, then the key findings enumerated `#1`, `#2`, `#3`… **each on its own line and each written as a problem**, not an observation. The test: the line should name **who is stuck, with what, and why they can't fix it themselves.** "Recognition collapses onto a few managers" is an observation; "Sponsors have no way to make a manager who's never posted, post" is a problem. Each carries one short quote. **Only after** the problems, a short "what this means for us" close.

**C. Problems at a glance — table.** Every problem in one scannable table:
`problem · accounts (n, % of scope) · frustration (n/5) · how they sound (tone read) · metric impacted`

**The metric-impacted column** names the one goal metric each problem sits on — the metric that would move if the problem went away. Pull the metric names from the team's own tracked set (North Star first, then secondaries); mark the North Star as such. Where no goal metric is credibly affected, say **"none identified"** rather than reaching for one — that emptiness is what justifies leaving a theme out of the recommendations later. Where customers assert a metric link we cannot measure, write it as **"X — asserted, not measured."** It is a **mapping, not a measurement**: it says which metric is in play, never how far it has moved.

**Churn counts and $-at-risk are deliberately not columns here.** Dollar figures require a CRM/billing join that many runs won't have, and a column that is often empty gets misread as a zero. Churn and commercial detail still appear where they carry real weight — as chips on the theme cards and as reasoning inside the recommendations — but the scannable index stays on what every run can actually answer: how many, how angry, which metric.
Minor one-offs included as dimmed rows. Follow with three short notes: (a) the denominator + dedup rule, stating that mentions were captured individually and deduped to accounts only at report time; (b) **what was captured but not counted as customer evidence**, with the reason breakdown by origin tier; (c) how the frustration score was derived and that it is analyst-assigned.
If a filter removed data a card or recommendation would otherwise rely on (e.g. a tickets-only run has no ARR or account status), say so once in plain language — **an unexplained absence gets read as a zero.**

**D. Volume vs frustration — radar chart.** Immediately after the glance table. One axis per named theme (exclude the minor bucket), two overlaid series: **volume** (accounts, scaled to the largest theme) and **frustration** (1–5). Label both series and show the axis scale. Follow with a short prose read naming the divergences — which theme is small-but-furious, which is wide-but-calm — and a line noting that neither axis can see commercial impact, so the chart informs the ranking rather than deciding it.

**E. Themes — cards, 2–3 per row.** Every theme (long tail grouped compactly): **size** · plain description · **impact read** (a chip row including a `frustration n/5 · tone` chip alongside accounts, churn, $ and any `not connected` labels) · **2–3 strong real quotes**. Ranked by goal impact. Silent-killer and vocal-minority flags called out in-card.

**F. Recommendations.** The **most important ~5, selected by goal fit**, each = action + why + the goal metric it moves. Then a **"Deliberately not recommended"** block naming what was passed over and why — this is required, not optional; it's where the goal-fit filter becomes auditable. Close with one soft line noting any of these can be developed into a PRD. **No "next step" directive box.**

**Not rendered:** no recommendation-basis badge, no sources-and-integrity footer, no verified-vs-inferred appendix. Provenance lives in the run line (A) and in per-quote attribution; missing signals are labeled inline on the theme cards where they actually matter.

Render as a clean, **report / Word-document-style** artifact (generous margins, prose TL;DR, sectioned, printable). Fall back to structured Markdown if no rich surface. **Three worked examples ship in `examples/` and are the reference standard for structure, tone and honesty — read the closest one before rendering:**
| Example | The question it answers | What it demonstrates |
|---|---|---|
| `kindling-voc-report.html` | "Give me the VoC report for the last two quarters." | The default run — full sources, goal-fit ranking, silent killer + vocal minority |
| `cassette-voc-report.html` | "What were the five most frustrating issues in the last six months?" | A **user-specified ordering** (frustration) honored in the body while recommendations stay goal-fit — and the disagreement called out |
| `marlowe-voc-report.html` | "Most prevalent issues from enterprise accounts' support tickets since March 1." | A **filtered run** — one source, one segment, prevalence ordering, recomputed percentages, empty columns explained, Tier-1 basis caveat |

## Ranking basis (adaptive — internal, not rendered)
Rank by the **richest data actually on hand**:
- **Tier 1 — volume only.** Rank by **volume × frustration**.
- **Tier 2 — + behavior (churn / usage held).** Triangulate voice vs behavior; surface silent killers and vocal minorities.
- **Tier 3 — + commercial (spend / ARR / value held).** Impact-size each theme and rank by business impact against the goal.
Use the highest tier the data supports, theme by theme; **never fabricate a signal to climb a tier.** The basis is not printed as a badge — but if the run is **Tier 1 (volume + frustration only, no churn or commercial data)**, say so in one plain line under the recommendations, because the ranking would likely reorder with behavioral or billing data and the reader needs to know that.

## Quality checklist (the bar)
- [ ] **Capture ran first and in full** — one record per mention, nothing merged, every exclusion carrying a reason code.
- [ ] **Counts exclude `asserted`, `speculative` and `undetermined` records**, and the captured-vs-counted split is disclosed with reasons.
- [ ] **Titled "Voice of Customer Report" with an explicit date range** directly beneath.
- [ ] **An "Asked:" line quotes the request** and states how it was honored.
- [ ] **The requested scope is honored and echoed** — window, source filter, account/segment filter — and percentages are re-derived inside it.
- [ ] **Reads as a report:** TL;DR → glance table → volume-vs-frustration radar → theme cards → recommendations.
- [ ] TL;DR is prose, **findings written as problems** (who is stuck, with what, why they can't fix it), `#1`/`#2`/`#3` each on its own line, business framing only in the close.
- [ ] **Glance table carries accounts, frustration (1–5), a plain-language tone read, and the metric impacted** — with "none identified" written plainly where no goal metric is in play.
- [ ] **Radar chart present** with both series labeled, scale shown, and a prose read of the divergences.
- [ ] Themes are **cards (2–3 per row)**, each with size + description + impact read (including frustration + tone) + 2–3 strong real quotes.
- [ ] **Recommendations selected by GOAL FIT**, capped at ~5, each naming the goal metric it moves — and a **"deliberately not recommended"** block is present.
- [ ] **Sources curated/direct-access only**; public volume channels excluded.
- [ ] **Voice never fabricated or overstated; no counts without a denominator; frustration read from real language; no root-cause guessing.**

## Files
- `SKILL.md` — this spec (Stage 2: the report).
- `CAPTURE.md` — Stage 1: the capture contract governing what enters the report.
- `FILTERING-EXPLAINER.md` — plain-language walkthrough of the five filtering gates, for humans and implementers.
- `examples/` — three reference reports; check output against the closest one before shipping.

## Merges / replaces
Absorbs the old `third-party-feedback` (exhaustive honest counting + real quotes) and `voc-volume-severity` (voice-vs-behavior-vs-commercial triangulation, silent-killer flags) into one curated-feedback → recommendations report. (The lighter `feedback-synthesis` remains for quick thematic passes.)

## Known gaps / limitations
- Only as complete as the curated corpus — gaps in what you collected become gaps here; stated, not hidden.
- **Narrow scopes shrink the denominator fast.** A one-week or single-account run is a snapshot, not a trend; the report says so rather than implying more.
- Frustration scoring is analyst judgment from language. It is observable but not objective — two readers may differ by a point, and the report states this.
- Correlation ≠ causation: a churn-linked theme isn't proven to cause churn — flagged; an experiment settles it.
- Survivorship: feedback misses users who already left over a problem — trend/exit signals partially cover this.
- **Capture is the expensive stage.** Reading every source in full before analyzing anything is slower than skimming for themes. It is also the only thing that makes counts trustworthy, so it is not optional — but on very large corpora expect capture to dominate the run.
- **Removing the integrity footer costs something.** Verified-vs-inferred separation and sampling caveats now live only inline (run line, `not connected` chips, in-card flags). If a report is going to an audience that will act on the dollar figures, consider restoring an appendix for that run.

## Sprntly integration

- **Where files live in this vendored copy:** `CAPTURE.md` is injected into the
  prompt as `### REFERENCE: CAPTURE.md` — read it there; same contract, same
  name. `examples/` and `FILTERING-EXPLAINER.md` are vendored on disk for
  maintainers but are NOT in the prompt; the pinned template below already
  encodes the examples' design, so follow the Output spec section order above.
- **Output surface:** in Sprntly chat the report is produced through
  `app/voc_report.py` — the model emits ONLY structured report data (that
  module's `SCHEMA`); a deterministic pinned template renders the HTML,
  including the radar chart as SVG computed from the glance rows. Never write
  HTML/CSS/SVG by hand. When this skill is invoked outside that path (generic
  skill-routed answers), fall back to structured Markdown in the locked
  section order, with the radar as a compact two-series table plus the prose
  divergence read.
- **Corpus provenance:** when run via the live call digest, the corpus header
  states the window, call count, and any sampling applied (quotes trimmed per
  call, or older calls omitted) — carry any stated truncation into the run
  line as a coverage caveat, and never claim more coverage than the header
  declares.
- **Generate PRD** lives OUTSIDE the rendered report (the app panel's bottom
  bar) — the report itself carries no CTA buttons; a recommendation routes the
  problem + its signals into `prd-author` pre-filled with counts, quotes, and
  the impact line.
- **Move to backlog** → creates a backlog item carrying the metric line.
- Convergence with `interview-synthesis` outputs is flagged with ⇄ when the
  same problem appears in another source family.
