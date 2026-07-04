---
name: voice-of-customer-report
description: Turn the company's own direct-access feedback corpus — CSM call transcripts/notes, support tickets, customer and churn/exit interviews, sales notes, NPS verbatims — into a decision-grade Voice of Customer report. Problems are framed as user problems, sized with real counts (never percentages), rated on paired low/med/high volume and severity scales, and quantified by the metric each impacts and by how much — with revenue impact always shown (a sourced figure, or 🅘 unknown; never estimated). A prioritization gate caps recommendations at the 5–7 most important. Triggers: "voice of customer", "VoC report", "what are customers telling us", "analyze our calls/tickets/interviews", "which problem should we fix first", "rank complaints", "quantify user pain". Direct-access sources only — public channels (app stores, Reddit, X, review sites) are out of scope and route to public-feedback-report. Replaces the retired third-party-feedback and voc-volume-severity skills.
---

# Voice of Customer Report

Synthesize the first-party feedback corpus into one report a product team can act
on: what users struggle with (in their words and framed as user problems), how
big and how severe each problem is, which metric it moves and by how much, and
the 5–7 most important actions — nothing more.

This skill **replaces** `third-party-feedback` and `voc-volume-severity`. Their
jobs — decision-grade feedback analysis and problem prioritization by
volume × severity × hard signals — are merged here as one pass over one corpus.

## Scope

- **In:** sources the company has direct access to — CSM call transcripts and
  notes, support tickets, customer interviews, churn/exit interviews, sales-call
  notes, NPS/CSAT verbatims with account linkage. Real counts are possible, so
  real counts are required.
- **Out:** public, anonymous, volume-based channels (app-store reviews, Reddit,
  X, G2, forums). Those are a biased sample and belong to
  `public-feedback-report`. If the user asks for both, run both skills and
  cross-reference convergence; never mix corpora in one count.

## Triggers

"voice of customer" · "VoC report" · "what are customers telling us" ·
"analyze our calls / tickets / interviews" · "which problem should we fix
first" · "rank complaints" · "prioritize feedback" · "quantify user pain" ·
a corpus of transcripts/tickets/interviews is provided and the user wants to
know what matters.

## Inputs

1. **The corpus** (required): files, exports, or pasted content. Declare what
   was received (source types, item counts, time window) — the report header
   names all of it.
2. **Goal metrics** (requested if absent): the metrics the team is tracking this
   quarter (e.g. activation, NRR). Recommendations are selected by fit to these.
   If none are given, state the assumption used and mark it `[ASSUMPTION → T0]`.
3. **Commercial data** (optional): ARR per account, expansion pipeline, churn
   records. When connected, the revenue line in the impact column becomes hard
   (🅗); when absent, it reads `🅘 unknown` — never an estimate.

## Method

1. **Classify the corpus.** Tag every item to a problem. Report classification
   coverage (% of items tagged) in the header.
2. **Name each problem as a user problem.** The name states the difficulty from
   the user's side — "Difficult to export PRDs for leadership", "Hard to get
   connected in the first week" — never internal/solution framing ("export
   feature gap", "onboarding funnel issue").
