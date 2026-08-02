---
name: top-insights
description: Generate a PM's Top Insights digest — a short cadence-aware opening plus ranked recommendation cards, each leading to its action CTAs — by fetching from the analysis skills the recipient has subscribed to. Covers top customer problems (voice-of-customer + feedback synthesis + interview synthesis), competitor and market moves, reliability and incidents, core metric movement, ways to celebrate, and a synthesized "what to build next." Use this skill whenever you need to assemble a daily, weekly, or monthly digest, home-screen recommendations, or any "what should the PM act on now" summary — even when the user only says "make the brief," "top insights," "surface recommendations," or "what's worth my attention."
---

# Top Insights

Produce a digest a busy PM can read cold and act on: a short cadence-aware opening, then a ranked set of cards. Each card names something the PM didn't know, sizes what's at stake, and shows what the finding rests on — so they can judge it for themselves and decide what to do. **The skill reports; the PM decides.**

**The output is the existing brief, unchanged.** Greeting, cards, source chips, paired CTAs, dismiss-and-undo — all exactly as they were. Everything new in this skill is upstream of the render: where the findings come from, how they're merged, and what earns a slot. A reader should notice better and fresher cards, not a new layout.

This skill runs at whatever cadence the recipient chose (daily, weekly, or monthly) over whatever categories they subscribed to. Those two facts change almost everything downstream, so resolve them first.

## The two principles that make this reliable

**1. Numbers are inputs, never outputs.** Every figure — the pain stat, the value of acting, the totals in the greeting — must come from an upstream analysis skill. This skill *fetches and phrases*; it never computes or invents. If a source produced no impact figure, do not manufacture one (see Edge cases). This prevents the most damaging failure: a confident headline built on a made-up number.

**2. A card slot must be earned by news.** The recipient may see this every day while some sources only refresh monthly. A finding that has not changed since the recipient last saw it does not get to occupy a card again — it simply isn't surfaced, and stays ranked on the backlog page where the reader can find it. Repetition dressed as freshness is the fastest way to lose a daily reader. Restraint is a feature: a quiet day is a short brief, not a padded one.

**3. Report the finding, don't prescribe the fix.** The PM is the one who decides what to build. A card's job is to tell them something they didn't know, size it, and show its basis well enough that they can form their own view. Leading with "we've drafted the PRD, approve it" skips the step where they judge whether the problem is real — and readers experience that as being sold to rather than informed. The evidence comes first; the PRD is something they generate once they agree.

Everything that shouldn't vary (layout, colors, taxonomy, CTA placement) is already pinned in `assets/brief-template.html`, which the frontend implements. You generate only the prose.

## Categories

The recipient subscribes to any subset. Each category has its own upstream source, its own refresh interval, and its own CTA pair. Full source registry and adapter contracts: `references/sources.md` — **read it before fetching.**

| Category | Section label | Fed by | Refreshes | Status |
|---|---|---|---|---|
| `customer_problems` | Top customer problems | `voice-of-customer-report` (weekly) + `public-feedback-report` (monthly) + `interview-synthesis` (ad hoc) | mixed | live |
| `competitive` | Competitor & market moves | `competitive-intelligence-review` | monthly | live |
| `reliability` | Reliability & incidents | monitoring-analysis adapter | daily | planned |
| `core_metric` | *the recipient's metric name* | metric-movement adapter | daily | planned |
| `celebrate` | Worth celebrating | ship-impact adapter (+ resolved findings from this skill) | weekly | planned |
| `what_to_build` | What to build next | synthesis across all of the above | per brief | live |

Planned categories degrade silently: if the adapter isn't wired, the category contributes nothing and is never mentioned. Do not apologize for a missing source in the brief.

`what_to_build` is different in kind — but not in appearance. It consumes the findings from every other category and answers one question: given the recipient's stated core business goal, what are the top three to five things to build? It runs **last**, after all other categories are gated and ranked, because it reasons over their output. Its items then render as **ordinary cards**, ranked among the rest, tagged by the type of the finding each rests on. No separate block, no special layout.

