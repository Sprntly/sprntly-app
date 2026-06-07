---
name: competitive-intelligence-review
description: A McKinsey-grade, decision-first competitive intelligence review for product teams — one skill that self-scopes, deciding internally which of its stages to run (scope, us-first, arena, 9-box, product/pricing, momentum, sentiment, financials, synthesis) based on the request and the competitors. Use when the user says "competitive intelligence", "deep competitive analysis", "market intelligence review", "full competitor study", "competitive landscape for strategy", "where are we vs competitors and what do we do", or wants the thorough version that pulls position, share, pricing, traffic, app ratings, sentiment, ship cadence, AI-search visibility, and (for public companies) filings into ranked product decisions. Written first-person for the company running it ("we/our"), as an internal review their own team built — and delivered as a mix of well-written prose and infographics (9-box, feature matrix, momentum/financial comparisons) for fast visual understanding. It self-scopes from a quick pulse to a full quarterly study, so it covers both fast teardowns and deep reviews.
---

# Competitive Intelligence Review

## What it does
Runs a thorough, decision-first competitive intelligence study for a product team — the kind a strategy partner would deliver. It starts with *your* position (not the competitors), maps the full arena including substitutes and future entrants, scores everyone on a McKinsey 9-box, tears down product and pricing by job-to-be-done, reads the momentum signals that reveal who is *actually* winning (traffic, app ratings, AI-search visibility, ship cadence), mines customer sentiment by theme, reads the money and strategy from public filings where available, and then **forces every finding into a ranked set of product decisions** tied to your strategy. It is explicitly not a data dump: a spreadsheet of competitor facts is worthless until synthesis turns it into a decision, and the whole skill is built around that last step.

**This is one skill.** You invoke it once; it then **decides for itself which of its internal stages to run** based on what's being analyzed and what the request needs — it never asks you to call parts separately. (For example: it skips the financials stage for a private competitor, skips app-store data for a non-mobile product, and runs a light pass when you only want a quick pulse.) The stages live as files in `modules/` and are loaded by the skill only when it determines they're needed (progressive disclosure), so one call does the right amount of work.

Internal stages (the skill selects which to run — see "How the skill self-scopes" below):