3. **Size volume.** Participant/item counts per source ("11 of 17 calls · 19
   tickets"), never percentages, and a **low / med / high** rating on the shared
   pill scale.
4. **Rate severity** on the same **low / med / high** scale, with a 2–4 word
   justification ("blocks all value", "workaround exists"). Volume and severity
   always render adjacent so divergence is visible at a glance.
5. **Quantify impact.** For each problem: the **metric it impacts** (one metric,
   named) and **by how much** — a delta computed or quoted from the corpus with
   a confidence tier (🅗 hard / 🅢 soft-stated / 🅘 inferred-unknown).
   **Revenue impact is always shown**: a sourced figure when stated or when
   commercial data is connected, otherwise `revenue: 🅘 unknown`. Never
   estimated, never omitted.
6. **Flag silent killers 🔇.** Any problem whose volume rating is low but whose
   severity or dollar impact is high gets the flag — impact sizing elevates it
   above its voice volume.
7. **Build recommendation candidates.** Every problem generates candidate
   actions; when 🅘 gaps dominate the impact column, one candidate must be the
   instrumentation/investigation that closes them.
8. **Run the prioritization gate** (see below) and write the report.

## Prioritization gate (hard rule)

The report never ships a long recommendation list.

- Rank all candidate actions by impact (metric moved × size of movement ×
  goal fit; revenue-bearing candidates rank on the dollars).
- Select the **top 5**. Extend to at most **7** only when candidates at the
  cutoff have approximately equal impact — a tie at the cut is the only
  exception.
- Everything below the cut routes to monitor/backlog and is **not listed** in
  the report.
- Render proof the gate ran, as one mono line under the Recommendations header:
  `PRIORITIZATION GATE PASSED ✓ — N candidates identified · K selected · cap
  5–7 unless impacts tie at the cut · N−K routed to monitor/backlog`.

## Output spec (section order is fixed)

Delivered as a document (HTML/Word style). Sections in order:

1. **Title + header chips** — skill name, scope note ("curated, direct-access
   sources only"), basis chip ("volume + impact + commercial").
2. **TL;DR panel** (designed block, not plain prose):
   - Header strip in a light tint with the label and the **sources as chips**
     (each source type + count, plus the time window). Light backgrounds only —
     never a dark/heavy block.
   - A one-sentence serif lede naming the arc of the quarter.
   - The top findings (**#1/#2/#3**) as rows: numbered disc · **user problem in
     bold** · one plain-language sentence · an `IMPACTS →` mono line (metric +
     quantified delta + tier) · **VOL and SEV mini-pills right-aligned** on the
     shared low/med/high scale. Silent killers carry 🔇 in the row.
3. **User problems at a glance** — table with this exact column contract:
   `User problem | Volume | Severity | Metric it impacts | By how much`
   - Volume and Severity are **adjacent columns** with the same low/med/high
     pill scale; the raw count sits beneath the volume pill, the justification
     beneath the severity pill.
   - "Metric it impacts" and "By how much" are **separate columns**. "By how
   much" holds the quantified delta + tier, and the always-present revenue
   line.
   - A final long-tail row aggregates items below the reporting threshold
     ("monitor — no metric movement claimed").
4. **Themes — in the customer's words.** Card grid, 2 per row: user-problem
   title · counts + persistence tag (persistent / new) · short description ·
   2–3 **verbatim** quotes with source + date attribution · an impact box
   restating metric + delta.
5. **Recommendations.** Gate line first (see above), then 5–7 cards:
   - rank (R1…) · **title = the action**
   - **description: 1–2 plain sentences that let a reader with no context
     understand what the recommendation actually is** — what gets built or
     done, and what changes for the user. Never lead with ranking rationale;
     the "why it ranks" sentence comes after the description.
   - `IMPACTS →` mono line: metric + expected/current delta + tier.
   - CTAs: **Generate PRD** (primary) · **Move to backlog** (ghost).
     Investigation-only items carry backlog CTA only.
6. **No footer.** There is no recommendation-basis block; scope and basis live
   in the header chips, tiers are legended inline on first use.

## Hard rules

- **No fabricated data.** Every figure is computed from the corpus, quoted with
  attribution, or labeled with a tier; unknowns say 🅘 unknown. Tiers: 🅗 hard
  (counted from the corpus) · 🅢 soft (stated, unverified) · 🅘
  inferred/unknown.
- **Counts, never percentages** for qualitative sources — n is too small.
- **User-problem framing everywhere** — TL;DR, table, themes, and
  recommendations all name problems from the user's side.
- **Revenue always shown** in the impact column, figure or 🅘 — never estimated,
  never omitted. Soft-stated dollars carry 🅢 and a verification note (e.g.
  "verify vs CRM").
- **Quotes are verbatim** from the corpus; a paraphrase is never quoted.
- **5–7 recommendations max**, gate line rendered as proof.
- Direct-access corpus only; public channels route to `public-feedback-report`.

## Quality bar

- [ ] Header declares sources, counts, window, classification coverage.
- [ ] Every problem name is a user problem, not a solution or internal label.
- [ ] Volume and severity adjacent, same pill scale, counts under volume.
- [ ] Metric and "by how much" are separate columns; every row has both.
- [ ] Revenue line present on every problem (figure w/ tier, or 🅘 unknown).
- [ ] 🔇 applied where volume is low but severity/dollars are high.
- [ ] Gate line rendered; recommendation count is 5–7 (or ≤5), tie rule noted
      if 7.
- [ ] Each recommendation is understandable from its own card — description
      sentences first, ranking rationale second.
- [ ] No basis footer; no percentages; no invented figures; quotes verbatim.
- [ ] TL;DR uses the light-tint panel — no dark background blocks.

## Sprntly integration

- **Generate PRD** on a recommendation → routes the problem + its signals into
  `prd-author` (the signals section arrives pre-filled with counts, quotes, and
  the impact line).
- **Move to backlog** → creates a backlog item carrying the metric line and
  tier labels.
- Convergence with `interview-synthesis` and `public-feedback-report` outputs
  is flagged with ⇄ when the same problem appears in another source family —
  cross-source convergence is the strongest confidence signal in the suite.