## Type taxonomy (drives accent color)

Category is *what the recipient subscribed to* and which adapter fetched it; type is *the nature of the finding*, and it sets both the accent color and the tag on the card. Category is routing metadata — it governs fetching, capping, and the audit trail, and it never appears on the card. The rendered pill stays exactly what it was: Reliability, Competitive, Growth, Customer demand, Engagement. Color must match valence — **never a gain color on a loss.**

| Type | When | Accent |
|---|---|---|
| Reliability | bugs, breakage, latency, broken data/tracking | `#c0473c` clay |
| Retention | churn, downgrades, satisfaction drops, complaint spikes | `#b23b52` rose |
| Competitive | a rival move threatening share or renewals | `#b07a2e` ochre |
| Growth | expansion, upsell, new revenue, pricing | `#1a8a52` green |
| Demand | feature asks, sales-driven requests, latent needs | `#5f57a6` iris |
| Engagement | activation, adoption, retention behavior | `#3f63a0` slate blue |
| Compliance | regulatory change, audit/data-residency risk | `#4f5675` deep slate |
| Momentum | a win worth celebrating, favorable metric movement | `#0f7d70` teal |

`competitive` and `reliability` categories always take their matching type. `customer_problems` takes retention, demand, or engagement depending on the finding. `celebrate` always takes momentum. `core_metric` takes momentum when the movement is favorable; when it isn't, it takes the type of the dominant driver named in the analysis, defaulting to engagement. `what_to_build` items inherit the type of the finding they rest on.

Adding a type is a deliberate edit here — not a model choice at runtime.

## Workflow

### 0. Resolve config

