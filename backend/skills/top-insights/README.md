# Top Insights — usage guide

> **Implementing this? Read `BUILD-BRIEF.md` first.** It lists the five user inputs, the adapter contract, the state to persist, the routes and events needed, and every open question — written for someone who wasn't part of the design conversation.
>
> **Want to see the output?** Open `top-insights-brief-format.html`.

This file orients any LLM or agent that needs to *run* this skill. `SKILL.md` is the authoritative spec; this README is the front door.

> **Renamed from `weekly-brief`.** The cadence is now a config value (daily, weekly, monthly), not a name — and the skill now *fetches* from upstream analysis skills rather than being handed signals.

## What it does

Assembles a decision-ready PM digest from the analysis skills the recipient subscribed to: a short cadence-aware opening, ranked recommendation cards, and a report shelf linking to the standing reports behind them. **Every run renders an HTML file** in the existing brief template — that is part of what it means to have run the skill, not an optional extra. Each card names something the reader didn't know, sizes what's at stake, and shows what the finding rests on. **The skill reports; the PM decides** — evidence leads, and the PRD is generated once they agree.

**The rendered output is the existing weekly-brief design, unchanged** — same greeting, same cards, same chips, same paired CTAs. Everything new here happens upstream of the render.

It does **not** run analysis or compute metrics. It fetches, merges, gates, ranks, and phrases. Every number in the output comes from an upstream skill.

## The two things that make it non-trivial

1. **Numbers are inputs.** No figure appears that doesn't trace to an upstream field. This is enforced by blocking linters, not by good intentions.
2. **Report, don't prescribe.** A card's job is to inform well enough that the reader can form their own view. The primary CTA goes to the evidence or the parent report; the PRD sits in the ghost slot as *Generate PRD*.
3. **The reader's stated criteria drive selection — not inferred ones.** Five or six sources at five to ten findings each means forty to sixty candidates for three to five slots. A stored selection profile does the narrowing: hard filters exclude, weighted preferences reorder, a focus tilts toward the current goal. Behavior may *propose* changes to that profile; it may never apply them silently.
4. **A card slot must be earned by news.** Sources refresh at different rates (reliability daily, customer problems weekly, competitive monthly) while the recipient may read daily. Anything unchanged since they last saw it is simply not surfaced — it stays ranked on the backlog page. Without this, a daily reader sees the same monthly competitive card 30 times and stops opening the digest.

## Categories

| Category | Fed by | Refreshes |
|---|---|---|
| `customer_problems` | `voice-of-customer` + `feedback-synthesis` + `interview-synthesis` | weekly |
| `competitive` | competitive intelligence skill | monthly |
| `reliability` | monitoring analysis *(planned)* | daily |
| `core_metric` | metric movement *(planned)* | daily |
| `celebrate` | ship impact *(planned)* + resolved findings from this skill | weekly |
| `what_to_build` | synthesis across all of the above | per brief |

Recipients subscribe to any subset. Planned categories return nothing and are never mentioned — the skill runs correctly today and lights up as adapters land.

## How to use it — the loop

1. **Read `SKILL.md` in full.** It is the contract: config → fetch → merge/decompose → gate → profile → freshness → prioritize → greeting → cards → self-critique → render.
2. **Read `references/sources.md`.** Adapter contracts, channel authority rules, and the open `⟨TBC⟩` items.
3. **Read `references/signal-schema.json`.** `brief_config`, `finding`, `ledger`, `brief`, `backlog`. Confirm each numeric you'll surface has a backing field — if it doesn't exist, you will not invent it.
4. **Resolve config, then fetch** every subscribed category in parallel. Never block the brief on one slow source.
5. **Merge**, cross-referencing the three feedback channels per the channel-authority table.
6. **Gate and assign freshness state** against the ledger — this is where staleness is prevented.
7. **Compose**, then **self-check** against `references/rubric.md` — blocking linters first, then the scored rubric; rewrite any card that fails a hard gate once. Compare voice to `references/examples.md`.
8. **Render** `assets/brief-template.html`, and emit the `brief`, the updated `ledger`, and the ranked `backlog`.

## Selection at a glance

Two questions, kept separate. The **gate** asks *is this finding sound?* — objective, same answer for every reader. The **profile** asks *does this reader want it?* — subjective, and only this one is overridable by the reader.

