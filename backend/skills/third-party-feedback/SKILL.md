---
name: third-party-feedback
description: Turn customer feedback into an exhaustive, time-bounded, decision-grade analysis — every problem, its real count over a defined window, trend vs the prior period, whether it's solved, and chronic vs new — with 2-3 real customer quotes per problem and a frustration-vs-volume radar chart — so a product team knows exactly what to act on. It deliberately does NOT guess root cause (that invites hallucination) — it reports what customers say. Runs in two modes it always declares: COMPLETE-CORPUS (your support tickets / review exports / NPS / feedback board → real counts) or OPEN-WEB (public channels → themes, explicitly sampled, no fake counts). Use when the user says "third party feedback", "what are people saying about us", "voice of customer", "analyze our complaints/tickets/reviews", "how many complaints about X", "what should we fix", "review mining". Always states its denominator and window; enumerates every problem, not just the top few; backs each with real quotes; ends each in a decision.
---

# Third-Party Feedback (exhaustive, decision-grade analysis of what customers say)

## What it does
Turns customer feedback into an analysis a product team makes decisions from — **exhaustively** (every problem in the corpus, not a top-3 highlight reel) and **honestly counted** (real numbers when it has real data; clearly-labeled themes when it doesn't). For each problem it answers: what it is, **how many over what window** (count + share + trend vs the prior period), **why** (root-cause hypothesis with confidence + how to confirm), **is it still live** (live / possibly-resolved / known), **chronic vs new** (first/last-seen dates), how customers feel (sentiment + real quotes), and **what to do** (a decision). Two things it never does: imply a count it can't back with a denominator, or stop short of the full list.

## The two modes — always declared up front
- **COMPLETE-CORPUS mode (use this for real counts).** Runs over a *complete* dataset for a window — support tickets, review/app-store exports, NPS/CSAT verbatims, feedback-board items, refund/error logs. Produces **real counts per problem, share of total, and trend vs the prior period** (e.g. "P1: 240 in the last 9 months, +18% vs the prior 9"), exhaustively, because the denominator is known.
- **OPEN-WEB mode (use when no corpus).** Mines public channels broadly. Produces **themes with relative frequency and source spread, explicitly labeled "sampled — not counts."** It states the window and coverage it actually had and never fabricates a per-problem count.
- **Combined:** corpus for the counts, web for extra color/quotes — kept visibly separate.
The analysis opens by stating which mode it's in, **the denominator (N items analyzed), and the window** — or says plainly that no denominator exists and the output is themes only.

## When to use / when NOT to use
- **Use** to decide what customer problems to fix/build from real-world signal, at the volume you actually have it.
- **Do NOT use** to theme a tiny handful you'll read yourself (overkill), run a competitor study (`competitive-intelligence-review`), or design a survey (`survey-design`).

## Inputs
- **Best (for counts):** a complete export for a window — support tickets (CSV/Zendesk/Intercom), app-store/review exports, NPS verbatims, feedback-board items, error/refund logs. The more complete the corpus, the more exhaustive and trustworthy the counts.
- **Required minimum:** the product (+ handles/IDs) for open-web mode.
- **Optional:** product type, the **window** (e.g. last 9 months) and prior window for trend, release notes/changelog (resolution check). *Works even if a source is unavailable — it reports coverage honestly and never invents.*

## Method (methodology)
0. **Declare mode, corpus, denominator, window.** State complete-corpus vs open-web, N items, and the date range. If no denominator exists, say so and switch the output to themes-not-counts.
1. **Source-select / load corpus.** In corpus mode, ingest the whole dataset for the window; in web mode, pick sources by product type (`modules/sources-and-areas.md`) and plan around any unavailable one.
2. **Gather/normalize.** Capture each item verbatim + date + source + segment. De-dupe (state the dedup rule).
3. **Classify every item** on problem type (complaint · bug · feature request · UX issue · praise · churn signal · pricing objection · question · competitor comparison), sentiment + emotion, and the feature/aspect.
4. **Enumerate exhaustively.** Produce the **full problem inventory** — every distinct problem found, including the long tail — and report **classification coverage** (what share of items landed in a named problem vs "other/unclassified"), so the reader knows the analysis accounts for the whole corpus, not a curated slice.
5. **Count + trend (corpus mode).** Per problem: count this window, count prior window, % change, share of total, first/last-seen. In web mode, substitute relative frequency + source spread and label it sampled.
6. **Longevity & resolution.** First/last-seen → chronic vs new (volume floor before any trend claim); resolution buckets **live / possibly-resolved (confirm) / known/won't-fix**, cross-checked vs changelog — never silent-drop.
7. **Voice of the customer — real quotes only.** Pull **2-3 vivid, verbatim, sourced quotes per problem** that convey the issue *and* the frustration. Quotes MUST be real and attributed — **never invent a quote, and never turn a paraphrase into quotation marks.** If the corpus/sample has no strong quote for a problem, **say so plainly** rather than fabricate. *(No root-cause analysis: inferring "why" from text is where hallucination creeps in — report what customers say, not a guessed cause.)*
8. **Sentiment, journey, churn, frustration.** Sentiment + emotion; journey-stage tag; churn-risk flag (floats cancellation up regardless of count); rate **frustration intensity from the language customers actually use** (observable, 1-5) — not invented.
9. **Decide — impact over volume.** Each problem ends in fix / build / investigate (naming the question) / monitor / won't-do, ranked by business impact (segment + value at risk), with a "what would change this call." Run `fact-check`; separate verified from inferred. Route to `user-stories` / `prioritize` / `competitive-intelligence-review`.

## Output spec
- **Header:** mode · denominator (N) · window · classification coverage %.
- **TLDR (strong, decision-first — lead with this).** 4-5 plain-language lines a busy PM gets in 20 seconds: the **2-3 problems to act on now** (with the decision for each), **where pain concentrates** (the loudest vs. the most enraging — they're often different), the **single most urgent thing**, and the mode/denominator caveat in one clause. Punchy, no jargon; the rest of the report justifies it.
- **Complete problem inventory (exhaustive) — table:** every problem · count this window · share of total · trend vs prior · first/last-seen · status (live/possibly-resolved/known) · severity. The long tail is included, not truncated. (Web mode: count column shows relative frequency + spread, labeled sampled.)
- **Frustration × Volume radar (spider) chart** — one axis per problem, two overlaid rings/series (**volume** and **frustration**) so the shape shows at a glance where pain concentrates. Values are real counts in corpus mode, **relative/qualitative (analyst-rated from the actual language, labeled as such)** in web mode.
- **Problem records (structured — easy for humans AND LLMs to consume):** each problem as a consistent block with fixed fields so nothing important is lost or buried —
  `id · problem · type · volume · frustration(1-5) · status · first→last-seen · churn-flag · decision + what-would-change` followed by **voice: 2-3 real sourced quotes**. No root-cause field.
- **Prioritized queue** by business impact. **Possibly-resolved & known lists** surfaced separately.
- **Coverage & integrity note:** sources/corpus completeness, what was unavailable, verified-vs-inferred, sampling/vocal-minority caveats.

## Sprntly integration (optional)
- **Inputs from Sprntly:** connected support/review/NPS data for corpus mode; handles/IDs + prior runs for web mode; release notes from the knowledge graph.
- **Outputs to Sprntly:** counted, typed, decided problems into the signal/outcome graph; feature requests → `user-stories`; queue → `prioritize`; competitor mentions → `competitive-intelligence-review`; recurring-run trend deltas.
- **Degrades to:** standalone — corpus file → counts; product name → themes.

## Quality checklist (the bar)
- [ ] **Mode, denominator (N), and window are stated up front** — or it plainly says no denominator exists and outputs themes only.
- [ ] **Exhaustive** — the full problem inventory is listed (long tail included), with classification-coverage % so nothing is hidden; not truncated to a top few.
- [ ] **Real counts + trend vs prior period in corpus mode**; relative-frequency-labeled-sampled in web mode; **never a count without a denominator.**
- [ ] First/last-seen dates; chronic vs new; resolution buckets (live/possibly-resolved/known), never silent-dropped.
- [ ] **Voice of the customer: 2-3 real, sourced, verbatim quotes per problem** that convey the frustration; **never invented**; gaps flagged honestly. **No root-cause guessing.**
- [ ] **Frustration × Volume radar chart** included; values labeled real (corpus) vs relative (web).
- [ ] **Output is structured for easy LLM + human consumption** — consistent fields per problem, nothing important buried.
- [ ] **A strong, decision-first TLDR leads** — top problems + their decisions, where pain concentrates, the most urgent thing — readable in 20 seconds.
- [ ] Journey + churn flag present.
- [ ] Ranked by business impact; every problem ends in a decision with a what-would-change-it.
- [ ] **The ultimate goal holds: an exhaustive, honestly-counted analysis a PM can decide from — not a lightweight highlight list.**

## Refined by expert panel
Critiqued and refined by a five-lens panel — Product (decisions/priority), UX Research (rigor: normalized prevalence, sampling bias), Customer Experience (journey + churn + human quotes), Support/Success (resolution buckets, never silent-drop), Data/Insights (denominator, counts + trend, volume floors, exhaustive enumeration). Holding constraint: **the output must be exhaustive and decision-grade — every problem accounted for, honestly counted, each ending in a decision.** Later refined by user direction: **root-cause inference removed to avoid hallucination; 2-3 real customer quotes added per problem; a frustration-vs-volume radar chart and an LLM-consumable structured format added.**

## Known gaps / limitations
- **Open-web mode cannot produce true counts** — public posts are a biased, partial sample; only a complete first-party corpus yields "how many in the last N months." The skill must say which it's giving.
- Root cause is a hypothesis from text, not a diagnosis — confirm before betting.
- Counts are only as complete as the corpus; gaps in the export become gaps in the analysis — state them.

## Worked example
**Input (corpus mode):** "Analyze our last 9 months of support tickets + app-store reviews for what to fix." (12,480 items, Sep 2025–Jun 2026)
**Output (abridged):** Header: corpus mode · N=12,480 · Sep 2025–Jun 2026 · 94% classified. Inventory (exhaustive, 23 problems): P1 sync failures 240 (1.9% of items, +18% vs prior 9mo, chronic, live); P2 charged-after-rejection 96 (+5%, chronic, live); … down to the long tail (e.g. "tooltip typo" ×3). Decision cards for the top movers with counts, trend, root-cause hypothesis, quotes, decisions. Possibly-resolved: "login loop" 0 mentions since v2.2 (was 70/quarter) — confirm. Coverage note: tickets complete; reviews via export, 2 locales missing.
**Input (web mode):** same product, no corpus → header says "open-web, sampled, no true counts," themes with spread, explicit caveat that counts aren't possible without the ticket data.
