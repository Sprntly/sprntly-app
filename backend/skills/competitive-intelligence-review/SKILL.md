---
name: competitive-intelligence-review
description: A decision-first competitive intelligence review a PM can hand to their VP without editing it. Runs in two modes — a monthly Scan that reports only what changed, and a quarterly Review that re-derives the whole picture. Reasons out the competitor set when none is supplied, logs every competitor launch with a classification, scans for new markets and technologies that could take the business rather than dent it, benchmarks sentiment per competitor, plots the deciding dimensions on a radar, and ends in a single ranked set of recommendations tied to named evidence. Use when the user says "competitive intelligence", "competitive analysis", "competitive review", "where do we stand vs competitors", "what are our competitors shipping", "monthly competitor report", or wants a recurring read on the landscape. Works for product, services, capital, and marketplace companies. Never fabricates a number, a feature, a price, or a quote.
---

# Competitive Intelligence Review (v3)

## What it does

Produces the competitive review a product team actually needs: what changed, what it means relative to us, what could take the business, and what we should do about it — written so that the PM who generated it can forward it to their VP without rewriting a sentence.

It is not a data dump. A spreadsheet of competitor facts is worthless until synthesis turns it into a decision, and the whole skill is built around that last step.

**One call, self-scoped.** The caller invokes it once; the skill decides internally which stages to run and at what depth. It never asks the caller to pick modules.

---

## Voice — the standard this output is held to

**Assume the reader is two levels up and was not in the research.** They have fifteen minutes, they will act on what they read, and they will be asked by their own leadership where a number came from. Everything below follows from that.

**Write claims, not impressions.** "Reddit grew advertising revenue 74% year over year" is a claim. "Reddit is on a tear" is an impression. Impressions get cut.

**Separate what is known from what is judged, visibly.** Sourced facts carry a confidence mark. Analytical reads are introduced as reads — *the pattern suggests*, *our interpretation is* — never smuggled in beside a filing figure in the same tone.

**Calibrate severity honestly, in both directions.** Do not inflate a competitor's routine release into a threat to create urgency, and do not soften a real structural risk to avoid alarming the room. A reader who finds one overstated claim discounts the whole document — and a report that finds every threat already covered was written to reassure rather than to inform.

**Describe gaps in our product factually and without blame.** "Advertisers report that generated variations altered logos" is reportable. "The creative team shipped this without guardrails" is not — it is an internal accusation the VP now has to manage. Name the gap, name the evidence, name the fix. Never name a team as the cause.

**No snark about competitors and no cheerleading about us.** The reader may know these companies, may have worked at them, may be about to partner with one. Respect that. Competitors are described by what they did.

**Say the difficult thing plainly, once.** If our position is weak on a dimension, state it in a sentence and move to the implication. Do not bury it, and do not dwell on it — repetition reads as editorialising rather than analysis.

**Every acronym is expanded on first use, and internal shorthand is removed.** If a term is only meaningful to the team that generated the report, it does not survive the draft.

**Recommendations are proposals with trade-offs, not demands.** Each names what to do, why now, how we would know it worked, and what to watch. A recommendation with no stated risk reads as advocacy.

**Nothing about the mechanics of the report appears in the report.** No cadence explanation, no statement of who the audience is, no "this is a baseline run", no description of how the skill works. The document opens on the finding.

---

## Two modes

| Mode | Cadence | Question | Stages |
|---|---|---|---|
| **Scan** | Monthly, or on demand | What changed, and what do we do? | 0, 1 (light), A, B, C, **D1**, D2, H |
| **Review** | Quarterly | Where do we stand and where is this going? | All |

**Scan is the default when a prior run exists on file.** Review is the default with no prior run, when the competitor set changes materially, or when the caller asks for the full study.

Whichever mode runs, the reader should not be able to tell from the document's framing which one it was. The difference shows in what is present, not in language about cadence.

### State between runs

Every run reads a state file, diffs against it, and rewrites it. Without stored state, "what changed" is a memory exercise, and memory is where fabrication enters. `references/state-spec.md` holds the full contract — field discipline, staleness, tier travel, mode selection and decision carry-forward. Read it before writing state.

```
state/ci-state.json
{
  "run_id", "previous_run",
  "competitors": { "<name>": { features[], pricing[], sentiment{}, hiring{},
                               exec_commentary[], financials{}, geo{} } },
  "our_state": { ... },
  "decisions": [{ id, raised_in_run, recommendation, owner, status, outcome_note }]
}
```

Every field carries `observed_on` and a source. A field that could not be re-observed keeps its prior value and is marked stale with its age — never silently refreshed, never re-derived from memory. If no state file exists, populate it and omit every diff section rather than inventing a comparison.