Read the `brief_config`: recipient name, company scale, `cadence` (`daily` | `weekly` | `monthly`), subscribed categories, core business goal, the **selection profile** (what this reader wants and doesn't), and the `ledger` of what was shown before. Derive `brief_interval_days` = 1 / 7 / 30. Nothing downstream is correct without these.

### 1. Fetch

For each subscribed category, call its adapter (`references/sources.md`). Fetch in parallel; never block the brief on one slow source. Each adapter returns `finding` objects carrying `as_of` — when the upstream analysis actually ran, not when you fetched it.

If a source returns nothing, is unreachable, or hasn't run since the recipient subscribed, that category contributes zero findings and is not mentioned. If **every** source is empty, emit the quiet-cycle brief — do not retry into invention.

### 2. Normalize and merge

Map each adapter's output into the `finding` shape (`references/signal-schema.json`). Then merge:

- **Dedupe within a category** — several angles on one issue is one finding, not three.
- **Cross-reference `customer_problems` across its three channels.** This is the highest-value merge in the skill and has its own rules below.
- **Cross-reference across categories** — if the competitive report and the customer feedback describe the same gap, that's one finding with both provenances, ranked once.

#### Composing a cross-channel customer problem

The three feedback skills are good at different things, so each supplies a different part of the card:

- **`voice-of-customer-report`** (internal: support, Slack, calls) → the **pain stat and reach.** These are your own paying customers, so this is the only channel allowed to supply a headline number about your customer base.
- **`interview-synthesis`** (direct conversations) → the ***why***. Low volume, high depth. It supplies the story beat — the cause the other two channels can only guess at.
- **`public-feedback-report`** (Reddit, social, review sites) → **breadth and velocity.** How loud, how fast-growing, whether it's spreading beyond your own base. It must never supply the primary pain stat about your customers — a public forum is not your customer list.

Rules for the merge:
- **Never average figures across channels.** If two channels give different numbers, use the one whose population matches the claim being made, and name that population in the body ("among your enterprise accounts…").
- **Corroboration raises confidence, honestly.** Merged confidence = the highest channel confidence, +0.05 per additional corroborating channel, capped at 0.95. Record `channels_present`.
- **Source chips show only the channels that actually carried it.** One channel means one chip. Never imply convergence that didn't happen.
- **Conflict is content, not noise.** If interviews say one thing and public feedback says another, populate `tension` and narrate it in the body ("your own customers rank this third; publicly it's the loudest complaint"). Don't silently pick a winner.

#### Reports decompose into findings

Most upstream skills produce a **report** — a monthly competitive review, a monthly public-feedback synthesis, a weekly voice-of-customer report — that contains several findings. Never surface the report as a standing item; it will go stale for its whole interval. Instead:

- **The cycle the report lands**, its strongest finding cards, and *that card* carries the report link. Its body says what else the report covers, so the reader knows there's more behind it.
- **Over the rest of the interval**, the report's other findings surface one at a time on their own merits. Each carries the same link back to its parent report.

The report stays reachable from every card that came out of it, and nothing repeats. A month of competitive analysis becomes a month of fresh cards rather than one card shown thirty times.

**Release pace.** Don't dump a report's findings in the first two cycles and then go silent. From any one report, the number of findings that may card per cycle is:

```
per_cycle = ceil( findings_count ÷ (refresh_interval_days ÷ brief_interval_days) )
            floor 1, capped at 2
```

A six-finding monthly report on a daily brief releases roughly one finding every five days. On a weekly brief, one or two a week. On a monthly brief it lands at once, which is right — the reader isn't back until next month. **Urgency bypasses pacing:** a high-urgency finding cards immediately regardless of the release schedule. Pacing governs the tail, never the alarm.

**Report links open in a new tab.** The reader leaves the brief to read the full analysis and comes back; nothing in the brief should try to reproduce the report inline.

#### The report shelf

Decomposition governs *cards*. Separately, the brief carries a **report shelf** at the bottom: a quiet row of links to every standing report behind the reader's subscribed categories — the competitive review, the voice-of-customer report, the public-feedback synthesis, the latest interview synthesis.

This is the one place a report is allowed to sit unchanged between runs, and it works precisely because it isn't a card. It's navigation. A reader who thinks *"wasn't there something about pricing in the competitive review?"* should be able to get there from any brief, on any day of the month, without the brief having to re-surface the finding.

**One line per report, and the line has to earn its click.** A bare link tells the reader a report exists; it doesn't tell them whether to bother. So each row carries the report's own one-sentence takeaway:

> **Competitive review** — Two rivals moved on pricing this month, and the sharpest gap is now in enterprise tiers. · Ran 1 Jul · View more
>
> **Voice of customer** — Export reliability displaced billing as the top theme across your support queue. · Ran 21 Jul · View more

**The sentence comes from the report's own summary — this skill never composes it.** That's the grounding rule applied to the shelf: writing a fresh characterization of a report you didn't read is exactly how a confident, wrong summary gets in front of a PM. The adapter returns the report's top-line; the shelf renders it. If a report has no summary line, render the row without one rather than inventing it.

Keep the sentence high-level and figure-light. It's an orientation, not a finding — anything with a number worth acting on should have been a card.

Three more rules keep it honest:

- **Every row shows when it ran** — "Ran 1 Jul." The staleness is stated rather than implied, which is what makes a month-old link acceptable in a daily digest.
- **It renders below the cards, always**, in the same position. It's a shelf, not a slot; it never competes for ranking and never counts toward the card budget or the greeting totals.
- **Only subscribed, available reports appear.** No row for a category the reader dropped, and no row for a skill that hasn't run — an empty shelf renders nothing at all rather than a placeholder.

If a reader is subscribed only to categories with no standing report behind them, the shelf is omitted entirely.

### 3. Gate

Restraint is part of quality; a digest full of noise loses trust faster than a sparse one.

- **Confidence floor** — drop anything below 0.6 (configurable) or without at least one concrete piece of evidence.
- **Staleness** — drop anything already resolved or shipped.
- **Dismissal memory** — if it was dismissed and nothing material changed, suppress it. If it got materially worse, resurface and say so.
- **Rotation exhaustion** — if it has appeared as a card three times with no user action and no material change, retire it to the backlog (`rotation_exhausted`). It returns only on a material change. Nagging is not persistence.
- **Scale threshold** — down-rank or drop impact that is a rounding error against company scale.

Everything gated out goes to `backlog[]` with its reason. Nothing is lost; it's just not on the brief.

### 4. Apply the reader's selection profile

Step 3 asked *is this finding sound?* — an objective question with the same answer for everyone. This step asks *does this reader want it?* — a subjective question whose answer is theirs, not yours. Keep the two separate: a finding that fails the gate is bad, a finding that fails the profile is merely not for them, and only the second can be overridden by the reader changing their mind.

Across five or six sources at five to ten findings each, expect **forty to sixty candidates** reaching this step and three to five slots at the other end. The profile is what does the narrowing, and it has three parts applied in order.

**a) Hard filters — what never appears.** Absolute exclusions the reader has stated: categories they've unsubscribed from, impact below a floor they've set, surfaces or segments they don't own. A filtered finding goes to the backlog with reason `excluded_by_profile` — never silently discarded, because the reader may want to revisit the filter.

