---
name: evidence-brief
description: Synthesize data-science analysis, competitive analysis, voice of customer, and market signals — plus the business context and company goals you provide — into a single visual evidence brief that tells one data story and lands on a testable, value-driven hypothesis a product team can act on. Use when the user says "evidence brief", "insight brief", "make the case for", "what's the opportunity here", "synthesize these signals", "turn this analysis into an insight", or provides one or more analyses and wants the opportunity surfaced for a product team. Actively converges independent signals where they genuinely agree, picks the best chart per finding so the visuals collectively tell the story, never invents data or quotes, and produces the artifact that feeds prd-author. Body voice is a data scientist on the team; the title is a product-led strategic thesis.
---

# Evidence Brief — the data story that justifies a bet

## What this is used for
The evidence brief is the **upstream artifact in the product pipeline**: it turns scattered analysis into one defensible opportunity and a testable hypothesis, which then **feeds `prd-author`**. Its job is to help a product team decide *where to invest* — not to specify what to build (that's the PRD) and not to run the analysis (it synthesizes analysis that already exists). One brief = one opportunity = one hypothesis.

Pipeline: **signals + business context → `evidence-brief` → `prd-author` → `implementation-spec`.**

## When to use / when NOT to use
- **Use** to assemble signals (data science + competitive + VoC + market) into one case for a product team, or to convert a single analysis into a sharable opportunity.
- **Do NOT use** to write requirements (`prd-author`, run *after* this), to run the underlying analysis (this synthesizes, never fabricates), or to brief leadership on an external shock (`leadership-brief`).

## Inputs (the kind of information that goes in)
- **Required:** at least one signal — data-science analysis, competitive analysis, voice of customer, or a market signal — **and** the business context + company goal/North Star the opportunity must ladder up to.
- **Optional (each strengthens it; each absent is omitted, never invented):** more signal types, baselines/targets, segment cuts, prior experiments, the metric tree.
- **Hard rule:** use **only data the user provides.** Missing numbers are omitted or named as a gap — never estimated, rounded into existence, or inferred onto a chart. Quotes are used **only if real VoC is provided** and only where they align with the other signals.

## Method (run in this order — same inputs should yield the same brief)
1. **Frame from the goal.** One line: the behavior/outcome under investigation and the company goal it serves. Anchor here so "business value" later is concrete.
2. **Read each signal for its one finding** — the thing that matters, not a summary. For **competitive**, extract *where we're weak and why that's the opportunity*, never a feature checklist.
3. **Converge (the spine).** Find where **≥2 independent signals agree**. Genuine agreement among separately-gathered sources is the strongest part of the case — center it. **Do not force it:** if signals diverge or only one exists, say so and lower the confidence. Flag suspected shared causes (one signal counted twice ≠ convergence).
4. **Find the wedge** — the strongest single proof the opportunity is real (often a segment already behaving the desired way). State its strength honestly (correlational, small-n).
5. **Pick the best chart per finding and sequence them as ONE story** (see chart guide). Every visual must advance the narrative; cut any chart that's decorative or duplicative. The reader should be able to follow the argument through the charts alone.
6. **Voice of customer matters** — when real VoC exists and aligns, include actual quotes across channels (review / support / sales). Customer words turn a data point into a reason; they are not optional flavor when available.
7. **Reason toward the value-driven hypothesis (internal — do not render it).** Form: *"We believe that by introducing **[X]**, **[users]** will **[change in behavior]**, which will drive **[business value: retention / revenue / engagement / DAU / market share]**."* Behavior change and business value are named separately; value ties to step 1's goal. This hypothesis is the brief's analytical conclusion; it is **not** painted as a section of the brief (no hypothesis card / "input to PRD" block).
8. **Hand off to the PRD** — this hypothesis flows to `prd-author` through the pipeline (the shared KG hypothesis), not through a rendered card in the brief.
9. **Honesty pass (mandatory).** Every figure traces to a provided input; every quote is real; correlation is never called causation; the strength of agreement is conveyed in prose (never as a "Confidence:" label or score); non-converging signals are not hidden. If nothing can be supported, say the evidence is insufficient rather than manufacture a story.

### Chart guide (variable — best fit, not a fixed set)
| The finding is about… | Use |
|---|---|
| Change over time (retention, ratings, adoption) | line / area |
| Ranking or composition of categories (VoC themes) | bar (horizontal for labels) |
| Drop-off across stages (churn by step) | funnel / waterfall |
| Two-group comparison (segment A vs B, the wedge) | paired bars |
| Relationship between two measures | scatter |
| Capability gap vs competitors | matrix + an explicit "what we extract" |
| Multiple independent signals agreeing | **convergence diagram** (signals → one opportunity) |
Collectively the chosen charts tell **one** story; if two say the same thing, cut one.

## Output spec
A single **visual brief (HTML), 1–3 pages**, audience = **product team**, body voice = **a data scientist on the team** who found something worth investing in. Order:
- **Title** — a **product-led, strategic thesis** naming the lever and/or outcome (e.g. "Unlock Retention by Letting Beginners Speak", "The Two Drivers of Activation", "Five Signals, One Bet"). **Never a first-person/opinion line** ("I went looking…", "Some thoughts on…").
- **TL;DR** — the whole story in ~5 lines, for someone who reads nothing else.
- **Opportunity** — one simple line.
- **Context** — *after* TL;DR and Opportunity: 1–2 lines on what the product is and the behavior under investigation.
- **The evidence** — findings, each with its best-fit visual, sequenced as a data story; VoC quotes if real and aligned; competitive as an extraction.
- **Convergence** — the diagram/section of agreeing independent signals (or an honest note that they don't agree). The brief ends here.
- **No footer, no methods boilerplate, no machinery** (never mention agents, the platform, or how it was produced). **Do not render a hypothesis card or any "input to PRD" section** — the hypothesis is the brief's internal analytical conclusion (method steps 7–8) that hands off to `prd-author` through the pipeline, not a visible section of the brief.

## Output format — HTML rendering contract (mandatory)
The brief **is** rendered HTML, not a description of one. Every brief uses the **same shared design system** so a team reads each one the same way; only the content and the charts flex. **Render, don't drift.**

**Hard rules**
- Output is **one self-contained `.html` file**: a `<meta charset="utf-8">`, one inline `<style>` block, then one `<div class="wrap">` holding the brief. **No external CSS/JS, no web fonts, no chart libraries, no build step, no `<img>` for charts.** It must open correctly by double-clicking the file.
- **Use the canonical design system verbatim.** Copy the `<style>` block from `examples/01-lyra-language-app.html` (the most complete one) as your base and do not change the `:root` tokens, fonts, spacing, or any core component rule. You may *append* a small bespoke class when a brief genuinely needs one — never edit or remove the shared rules. Two briefs placed side by side must look like the same template.
- **Charts are hand-authored inline `<svg viewBox="…">`**, drawn directly from the provided numbers — never a JS chart, never a screenshot, never a placeholder. Use the chart-text classes (`.ax` axis labels, `.vlabel` value labels, `.blabel` bar/category labels) and the color tokens (`--problem` for the leak/problem, `--opp` for the opportunity/wedge, `--bar-neutral` for the comparison baseline, `--grid` for gridlines). Wrap each chart in `<figure>…<figcaption>` and keep `svg{width:100%}` so it scales.

**Section → required HTML component** (use these classes; this is what makes every brief render identically)

| Brief section | HTML component |
|---|---|
| Eyebrow / kicker line above the title | `<p class="eyebrow">Evidence Brief · <source> → <team></p>` |
| Title (strategic thesis) | `<h1>` + italic `<p class="deck">` subtitle |
| Author / date / status / "Pairs with → … PRD" line | `<p class="meta">` (and `<p class="demo">` only for illustrative/example data) |
| TL;DR | `<div class="tldr"><h4>TL;DR</h4><p>…</p></div>` |
| Opportunity (one line) | `<div class="opp-top"><span class="tag">OPPORTUNITY</span><p>…</p></div>` |
| Context | `<p class="context"><b>Context.</b> …</p>` |
| Each evidence finding | `<section>` → `<p class="kicker">` (use `.o` for opportunity-tone, `.n` for neutral) → `<h2>` → `<p>` → `<figure>` chart |
| VoC quotes | `<div class="voc">` of `<div class="q"><p class="ch rev|sup|sale">channel</p><p>quote</p></div>` |
| Competitive extraction | `<table>` with `.yes`/`.no`/`.us` cells, then `<div class="extract"><b>What I extract:</b> …</div>` |
| Convergence | `<section>` with an inline-SVG **convergence diagram** (signal nodes → one opportunity box), as in `01`/`05` |

**Do not render a hypothesis card.** Earlier versions ended the brief with a `<div class="hyp">` "input to PRD" card; that section is intentionally removed. The brief ends at convergence. The value-driven hypothesis (method step 7) is still reasoned internally and flows to `prd-author` via the pipeline — it is just not painted as a section of the brief.

If a section's signal is absent (e.g. no VoC, single signal), **omit that component** — never render an empty shell or invented filler. The five `examples/` are the authoritative rendering reference: match their markup, not just their wording.

## Repeatability
This skill is meant to be run the same way every time: the ordered method + fixed output sequence + chart guide make the brief reproducible across products and analysts. Given comparable scattered inputs, two runs should produce structurally identical briefs that differ only in their content — that consistency is the point, so a team learns to read every brief the same way.

## Integration & degradation (Sprntly)
- **Inputs from Sprntly:** DS / competitive / VoC / market analyses as signals; **business-context** (goals, North Star, segments) from the knowledge graph.
- **Output to Sprntly:** the hypothesis flows into `prd-author` as the grounded problem + evidence for Part A; the brief is the product team's shareable opportunity artifact.
- **Degrades to:** a single-signal brief — fewer convergence claims, more labeled gaps, lower stated confidence. With no goal given, it asks rather than assumes.

## Quality checklist (the bar)
- [ ] Title is a product-led strategic thesis, not a first-person/opinion line.
- [ ] Order is TL;DR → Opportunity → Context → evidence → convergence. **The brief ends at convergence — no rendered hypothesis card / "input to PRD" section.**
- [ ] Anchored to a stated company goal; the opportunity ladders up to it.
- [ ] **Every number traces to a provided input; nothing invented, estimated, or rounded into existence.**
- [ ] **Every quote is real VoC**, across channels, aligned with the other signals (or VoC omitted).
- [ ] Competitive is an **extraction** (where we're weak → the opportunity), not a checklist.
- [ ] **Convergence** of ≥2 independent signals surfaced where real; divergence/single-signal stated with lower confidence.
- [ ] Charts chosen per finding; collectively one sequenced story; none decorative/duplicate.
- [ ] Correlation vs causation labeled; the wedge's strength stated plainly.
- [ ] The value-driven hypothesis is reasoned internally (behavior change → business value, testable) and handed to `prd-author`, but is **not rendered as a card/section in the brief**.
- [ ] ≤ 3 pages; data-scientist body voice; product-team audience; **no footer/methods/machinery.**
- [ ] **Renders as one self-contained HTML file** (inline `<style>`, single `.wrap`, no external CSS/JS, no chart libs); opens by double-click.
- [ ] **Canonical design system used verbatim** (tokens/fonts/core classes from `examples/01` unchanged); each section uses its required component class.
- [ ] **Every chart is hand-authored inline `<svg>`** from the provided numbers, in a `figure`/`figcaption`, using the `.ax/.vlabel/.blabel` + color tokens — no JS charts, screenshots, or placeholders.

## Known gaps / limitations
- Synthesis layer, not an analysis engine — it structures and visualizes provided analysis but cannot validate the underlying numbers (garbage in, garbage out).
- Convergence is only as independent as the sources; signals sharing a root cause can masquerade as agreement — note suspected shared causes.
- The wedge is usually correlational; the brief should drive an experiment, not a launch.
- Without a stated goal it cannot judge business value; it asks, it doesn't assume.

## Worked example (abridged — Lyra, a language app)
**Inputs:** DS retention analysis; VoC (reviews + support + sales notes); competitive teardown; market note on AI voice; goal = subscription retention/LTV.
**Output:** Title "Unlock Retention by Letting Beginners Speak" → TL;DR → Opportunity → Context. Findings: retention cliff at the intermediate gate + the speaking-cohort wedge 2.3× labeled correlational (line + paired bars); VoC across three channels, "no way to speak" as the aligned theme (quotes + bar); competitive **extraction** (rivals invest where we leak). **Convergence:** data + VoC + sales + competitive + market all point to speaking practice at the plateau (diagram) — the brief ends here. The internal hypothesis ("By introducing adaptive conversation practice at the transition, learners will start speaking early and feel progress, which will drive week-4 retention and renewals (LTV)") is what feeds the "Conversation Practice" PRD through the pipeline, but is not rendered as a card. No footer.

## Reference examples
Five worked briefs ship in `examples/` (see `README.md` for the index). They hold the structure constant while varying business, charts, available signals, and convergence strength — including honest weak-convergence (`03`) and single-signal degradation (`04`). Read them to calibrate before generating.