---

## Stage 0 — Scope and the competitor set

If the caller names competitors, use them, and add any obvious omission with a one-line reason. If they don't, **derive the set and show the reasoning.** Never ask, and never guess silently.

Derive from the company's own position: what job the customer is hiring them for, which budget line pays for it, and who else is in the consideration set at the moment of purchase. Build from four buckets:

| Bucket | Question | Count |
|---|---|---|
| **Direct** | Same job, same buyer, same budget line | 2–3 |
| **Adjacent** | Different product, same budget | 1–2 |
| **Substitute** | Solves the job without buying the category | 0–1 |
| **Entrant** | Not a competitor yet; will be within 12 months | 1–2 |

**The entrant bucket is mandatory.** A set of only incumbents produces a report that confirms what the team already believes. Name the entrant even when it feels premature — being early on one is worth being wrong on three. If the caller supplied the set and none of their names is an entrant, say so once and add one.

Print the derived set with a sentence each on why it is in, and note who was considered and excluded. Resist the forty-points-by-N-competitors trap: three to five deep beats twelve shallow.

### Company-type adaptation

The stages hold; the evidence changes.

| Type | Where the signal lives |
|---|---|
| Product / software | Changelogs, release notes, docs, app stores, pricing pages |
| Services / agency | Case studies, named client wins and losses, practice launches, partner tiers, senior hires |
| Capital / investment | Portfolio adds, fund closes, published theses, partner moves, LP disclosures |
| Marketplace | Supply and demand-side terms, take-rate changes, category expansion, seller tooling |

Where a stage has no equivalent for the company type, say so in one line rather than forcing it.

---

## Stage 1 — Us first

Establish our position, our segments, where we win and lose, and — critically — **our strategy or goal**, because every later finding is judged "so what *for us*." Without a stated goal the synthesis is directional and the report says so.

---

## Stage A — Launch log (per competitor, mandatory)

For every competitor, a dated list of what shipped in the window. This is the section readers open first. It is never folded into a timeline.

Each entry: **date · what it is, in one sentence · classification · source**.

| Class | Meaning |
|---|---|
| `net-new` | Capability that did not exist in the category |
| `parity` | Closed a gap with us or another rival |
| `deprecation` | Removed or sunset — as informative as a launch, and almost never tracked |
| `beta` | Announced, not generally available |
| `market` | Same product, new geography or segment |

Then one line per competitor on **what the pattern says** — three parity launches means they are closing a gap; three net-new means they are opening one. That sentence is what turns a log into intelligence.

**If a competitor shipped nothing, say so with the window checked.** Silence from a fast-moving rival is a finding.

---

## Stage B — Market and technology threat scan

Answers the question the reader actually has: *is anything happening that takes our business away, and are we defending it?*

**1. New markets.** Has a competitor entered a geography, segment or vertical we are not in? Distinguish announced from live. Track the sequence — the order in which someone expands says which customer bases they think are worth the cost.

**2. New technology.** Is a capability emerging that changes *how the job gets done* rather than how well? The test: does it move the customer's decision to a surface we do not own? Discovery moving upstream, a new interface layer, a protocol that disintermediates. These rarely appear as a competitor's feature launch, which is why a launch log alone misses them.

**3. Structural threats.** Anything that could remove the business rather than dent it: platform-level regulation, a protocol that turns us into an API, a channel shift that bypasses our surface, an identity or data change that breaks targeting.

Each threat carries three labels, all stated:

| Axis | Values |
|---|---|
| **Severity** | Dents us · Reshapes us · Removes us |
| **Timing** | Now · This year · Watch |
| **Our defence** | Named · In flight · **None** |

**"None" is written when it is true.** It is the most useful word in this stage.

---

## Stage C — Sentiment, per competitor

Same axes for every competitor, including us. Pull from app stores, review platforms and public forums.

Report where sourced: rating, review volume, direction versus prior run, and themes with representative **verbatim** quotes. Quotes are verbatim or they are paraphrased findings — never invented, never assembled from remembered substance. Where a competitor has no accessible corpus, say so rather than filling the row.

Close with the column that makes this section worth reading: **for each complaint theme about us, which competitor is actively selling against it.** A theme that maps to a rival's marketing line is a roadmap item. One that maps to nobody is avoidable loss. Both are useful; they lead to different decisions.

---

## Stage D1 — Benchmarks (mandatory, all modes)

Three benchmarks, always present, always in this order. They answer *where does everyone sit*, which is a different question from *what changed*.