**b) Weighted preferences — what ranks higher.** The reader's stated priorities, expressed as multipliers on the base score. If they've said enterprise churn matters more than SMB activation, enterprise churn findings rank above equally-scored activation ones. Preferences reorder; they never exclude. A finding the reader deprioritized still appears if nothing better exists.

**c) Focus — what this quarter is about.** A standing emphasis tied to the core goal. Findings that bear on the current goal get a modest lift, and the `what_to_build` category reads directly from it.

```
adjusted = base_priority × Π(preference_multipliers) × focus_multiplier
```

Multipliers stay in the 0.5–2.0 range. Anything wider and the profile stops being a preference and becomes a filter, which belongs in (a) where the reader can see it plainly.

#### The severity override — the profile cannot hide a crisis

A reader who writes "only show me growth work" must still be told about a data-residency breach or a total checkout outage. **Any finding at maximum severity bypasses hard filters and preference weighting entirely**, and its card says why it's there despite the profile: "Outside your usual filters, but this one warrants it."

This is the one place the reader's stated preference is overruled, and it should be rare — no more than a handful of times a year. If the override fires weekly, the severity bar is set wrong. The whole point of the skill is telling someone what they don't know; a profile that could suppress anything would defeat it.

#### Stated criteria beat learned criteria

It's tempting to infer the profile from behavior — quietly down-weight whatever the reader dismisses, up-weight whatever they act on. **Don't.** A digest that learns to show only what you already engage with stops being able to tell you what you don't know, which is the entire reason it exists. The narrowing is invisible, compounds every cycle, and by the time it's obvious the reader has been in a bubble for months.

Behavior is still useful — as a *prompt*, never as a silent adjustment:

- Three dismissals of the same kind of finding → surface a suggestion: "You've passed on the last three onboarding findings. Want to down-weight them?" The reader decides.
- A category acted on repeatedly → suggest raising its weight.
- Every profile change is explicit, recorded with a timestamp, and reversible.

The reader's stated criteria are the contract. Behavior can propose changes to that contract; it may not amend it.

### 5. Assign freshness state — the anti-staleness engine

This is what keeps a daily brief from repeating itself. For each surviving finding, compare against the `ledger` and assign one state:

| State | Condition | Gets |
|---|---|---|
| `new` | never surfaced before | card |
| `updated` | surfaced before **and** materially changed | card, framed as the change |
| `carried` | surfaced before, no material change, still open | not shown — stays ranked on the backlog |
| `in_progress` | PRD approved / prototype generated / patch merged | not shown — backlog, marked in progress |
| `resolved` | fixed, shipped, closed, or recovered | not shown; promoted to `celebrate` candidate |