- **Hard filters** exclude absolutely; excluded findings land on the backlog with their filter label, never silently discarded.
- **Preferences** reorder via multipliers clamped to 0.5–2.0. They never exclude — anything wider is a filter in disguise.
- **Focus** gives a modest lift to findings bearing on the current core goal.
- **Severity override**: maximum-severity findings bypass the profile entirely and say so on the card. Should fire a handful of times a year, not weekly.
- **Three reader actions**: dismiss (not interested), defer (interested, wrong moment — returns at full rank after one refresh interval), act (taken up). All vacate the slot; refill arrives next cycle, not live.

## Anti-staleness at a glance

- **Report shelf:** the one place a report sits unchanged between runs, because it's navigation rather than news. Rendered below the cards, every row showing when it ran, never competing for a card slot.
- **Freshness states:** `new` and `updated` get cards; `carried` and `in_progress` are not surfaced and stay on the backlog; `resolved` vacates and becomes a celebrate candidate.
- **Material change** = pain ±15%, reach ±25%, urgency tier change, value ±20%, or status change.
- **Cadence lanes:** `refresh_interval ÷ brief_interval`. Ratio ≤ 1 → can card every cycle. Ratio > 1 → cards only in the cycle after its source refreshes.
- **Rotation exhaustion:** three showings, no action, no change → retired to backlog; returns only if it worsens.
- **Resolution advances the queue:** acting on a card promotes the next backlog item into the brief.
- **Floor promotion:** at most one carried item may fill an otherwise-empty brief, framed by its age. Never padding.

## Input contract (quick reference)

`brief_config`: recipient, cadence, subscribed categories, core goal, company scale, confidence floor — plus the `ledger` from the previous run.

Each adapter returns `{ category, source_skill, as_of, refresh_interval_days, available, findings[] }`. Each `finding` carries `id` (**must be stable across runs**), category, type, pain, value (with `basis`), story, recommended action, artifact refs, sources, evidence, confidence, urgency, reach, `as_of`, and status. For `customer_problems`, also `channels_present`, `cluster_members`, `theme_key`, and `tension`.

## Output contract (quick reference)

A `brief` object: recipient, cadence, greeting, and `cards[]` (each with category, type, accent, state, title, body, sources, exactly two CTAs, finding backlink, and an `audit` block recording figure provenance and why it surfaced). Plus the updated `ledger` and the full ranked `backlog[]`. The HTML render is a view of this object.

## Quality gates (must pass before emitting)

Full list in `references/rubric.md`. The load-bearing ones: every figure traces to a source; greeting total equals the sum of card figures only; cadence framing matches cadence; no `carried` finding rendered as a card; slow-lane sources only card after a refresh; ≤1 floor promotion; titles carry pain and value; bodies ≤4 lines and read with the title removed; accent matches type and valence; exactly two CTAs from the category's allowed pair; no public-channel stat presented as your customer base; nothing surfaced from an unsubscribed category; nothing said about an unwired one; every gated finding lands in the backlog with a reason.

Also: run the **daily-cadence stress test** in the rubric before shipping changes. Seven consecutive daily briefs over a fixed corpus should get quieter, not repeat.

## Running it — the short version

1. Load `brief_config` for the reader: categories, cadence, core goal, selection profile, company scale, plus the `ledger` from last run.
2. Call each subscribed category's adapter in parallel. Nothing blocks on a slow source.
3. Merge, decompose reports into findings, gate, apply the profile, assign freshness state.
4. Rank, allocate slots, write the greeting and cards.
5. Self-check against `references/rubric.md`, rewrite any card that fails a hard gate once.
6. **Render the HTML** — every run produces one, including quiet ones — and emit the `brief` object, the updated `ledger`, and the `backlog`.

## Files in this skill

- `BUILD-BRIEF.md` — **for implementers.** Inputs, contracts, state, routes, events, open questions.
- `SKILL.md` — authoritative spec and workflow. Read first if you're running the skill.
- `references/sources.md` — category registry, adapter contracts, cadence table, open questions.
- `references/signal-schema.json` — config, finding, ledger, brief, backlog structures.
- `references/rubric.md` — blocking linters + scored rubric + the daily stress test.
- `references/examples.md` — goldens (cross-channel, competitive pointer, updated card, quiet day) and annotated counter-examples.
- `assets/brief-template.html` — canonical render template; all visual tokens live here.