**Scale benchmark.** Every competitor's most recent reported revenue and growth, their differentiator in one phrase, and what they take from us. Where a competitor is private or does not disclose, say so; where published estimates diverge, report the range rather than picking a figure.

**Market position benchmark.** A two-axis map — typically reach against differentiation of the buying experience, though the axes are chosen for the category. Every competitor placed, us included and visibly marked. Placement is judgment and is labelled as such. Follow it with the read: which square we occupy, who sits beside us, and whether our position rests on something copyable.

**Feature benchmark.** Capability by capability, every competitor as a column, with a status on each row: `table stakes` (everyone has it), `contested` (some do), `X only` (one company holds it), or `their gap` (one company is behind). This is the section that tells a reader which specific features are commodity and which belong to one company — and rows marked *X only* should reappear in the recommendations or be explicitly dismissed.

Close with the count: how many capabilities are table stakes, how many were differentiators a year ago, and which rows we hold alone.

## Stage D2 — Radar on the deciding dimensions

Choose six to eight dimensions that decide the category — not a feature list. Score every competitor 0–5. State plainly that scoring is judgment while the facts underneath are sourced.

**Run it twice: once against the large rivals, once against the specialists.** Small competitors are usually sharpest on one or two dimensions, and averaging them into a six-way chart hides exactly the shape worth seeing.

Follow each chart with the read: where shapes overlap is commodity; where a rival extends past us is their sales pitch; where we extend past everyone is what we should be selling.

---

## Stages E–G — The strategic picture (Review mode)

Retained in full from v2, run when the mode calls for them:

| Stage | Question |
|---|---|
| **The arena** | Direct rivals, substitutes, adjacent and future entrants (Porter's Five Forces) |
| **Position and share** | GE-McKinsey 9-box with a verb — invest / maintain / harvest / divest, per competitor and segment |
| **Product and pricing** | Teardown by job-to-be-done; pricing and packaging tracked as dated history, not a snapshot. **"No change" is reported as a finding**, with the window checked — a silent section reads as an unchecked one |
| **Momentum** | Ship cadence always; traffic, app data and AI-search visibility where a source exists |
| **Money and strategy** | For public companies: segment growth *and decline*, guidance versus actual, capex and R&D allocation, risk-factor changes, M&A. Read the transcript as text — what management leads with, what they avoid in Q&A, tone shift versus prior quarter |
| **Organisational signals** | Hiring read through **STAR**: **S**cale (volume indicates investment level), **T**iming (clustering reveals urgency), **A**lignment (postings that do not match announced strategy reveal unannounced plans — the highest-value signal here), **R**ecurrence (net-new versus backfill). Plus notable hires and departures by specialty, and executive commentary with venue, date and source |

---

## Stage H — Consolidated recommendations

One section, at the end, tying every stage together. Not per-section recommendations collected — a single ranked set where each item names the findings that produced it.

Each recommendation carries:

- **From** — the stages and specific findings behind it
- **Do** — concrete enough to brief a PM or open a PRD
- **Why now** — what changed that makes this the moment
- **Measure** — how we would know it worked
- **Watch** — the risk or trade-off

Ranked by leverage, not effort. Three to five. A finding that produced no recommendation needs no padding — but **a threat rated *removes us* with defence *none* must produce one.**

Carry prior recommendations forward with status (`open`, `in progress`, `done`, `dropped`) and what happened. A dropped item records why. This is what turns a report into a program.

---

## Data integrity — the hard guardrail

Competitive analysis is exactly where invented-but-plausible numbers slip in. **The skill never fabricates. Not once, not "for illustration", not to fill a cell.**

- **Every quantitative claim needs a real, named source and date.** Traffic, share, downloads, ratings, revenue, growth, margins, headcount, pricing.
- **No invented specifics** — not a revenue figure, a traffic number, a rating, a price, a tier, a feature, or an executive quote. **Feature claims carry the same risk as numbers**: AI-drafted competitive content is documented to invent competitor features that do not exist. A feature is reported only when observed on the competitor's own surface — product page, changelog, docs, release notes, or a dated announcement.
- **Estimates are allowed only when grounded and labelled**, tagged soft with their basis. A precise-looking figure with no basis is fabrication even if it feels right.
- **Where sources disagree, report the range and say so.** A single figure quoted internally from a wide spread will be wrong.
- **Unknowns are handled cleanly** — stated as unknown in prose where the absence is informative, or omitted where a blank adds nothing. Never placeholder clutter, never a guess. Items worth pulling later go in the sources block, in prose.
- **Tier discipline.** 🅗 hard (observed, sourced) · 🅢 soft (grounded estimate) · 🅘 inferred (analytical judgment) · 🅥 vendor-reported (the company's own claim about itself). Tiers are never blended silently, and an inference is never promoted to a fact. **Vendor-reported is a separate axis from confidence** — it measures incentive, not certainty, and a competitor's self-reported performance figure is not an independent measurement.
- **Our own figures need a marked source too.** Run first-person without internal data access, the skill will source *our* numbers from trade press. That is acceptable and must be marked — a report that says "we" while citing a third party about us is a credibility risk the reader cannot see.
- **If search or data tools are unavailable**, say so plainly and limit the run to what can be sourced. Never populate metrics from model memory.
- **Final self-audit, required before delivery.** Scan every number, quote and named fact; confirm each binds to a cited source or is stated as unknown. Remove or rephrase anything untraceable.

---

## Output

A single self-contained HTML document. Order:

1. **Opening** — the two or three findings that matter, in prose. No metadata banner, no audience label, no cadence note.
2. **Radar** — where we win and lose, twice.
3. **Scale benchmark** — revenue, growth, differentiator, what they take from us.
4. **Market position benchmark** — the two-axis map, us marked.
5. **Feature benchmark** — capability by capability, with table-stakes status.
6. **Launch log** — per competitor, classified, with the pattern read.
7. **Threat scan** — severity, timing, defence.
8. **Sentiment** — theirs and ours, with the who-sells-against-it column.
9. *(Review mode)* Arena · 9-box · product and pricing · momentum · money · organisational signals.
10. **Recommendations** — consolidated and ranked.
11. **Sources** — grouped by competitor in a designed block, each with what it supports and its date.
12. **Thin meta line** — window, derived set, confidence key, and the note on our own figures.

**Sections are additive, never substitutive.** A benchmark, matrix or map specified here is not dropped because a newer visual covers similar ground. The radar summarises eight aggregate dimensions; it does **not** replace the feature benchmark, which works at the level of individual capabilities, or the market position map, which places competitors rather than scoring them. If a section feels redundant with another, both stay and the prose around them is tightened instead. Removing an established section is a spec change, not an editorial one.

**Presentation:** no format runs more than one screen. Rotate prose, table, radar, timeline, stat cards, quote, card. Confidence marks sit inline as small chips, never as a caveat paragraph. A reader who scrolls past four consecutive tables has stopped reading.

---

## When NOT to use

Market attractiveness or structure alone → `market-structure` (this skill uses it). Our own positioning statement → `positioning` (this feeds it). A single decision between options → `decision-by-traffic-lights`. A sales-ready card against one named rival → `sales-battlecard`. For a fast read, run this skill in Scan mode rather than reaching for another skill.

## Follow-ups after delivery

A question that filters or interrogates a review already delivered ("what did Google ship", "which threats have no defence", "did their pricing change", "status of last quarter's recommendations") is answered from the stored run, not by re-running the study. `references/query-guide.md` governs those answers. A report-shaped ask is never a follow-up — it means a fresh run.

---

## Quality checklist

- [ ] Output is VP-shareable as written — claims not impressions, judgment labelled as judgment, no internal blame, no snark, severity calibrated in both directions, no report mechanics on the page.
- [ ] Competitor set reasoned and printed, including a named entrant.
- [ ] Launch log present for every competitor, dated, classified, with a pattern line — and silence reported where a competitor shipped nothing.
- [ ] Threat scan rates severity, timing and defence; "None" written where true; a *removes us / none* threat produced a recommendation.
- [ ] Sentiment covers competitors and us on the same axes, with the who-sells-against-it column.
- [ ] All three benchmarks present — scale, market position, feature — with the feature benchmark carrying a table-stakes status per row.
- [ ] Radar run twice — scale players and specialists — with the read stated. The radar did not replace a benchmark.
- [ ] Recommendations consolidated into one ranked set, each naming its evidence, with measure and watch.
- [ ] Prior recommendations carried forward with status.
- [ ] No fabricated number, price, feature, or quote; every figure sourced or stated unknown; ranges reported where sources disagree; our own figures marked.
- [ ] Sources presented by competitor with dates; open pulls named in prose.

---

## Known gaps

- The richest signals — traffic, share of search, app downloads, AI-search visibility — come from paid tools. Free workarounds are directional and are labelled soft, never presented as precise.
- Strategy reads are interpretive by nature. They are hypotheses to validate, not facts.
- The skill structures and synthesises; it cannot manufacture data it was not given. Missing metrics become open pulls, not guesses.
- A Review-mode run is heavy. For speed, use Scan.