**Material change** = the pain stat moved ≥15%, or reach moved ≥25%, or urgency changed tier, or the value figure moved ≥20%, or status changed. Thresholds are configurable per category.

**Cadence lanes.** Compute `cadence_ratio = source_refresh_interval_days / brief_interval_days`.
- **Fast lane** (ratio ≤ 1): the source refreshes at least as often as the brief, so it can produce a fresh card every cycle. Reliability and core metric on a daily brief; customer problems on a weekly one.
- **Slow lane** (ratio > 1): the source refreshes less often than the brief. It gets a card **in the cycle immediately after its source refreshes** and is not surfaced again until the next refresh. On a daily brief, the monthly competitive report is a card the day it lands and absent for the next 29 days — still on the backlog, just not taking a slot it hasn't earned.

The lane rule is what makes a monthly source survivable in a daily digest. Do not fight it by re-phrasing old findings to seem new.

**Resolution advances the queue.** When something resolves or moves to in-progress, it vacates its slot and the next-ranked backlog item is promoted into the brief. That is the intended loop: acting on a card makes room for the next one.

#### Three reader actions, three different meanings

The reader can do three things to a card, and conflating them is how a digest becomes annoying. Each writes a distinct entry to the ledger:

| Action | Means | Behavior |
|---|---|---|
| **Dismiss** (×) | Not interested | Suppress permanently unless it materially worsens. Counts toward a profile suggestion after three of a kind. |
| **Defer / not now** | Interested, wrong moment | Suppress for a set period (default one full refresh interval of its source), then re-enter the pool at full rank. Not a dismissal — never counts toward a profile suggestion. |
| **Act** (generate or approve a PRD) | Taken up | Move to `in_progress`, vacate the slot, stop surfacing. Returns only as a `celebrate` candidate once resolved. |

All three vacate the slot, and the next-ranked backlog item is promoted on the following run. **Refill is not live** — dismissing a card grays it in place with Undo, exactly as before; the replacement arrives next cycle. A card sliding in underneath a dismissal would make the brief feel like an inbox that never empties.

Deferral is the one most worth having. Without it, "not this week" and "never" collapse into the same button, and readers stop dismissing anything for fear of losing it.

**Floor promotion.** If the brief would otherwise have zero cards, one `carried` finding may be promoted — highest urgency, unaddressed — and its body must open with its age rather than as news: "Still open, and now in its ninth day." Never promote more than one, and never to pad a thin brief to a target count.

### 6. Prioritize

```
priority = 0.32·impact_norm + 0.20·confidence + 0.18·urgency + 0.12·reach_norm + 0.18·freshness
```

`impact_norm` is the value of acting normalized to company scale — $2.2M is a five-alarm fire at one company and a rounding error at another. `freshness` = 1.0 for `new`, 0.6 for `updated`, 0.2 for a floor-promoted `carried`. Break ties by urgency, then confidence.

**Slot budget.** Daily: 1–5 cards, default 3. Weekly: 3–7, default 5. Monthly: 3–7, default 6. At most 2 cards from any one category unless the recipient subscribed to only one. `celebrate` gets at most 1 card and never position 1 unless it's the only card. `what_to_build` items compete for the same card slots as everything else; when a build recommendation rests on a finding already carded above it, keep the finding's card and drop the duplicate rather than showing both.

### 7. Write the greeting

Address the recipient by name. Lead with the work done and the upside on the table, roll up the totals, name the top plays. Frame it as money to go capture, not fires to put out. **The time framing must match the cadence** — never say "this week" in a daily brief.

- *Daily* — what moved since yesterday: "Good morning, David — two things moved overnight. The larger one is a checkout failure now costing about **$2.2M a year**."
- *Weekly* — the week's upside: "Good day, David — I've scouted everything across your tools, and there's real upside on the table this week: roughly **$60M within reach**. The clearest signals are a gap a competitor just opened, **$8.4M** sitting with accounts that have outgrown their plan, and friction costing your highest-spend users. Five ranked below; the top three carry the most."
- *Monthly* — the month's picture, with the competitive report as an anchor.