| # | Stage | The question it answers | File |
|---|---|---|---|
| 0 | Scope & competitor set | Who actually shapes our buyer's decision? (pick 3–5, don't boil the ocean) | `modules/00-scope.md` |
| 1 | Us first | What's our position, segments, share, where we win/lose, and our goal? | `modules/01-us-first.md` |
| 2 | The arena | Direct rivals + substitutes + adjacent/future entrants (Porter's Five Forces) | `modules/02-arena.md` |
| 3 | Position & share | 9-box: invest / maintain / harvest / divest per competitor & segment | `modules/03-position-share.md` |
| 4 | Product & pricing | Feature teardown by job-to-be-done + pricing/packaging; where each wins | `modules/04-product-pricing.md` |
| 5 | Momentum signals | Who's *actually* winning: traffic, app data, AI-search visibility, ship cadence | `modules/05-momentum-signals.md` |
| 6 | Voice of customer | Review/social sentiment by theme, per competitor vs. us | `modules/06-third-party-feedback.md` |
| 7 | Money & strategy | Public-company filings: growth, margins, R&D, M&A, broadening strategy | `modules/07-money-and-strategy.md` |
| 4b | Head-to-head & feature gaps | How do we score vs. each rival by job? Which gaps are Build / Skip / Reframe, and why? | folded into `modules/04-product-pricing.md` + synthesis |
| 8 | Synthesis → decisions | TLDR + ranked Build/Skip/Reframe + build queue; the "so what" → roadmap moves | `modules/08-synthesis-decisions.md` |

A data-sourcing playbook (free/low-cost workarounds for the metrics that normally need paid APIs) is in `modules/data-sources.md`. The final artifact structure is `templates/cir-report-template.md`.

## Voice & format
- **First-person, insider voice.** Whoever runs this skill is analyzing the landscape *for their own company*, so write it that way: the company under review is **"we / our / us,"** and the report reads like the company's own team built it for an internal strategy discussion — not like an outside analyst describing a stranger. (In the worked example below, Sprntly is "we.")
- **Prose + infographics, interleaved.** The deliverable is a mix of well-written documentation and visuals: render the 9-box, the job-to-be-done feature matrix, the momentum scorecard, the pricing comparison, and the financial/scale comparison as **infographics** (colored tables, quadrant plots, stat cards, simple bars) on a surface that supports them, with prose around each explaining the "so what." Lead with words, support with visuals — never a wall of tables, never a wall of text.
- **One report, two layers — never drop the strategy.** The report always contains BOTH: **Part 1 — the build decision** (TLDR, head-to-head scorecard, feature-gap Build/Skip/Reframe, what's heating up, ranked build queue) AND **Part 2 — the strategic picture** (us, arena/lanes, 9-box position & share, product/pricing, momentum data, money/scale). Part 1 is what a PM acts on; Part 2 is the evidence base that proves the Part 1 calls are right. The PM build layer *augments* the strategic analysis — it never replaces it. A reader can stop after the TLDR, go one level deeper into Part 1, or read the full Part 2 evidence.
- **TLDR first — always.** The report opens with a tight TLDR a busy PM reads in 30 seconds: the 2–3 things that changed / matter most, the top feature gaps, and the ranked recommendations (build / skip / reframe). Everything else is the evidence base for the TLDR.
- **Built for a PM to know what to build.** This is not just strategy — it ends in *features and moves*. It must always deliver: (1) a **head-to-head scorecard** (how we do vs. each competitor, by the jobs that matter); (2) a **feature-gap matrix** with each gap classified **Build / Skip / Reframe** and *why it matters* (losing deals / user complaints / stalled evals vs. theoretical); (3) **what's heating up** — the trends, fast-growing features, and momentum signals that say where the puck is going; (4) a **ranked build queue** weighted by impact × our strength. A gap with no evidence it costs us is labeled theoretical and routed to Skip — naming what *not* to build is as valuable as what to build.

## When to use / when NOT to use
- **Use** for the thorough, periodic (e.g. quarterly) study that informs roadmap and strategy — when a PM needs a real pulse on the whole industry and a decision at the end.
- **Do NOT use** for: market attractiveness/structure alone (`market-structure` — this skill *uses* it in module 2); your own positioning statement (`positioning` — this *feeds* it); a single decision between options (`decision-by-traffic-lights`). For a quick read, run this skill at its "quick pulse" depth rather than the full study.

## Inputs
- **Required:** your product, and either the named competitors or "find them."
- **A specific company to compare against:** if the user names one ("compare us to X"), X is always included as a first-class competitor and gets its own column in every matrix and the head-to-head scorecard — the skill still adds the other key rivals around it for context, but never drops the named one.
- **Recurring run (this is built to run weekly / bi-weekly):** if a prior run is provided, the report leads with **what changed since last time** (new features shipped, pricing moves, traffic swings, new entrants) — a diff, not a re-derivation. If none, it's the baseline run and says so.
- **Optional:** your strategy/market goal (strongly recommended — it's the lens module 8 judges everything against), target segment, your own metrics (share, traffic, ratings), win/loss notes, any data exports you have. *If a metric is missing and no source is available, the skill flags it as an open data-pull or labels an `[ASSUMPTION]` — it never fabricates a traffic number, share figure, or rating.*

## Method (methodology)
Adopts the **McKinsey five-step spine** — diagnostic (current state) → benchmarking (vs competitors) → scenario modeling (where it's going) → prioritization (impact × feasibility) → implementation roadmap (sequenced, owned) — and maps the eight modules onto it: modules 1–7 are diagnostic + benchmarking, module 5's trend read is scenario modeling, and module 8 is prioritization + roadmap. Two named frameworks are baked in: **Porter's Five Forces** (module 2, for substitutes/entrants/power) and the **GE-McKinsey 9-box** (module 3, market attractiveness × competitive strength → invest/maintain/harvest/divest).

Run order:
1. **Select stages (self-scope).** First decide which stages this particular review needs and at what depth, per "How the skill self-scopes" below — public vs. private competitors, mobile vs. not, quick pulse vs. full study, what data exists. Run only those; note what was skipped and why. The caller invokes the skill once and this routing happens inside it.
2. **Scope (stage 0).** Pick the 3–5 competitors that actually shape buyer decisions; go deep on them, shallow on the rest. Resist the 40-points-×-N-competitors trap — that produces a month-stale data dump, not a decision.
3. **Us before them (stage 1).** Establish our own position and, critically, our strategy/goal — every later finding is judged "so what *for us*."
4. **Run the selected stages (2–7)** to the chosen depth, pulling data via `modules/data-sources.md`. **Tier every claim**: 🅗 hard (filings, app ratings, measured traffic), 🅢 soft (review themes, social sentiment), 🅘 inferred (strategy reads). Never blend tiers silently — a strategy partner labels confidence.
5. **Synthesize (stage 8).** Turn the evidence into the "so what": the top 3–5 things to learn-from / close / exploit, each scored impact × feasibility and tied to our strategy, ending in concrete roadmap moves with owners. **This stage is the point; everything above feeds it.**
6. **Set a refresh cadence.** CI decays fast — state when this is re-run and what change-alerts to watch, so it stays living, not a one-off.
7. **Data-integrity self-audit (required).** Before delivering, scan every number, quote, and named fact; confirm each cites a source or is plainly described as unknown. Remove or rephrase anything untraceable. See "Data integrity" below — this is non-negotiable.

## Output spec
A decision-first report (`templates/cir-report-template.md`), in this order: executive "so what" (the decisions, up front) → our position & goal → the arena (Five Forces) → 9-box position/share → product & pricing teardown → momentum signals + trend → third-party-feedback sentiment → money & strategy (public cos) → ranked decisions with owners & impact×feasibility → data appendix (sources + confidence tiers + open pulls) + refresh cadence. It is written **first-person (we/our)** as the company's own internal review, and is delivered as **interleaved prose + infographics**: the 9-box (quadrant plot), the JTBD feature matrix (colored grid), the momentum scorecard, the pricing comparison, and the competitor scale/financials (stat cards or bars) render as visuals on a surface that supports them, each wrapped in prose that states the implication.

## How the skill self-scopes (stage selection)
One invocation. Before doing the work, the skill decides which stages to run and at what depth — the caller never picks. The rules:

- **Always run:** Stage 0 (scope), Stage 1 (us-first), Stage 8 (synthesis → decisions). These are the spine; without them it's a data dump.
- **Depth dial — read the request:**
  - *Quick pulse / "where do we stand"* → run 0, 1, a light 2 (arena), 4 (product/pricing), 8. Skip 3, and run 5–7 only if a specific signal is asked for — this is the fast-teardown depth.
  - *Full / quarterly / "thorough" / "for strategy"* → run all stages.
- **Conditional stages (include only if the condition holds):**
  - **Stage 3 (9-box)** — include for portfolio/strategy/roadmap decisions; skip for a narrow single-competitor head-to-head.
  - **Stage 5 (momentum)** — always include AI-search visibility + ship cadence; include the **app-data** sub-part only if the product is mobile; include traffic only if a source exists.
  - **Stage 6 (third-party-feedback)** — include if the competitors have a meaningful review/social footprint; skip (and say so) for a brand-new competitor with no reviews yet.
  - **Stage 7 (money & strategy)** — include only for **public or well-funded** competitors (filings/funding exist); for tiny private ones, skip the financials and use the lightweight proxy note instead.
- **Data-driven skips:** if a stage's data can't be sourced (even via the free workarounds), the skill doesn't pad it — it records an *open data-pull* and moves on.
- **State what it ran:** the report names which stages were run vs. skipped and why, so the scoping is transparent (e.g. "Stage 7 skipped — all competitors private").

## Data integrity — no fabricated data (hard guardrail)
This is the skill's most important rule. Competitive analysis is exactly where invented-but-plausible numbers slip in (a traffic figure, a market share, a download count, a revenue line). **The skill never fabricates data. Not once, not "for illustration," not to fill a cell.** Enforced as follows:

- **Every quantitative claim needs a real, named source + date.** Traffic, market share, downloads, ratings, revenue, growth %, margins, headcount, pricing — each must cite where it came from (e.g. "SEC 10-K, FY25"; "G2, pulled 2026-05-30"). A number with no source is forbidden.
- **No invented specifics.** The skill does not guess a competitor's revenue, a traffic number, a download count, a rating, a price, a tier, a feature, or an exec quote. If it wasn't observed or sourced, it isn't stated as fact.
- **Estimates are allowed only when grounded and labeled.** A directional range from a real basis (e.g. a free-tier estimate, or "≈, order-of-magnitude from employee count") is fine **if** tagged 🅢 soft with its basis. A precise-looking figure with no basis is not — "~2.3M monthly visits" pulled from nowhere is fabrication even if it "feels right."
- **Unknown data is handled cleanly — never with placeholder clutter.** If a metric can't be sourced, the skill does ONE of two things: (a) **state it as unknown in plain prose** where the absence is itself informative (e.g. "Their traffic isn't publicly disclosed; as a stealth-stage company that's expected"), or (b) **omit the point entirely** where a blank line adds nothing. It never litters the report with bracketed `[DATA NEEDED]`-style tags, and it never fills the gap with a guess. If knowing the gap matters to a decision, note the missing item once in the closing data appendix as something worth pulling — in prose, not as inline placeholders.
- **No fabricated quotes.** Filing/earnings-call language and customer reviews are paraphrased or quoted *only if real and cited*; the skill never manufactures a quote or attributes words to a person or company.
- **Tier discipline.** 🅗 hard (observed/sourced) · 🅢 soft (grounded estimate/sentiment) · 🅘 inferred (analyst judgment). Tiers are never blended silently, and an inference is never promoted to a fact.
- **If web/search/data tools aren't available in the run,** the skill says so plainly and limits itself to what it can source or reason about — it does not populate metrics from model memory (those are stale and unreliable) and does not present them as current.
- **Final self-audit (required before delivery):** run a `fact-check` groundedness pass — scan every number, quote, and named fact and confirm each binds to a cited source snippet or is stated as unknown. Anything untraceable is removed or rephrased as unknown. The data appendix lists sources + dates, and (in prose) the few unknowns worth pulling later.

## Sprntly integration (optional)
- **Inputs from Sprntly:** competitor mentions in lost deals, changelog/review ingestion, traffic/usage signals, and prior CIR runs from the knowledge graph (so trends are real deltas, not snapshots).
- **Outputs to Sprntly:** the report as a living entity; white-space + "exploit" items become ranked opportunities; sentiment themes feed `positioning`; the decisions register to the outcome graph; the refresh cadence becomes a monitored schedule.
- **Degrades to:** fully standalone — runs on web search + the data-sources workarounds, asking at most 1–2 scoping questions.

## Quality checklist (the bar)
- [ ] **One call, self-scoped** — the skill selected which stages to run (and skipped the rest with a stated reason), rather than running everything blindly or asking the caller to pick.
- [ ] **Us first** — our position and *strategy/goal* are established before any competitor, and every decision ties back to that goal.
- [ ] Scope is disciplined — 3–5 decision-shaping competitors, not the whole field.
- [ ] The arena includes **substitutes and future entrants**, not just direct rivals (Five Forces).
- [ ] Position is a **9-box with a verb** (invest/maintain/harvest/divest), not just a quadrant.
- [ ] Feature comparison is **by job-to-be-done**, not a feature checklist.
- [ ] "Who's winning" uses **momentum signals** (traffic, app, AI-search, ship cadence), not vibes.
- [ ] **No fabricated data** — every number, quote, price, and named fact cites a source + date, or is stated as unknown / omitted; nothing is invented "for illustration," no `[DATA NEEDED]`-style placeholder clutter, and the final self-audit was run.
- [ ] Every claim carries a **confidence tier** (hard/soft/inferred); tiers are never blended silently.
- [ ] It ends in **ranked decisions** (impact × feasibility, owners), and a **refresh cadence** is set.

## Known gaps / limitations
- The richest inputs (traffic, app downloads, share) come from paid tools; `modules/data-sources.md` gives free/low-cost workarounds, but those are *directional estimates* — labeled soft, never presented as precise.
- Strategy and "where they win" reads are interpretive (inferred tier); they're hypotheses to validate (e.g. via `red-team-review`), not facts.
- It structures and synthesizes intelligence; it can't manufacture data it wasn't given — missing metrics become open data-pulls, not guesses.
- A full run is heavy; for speed, run the skill at its "quick pulse" depth, or only the modules the decision needs.

## Worked example (public company, two-layer format)
**Input:** "Competitive intelligence review for Facebook / Meta."  *(Public company → financials from filings; no goal supplied, so the strategy layer is directional and says so.)*
**Output (abridged — note it leads with the TLDR + build decision, then the strategic evidence base):**

**TLDR (read first):** Core ad engine firing (Q1 2026 rev $56.3B, +33% YoY, sourced to the release/10-Q) — but two signals matter more for *what to build*: TikTok's Jan-2026 move to US ownership removed its ban risk (Meta itself calls it "highly urgent"), and Family DAP fell to 3.56B, the first-ever sequential decline. **Recs:** BUILD generative-ads depth + agentic AI in WhatsApp + Reels discovery-AI; SKIP all-AI feeds (Vibes "AI slop" backlash); REFRAME Reality Labs from VR to AI wearables.

**Part 1 — the build decision:**
- *Head-to-head scorecard:* win on ad-AI, messaging reach, AI wearables; lose on Gen-Z short-video discovery (TikTok) and the consumer assistant race (OpenAI).
- *Feature-gap matrix:* TikTok discovery algorithm → BUILD (sourced: "still considered superior"); AI-assistant monetization → BUILD (~600M MAU, ~no revenue); all-AI feed → SKIP (sourced backlash); AR/AI glasses → BUILD (7M+ units 2025).
- *What's heating up:* agentic AI (Meta positioning as the "OS for agents"), generative ads (millions of personalized variations), wearables over VR.
- *Build queue (impact × strength):* 1) generative-ads/Advantage+, 2) agentic assistant in WhatsApp, 3) Reels discovery-AI, 4) AI wearables. Skip Vibes; reframe Reality Labs.

**Part 2 — the strategic evidence base (kept in full):**
- *Us:* ~4B+ MAU; ads ~97–98% of revenue; projected to overtake Google on 2026 ad revenue; the DAP decline is the risk under the strong P&L.
- *Arena:* three fronts — social/attention (TikTok, urgent), advertising (Google primary; Amazon/TikTok taking share), AI assistants (OpenAI/Google).
- *9-box:* ads = invest; social engagement = invest-to-defend; assistant = invest; VR = harvest/reframe to wearables.
- *Momentum / money:* impressions accelerating; capex guided up to $125–145B (CAPEX-fatigue is the named investor risk); Reality Labs −$4.0B in the quarter; $81B cash funds the bet.
- *Integrity log:* financials hard-sourced to filings (🅗); scorecard / 9-box / build-ranking are judgment (🅘); per-competitor engagement-trend data flagged as the top open pull. No number invented; DAP decline's stated one-off cause (Iran/Russia) flagged rather than over-read.

*(The same structure runs for a private company — financials stage uses funding/scale proxies labeled 🅢 instead of filings, and the report still leads with the TLDR + build decision.)*
