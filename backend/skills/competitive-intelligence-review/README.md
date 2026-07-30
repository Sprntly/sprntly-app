# competitive-intelligence-review (v3)

A decision-first competitive review **a PM can forward to their VP without editing it.**

That constraint drives the whole design. The reader is two levels up, was not in the research, has fifteen minutes, and will be asked by their own leadership where a number came from.

## How to use

> "Run a competitive review for [company]"
> "Monthly competitive scan — [company] vs [competitors]"
> "What have our competitors shipped this month?"

Invoke once. The skill self-scopes: it decides which stages to run and at what depth, and never asks you to pick modules.

**You do not have to name competitors.** If you don't, the skill derives the set from your position — what job customers hire you for, which budget line pays for it, who else is in the consideration set — and prints its reasoning. It always includes at least one **entrant**: a company that isn't a competitor yet but will be inside twelve months. A set of only incumbents produces a report that confirms what the team already believes.

**Works beyond product companies.** Stages hold; evidence sources adapt — changelogs and app stores for software, case studies and practice launches for services, portfolio adds and published theses for capital, take-rate and category changes for marketplaces.

## Two modes

| Mode | Cadence | Answers |
|---|---|---|
| **Scan** | Monthly | What changed, and what do we do about it |
| **Review** | Quarterly | Where do we stand and where is this going |

Scan is the default once a prior run exists. The reader shouldn't be able to tell which mode ran from the document's framing — the difference shows in what's present, not in language about cadence.

State lives in `state/ci-state.json`. Every field carries an observation date and a source; a field that can't be re-observed keeps its prior value and is marked stale with its age. Without stored state, "what changed" becomes a memory exercise — which is where fabrication enters.

## What the report contains

- **Opening** — the findings that matter, in prose. No metadata banner, no audience label.
- **Three benchmarks, always present** — **scale** (revenue, growth, differentiator, what they take from us), **market position** (a two-axis map with us marked), and **feature** (capability by capability, each row marked table stakes / contested / one-company-only / their gap). The feature benchmark is what tells you which specific capabilities are commodity and which belong to one company.
- **Radar, run twice** — against the scale players and against the specialists. Averaging a small specialist into a six-way chart hides the shape worth seeing. The radar summarises dimensions; it does not replace the benchmarks, and v3 states that explicitly after an earlier draft dropped them.
- **Launch log per competitor** — dated, classified `net-new` / `parity` / `deprecation` / `beta` / `market`, with a pattern line. Three parity launches means they're closing a gap; three net-new means they're opening one. Silence from a fast-moving rival is reported as a finding.
- **Threat scan** — new markets, new technology, structural risks. Each rated **severity** (dents / reshapes / removes us), **timing**, and **our defence** — where the answer is *none*, it says none.
- **Sentiment per competitor** — same axes for everyone including us, from app stores, review sites and forums. Closes with the column that matters: for each complaint about us, which competitor is actively selling against it.
- **Recommendations** — one consolidated ranked set, each naming the evidence behind it, with what to do, why now, how we'd measure it, and what to watch.
- **Sources** — grouped by competitor, each with what it supports and its date.

Review mode adds the strategic layer: arena and Five Forces, the GE-McKinsey 9-box with a verb, product and pricing teardown by job-to-be-done, momentum signals, money and strategy read from filings and transcripts, and organisational signals including hiring read through STAR.

## The voice standard

The output is held to a single test: could the PM who generated it forward it unedited?

- **Claims, not impressions.** "Reddit grew advertising revenue 74% year over year" survives. "Reddit is on a tear" gets cut.
- **Judgment is visibly separated from fact.** Analytical reads are introduced as reads; sourced figures carry a confidence mark.
- **Severity is calibrated in both directions** — no inflating a routine release to create urgency, no softening a real risk to avoid alarming the room.
- **Gaps in our product are described factually, never with blame.** Name the gap, the evidence, and the fix. Never name a team as the cause.
- **No snark about competitors, no cheerleading about us.**
- **Recommendations are proposals with trade-offs.** One with no stated risk reads as advocacy.
- **Nothing about the report's own mechanics appears in the report.**

## Data integrity

The hard guardrail, unchanged from v2 and extended in v3.

Every quantitative claim carries a named source and date. Nothing is invented — not a revenue figure, a price, a rating, or an executive quote. **v3 adds feature claims to that list**: AI-drafted competitive content is documented to invent competitor features that don't exist, so a feature is reported only when observed on the competitor's own surface.

Four tiers: 🅗 hard · 🅢 soft · 🅘 inferred · **🅥 vendor-reported**. The fourth is new and sits on a different axis from the others — it measures *incentive*, not certainty. A competitor's self-reported performance figure is not an independent measurement.

Where sources disagree, the range is reported rather than a figure picked. Where a metric can't be sourced, it's stated as unknown in prose or omitted — never a placeholder, never a guess. And figures about *us* carry a marked source too: run without internal data access, the skill will source our own numbers from trade press, and a report that says "we" while citing a third party about us is a credibility risk the reader can't see.

## Contents

- `SKILL.md` — the authoritative spec
- `examples/01-facebook-ads.html` — full run: Facebook Ads against Google, TikTok, Snapchat, Reddit and OpenAI

## What changed in v3

| Area | v2 | v3 |
|---|---|---|
| Competitor set | Caller supplies, or "find them" | Derived from the company with reasoning printed; entrant bucket mandatory |
| Company types | Written for product companies | Product, services, capital, marketplace |
| Feature launches | Inside "what's heating up" | Own section per competitor, dated and classified |
| Threats | Implicit in the arena stage | First-class scan: severity × timing × defence |
| Sentiment | Market-level themes | Per competitor on shared axes, plus the who-sells-against-it column |
| Visuals | Tables and matrices | Radar on deciding dimensions, run twice |
| Recommendations | Per section, then a list | One consolidated ranked set naming its evidence |
| Cadence | Single deep study | Scan and Review modes with state between runs |
| Voice | Implicit | Explicit VP-shareable standard, enforced in the checklist |
| Integrity | Numbers, prices, quotes | Adds feature claims and a vendor-reported tier |

## Known gaps

Traffic, share of search, app downloads and AI-search visibility come from paid tools; free workarounds are directional and labelled soft. Strategy reads are interpretive — hypotheses to validate, not facts. The skill synthesises; it cannot manufacture data it wasn't given.

## Pipeline position

`business-context` → **`competitive-intelligence-review`** → `positioning` · `prioritize` · `roadmap`, with findings feeding `prd-author` as evidence.