Maximum three lines. Totals must equal the sum of figures in the cards. On a quiet cycle, say so plainly and keep it to one line: "Quiet day, David — nothing new crossed the line since yesterday." Never manufacture urgency.

### 8. Write each card

**Title — finding, then stake.** State what you found with a concrete number, then how much is riding on it. Same two-part shape as before; what changes is that the second half sizes the *problem* rather than promising the reward of a fix.

- Reliability: *A login bug is failing 1 in 6 iOS checkouts — roughly $2.2M a year is going with them.*
- Customer problems: *Export failures are now your most-raised issue — and $4.1M in renewals sit with the accounts hitting them.*
- Growth: *42 accounts have outgrown their plan — $8.4M in expansion is sitting unclaimed.*

Keep it tight (~16 words). **If the source gave no figure, size it qualitatively** ("…and it's the biggest drop in the funnel") — never invent a number. The stake is still a projection and must arrive with a basis attached; a fabricated number does the most damage here, because the headline is the most persuasive line.

Avoid verbs that presume the solution — *fixing it protects*, *the fix recovers*, *claiming them adds*. Those tell the PM what to do before they've decided the finding is real.

**Body — self-contained, max 4 lines, three beats.** It must read completely with the title removed:

1. *What's happening* — name the subject explicitly (not "it has been live three weeks," but "a checkout failure has been live three weeks").
2. *What's at stake* — the impact, bold the headline number.
3. *What this rests on* — the basis for the claim, stated plainly: "Drawn from 340 support tickets, three interviews, and a public thread." Or for a report-backed finding: "This month's review covers four more moves; this is the one with a number attached."

Beat 3 is the change from the old version, and it's the one that matters. It used to be "we've drafted the fix, approve it." Now it's the evidence base — enough for the reader to judge whether they believe you. The CTA does the inviting; the prose doesn't need to tell them to click.

For an `updated` card, the first beat becomes *what changed*: "Flagged last Tuesday at 40 accounts — now 96." The change is the reason it's back; say so.

Do not enumerate source tools in the prose — the chips carry provenance. Exception: for a cross-channel customer problem, naming *which kind* of channel is content, not plumbing ("your own customers rank it third; publicly it's the loudest complaint").

**Sources and CTAs.** A quiet "From" row of source chips, honest to the real sources. Then exactly two CTAs, primary then ghost, in the same place on every card, drawn from this table:

| Case | Primary | Ghost |
|---|---|---|
| Finding came from a report | View the full report | Generate PRD / View PRD |
| Everything else | View the evidence | Generate PRD / View PRD |

**Evidence leads, the PRD follows.** The primary CTA takes the reader to what the finding rests on — the parent report where one exists, otherwise the evidence view. The PRD sits in the ghost slot and reads **Generate PRD** when none exists yet, **View PRD** when one does. This is the CTA expression of the insight-first posture: the reader investigates, then decides, then generates.

Report links open in a new tab. Every finding has an `evidence` array — it's a required field — so the primary CTA always has somewhere to go.

Do not add labels for capabilities that don't exist yet. When the reliability skill can generate a patch, add its label then.

The View/Draft and View/Generate variants switch on whether the artifact exists — never link to nothing. Each card carries a regenerate icon and a dismiss (×) top-right; dismissing grays the card with an Undo.

**The competitive card is a pointer, not a summary.** The monthly report is long and lives elsewhere. The card carries one headline finding and its value, and sends the reader to the report for the rest. Don't try to compress a month of competitive analysis into four lines.

### 9. Self-critique, then revise once

Score the draft against `references/rubric.md`. If any hard gate fails — a number without a source, a body that needs its title, a color mismatching valence, a wrong CTA pair, a title missing pain or value, a carried finding rendered as a card, a greeting whose time framing contradicts the cadence — rewrite that card once and re-check.

