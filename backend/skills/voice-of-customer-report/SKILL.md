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
   named) and **by how much** — a delta counted from or quoted verbatim in the
   corpus with a confidence tier (🅗 hard / 🅢 soft-stated / 🅘
   inferred-unknown). When the corpus contains no quantified movement for the
   metric, say so qualitatively or write `🅘 unknown — no metric data connected`
   — a number is NEVER computed, extrapolated, or invented to fill the slot.
   **Revenue impact is always shown**: a sourced figure when stated or when
   commercial data is connected, otherwise `revenue: 🅘 unknown`. Never
   estimated, never omitted — no CRM spend data means every revenue line is 🅘
   unknown.
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

## Output spec (structured data → fixed template)

Return the report as **structured data only** — you do NOT author HTML or CSS. A
fixed template (`app/voc_report.py`) renders your data into the pinned design
(`example-output.html`) and the app shows it in a sandboxed iframe. This keeps the
design pixel-identical and the output fast to generate. Emit exactly these fields
(schema-validated); the template supplies all layout, chips, pills, the disc
numbering (#1/#2/#3), the `R1…` ranks, the gate line, and the CTA buttons.

- **`title`** — report title naming the window (e.g. "Voice of Customer — Q2 2026").
- **`lede`** — one sentence naming the arc of the quarter. `**bold**` spans are
  honoured (the only markup); keep it short.
- **`sources`** — the source chips: each source type + count, plus the time window
  (e.g. `"17 CSM calls · ~15 accounts"`, `"42 support tickets"`, `"Apr–Jun 2026"`).
- **`coverage`** — classification coverage note (e.g. "coverage: 94% of items tagged").
- **`top_findings`** — the TL;DR #1/#2/#3 rows, most important first. Each:
  `problem` (user-problem framing), `sentence` (one plain line), `impact_line`
  (the `IMPACTS →` metric + quantified delta + tier), `vol{level,count}`,
  `sev{level}`, `silent_killer`. `level` ∈ `low|med|high`.
- **`problems`** — the "at a glance" table rows: `problem`, `vol{level,count}`,
  `sev{level,note}` (note = 2–4 word justification), `metric` (the one metric it
  impacts), `by_how_much` (quantified delta + tier), `revenue_line` +
  `revenue_unknown` (set `true` and `revenue_line:"revenue: 🅘 unknown"` when no
  sourced figure — never estimate), `silent_killer`.
- **`long_tail`** — `{label, count_note}` for the aggregate row of items below the
  reporting threshold (rendered as "monitor — no metric movement claimed").
- **`themes`** — quote-led cards: `title` (user problem), `size_line` (counts +
  persistence: persistent / new), `description`, `quotes[]{text, attr}` (2–3
  **verbatim** quotes with source + date), `impact_line`, `impact_warn` (true tints
  it amber for an at-risk metric), `silent_killer`.
- **`gate`** — `{candidates, selected, routed}`: the true counts behind the
  prioritization gate. The template renders the `PRIORITIZATION GATE PASSED ✓` line.
- **`goals_note`** — the goals the recommendations were selected against (e.g.
  "activation · NRR"), shown in the Recommendations header.
- **`recommendations`** — ONLY the selected 5–7, ranked: `title` (= the action),
  `description` (1–2 plain sentences a reader with no context understands — what
  gets built and what changes for the user; ranking rationale comes AFTER),
  `impact_line`, `investigation_only` (true when the action is research, not a
  build — the report renders no CTA buttons; the app's panel hosts the real
  Generate PRD action outside the document).

No footer and no header chips — the report body carries the signal; tiers are
legended inline.

## Hard rules

- **No fabricated data.** Every figure is counted from the corpus or quoted
  with attribution; unknowns say 🅘 unknown. A number the corpus doesn't
  contain is never derived, projected, or "reasonably estimated" — absent
  analytics/CRM data reads 🅘 unknown, full stop. Tiers: 🅗 hard (counted from
  the corpus) · 🅢 soft (stated, unverified) · 🅘 inferred/unknown.
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