### 10. Emit

**Emit the structured `brief` object (`references/signal-schema.json`), and nothing else.**
That object IS the brief. Sprntly's frontend renders it into the pinned layout — you neither
produce nor see HTML, and the call that runs this skill is a forced-tool JSON call, so there is
no channel through which markup could reach anyone even if you wrote it. Prose is your entire
output surface; spend it on the cards.

Layout, tokens, fonts, the green CTAs, chips, the shelf, and the dismiss/undo behaviour are all
pinned in `assets/brief-template.html`, which the FRONTEND implements. Nothing you emit can
restyle it, so do not try — write the slots and let the renderer place them.

On a quiet cycle you still emit a complete object: greeting, no cards, and the shelf. A brief
with zero cards is a valid brief. Also emit the updated `ledger` (what was shown, in what state,
when) and the full ranked `backlog[]` — the backlog page reads from that, and the next cycle's
freshness logic reads from the ledger. Everything not carded this cycle is on the backlog, so
nothing is lost by leaving it off the brief.

## Edge cases

- **No value figure:** qualitative value clause, never a fabricated number.
- **Single channel on a customer problem:** one honest chip; don't fake convergence. A 1,000% complaint spike is allowed to stand alone.
- **Channels disagree:** narrate the tension; never average.
- **Source unavailable / category not yet wired:** contribute nothing, say nothing.
- **Quiet cycle:** greeting only, one line, no cards. Never pad to a target card count.
- **Daily cadence, slow-lane sources only:** most days will be quiet by design. That is correct behavior, not a failure; if it happens every day for a week, that's a signal to suggest a weekly cadence, not a signal to invent cards.
- **Everything resolved at once:** one celebrate card and nothing else.
- **Dismissed before:** suppress unless materially worse, then resurface with the reason.
- **Rotation exhausted:** retire to backlog; return only on material change.
- **Non-monetary impact:** the value clause flexes to %, points, time saved, NPS — the pain-then-value shape stays the same.
- **Too many high-severity at once:** calibrate; crying wolf every cycle destroys the signal.

## Guardrails

- Figures are inputs; prefer ranges over false precision; every figure traces to a named source.
- Honor the surfacing gate and the freshness rules — quality includes staying quiet.
- Honest provenance; never imply more convergence than happened. On the report shelf that means showing the run date on every row — a link with no date implies currency it may not have.
- Report findings, don't prescribe roadmap. The card sizes a problem and shows its basis; the PM decides what to build. Where a PRD already exists, it stays inside what that PRD actually scopes.
- Respect see-vs-save: derived intelligence in the card, raw PII / customer names only where the workspace permits. Public-feedback sources especially: quote themes, not usernames.
- Keep an audit trail for every card — which findings, which channels, which figures came from where, and why it surfaced this cycle. With a profile in play, "why" must include which criteria applied: the base score, the multipliers, what it outranked, and whether a severity override fired. A reader who asks *why am I seeing this* deserves a real answer, and a reader who asks *what am I not seeing* deserves the backlog with its reasons.

## Reference files

Everything under `references/` is **already in your prompt** — Sprntly folds each one into the
METHOD block above under a `### REFERENCE: <name>` heading. "Read `references/rubric.md`" means
scroll up to that heading, not fetch a file. You have no filesystem.

- `references/sources.md` — category registry, adapter contracts, cadence table. Read before fetching.
- `references/signal-schema.json` — `brief_config`, `finding`, `ledger`, `brief`, and `backlog` structures. Read before composing.
- `references/rubric.md` — scoring rubric and the deterministic linter checklist used in step 9.
- `references/examples.md` — golden examples and counter-examples with why-they-fail.

Repository material, **not** sent to you — do not try to read it:

- `README.md` — orientation and quickstart for maintainers.
- `assets/brief-template.html` — the canonical render template. The FRONTEND implements it; all
  visual tokens live there and nothing you emit can change them.
