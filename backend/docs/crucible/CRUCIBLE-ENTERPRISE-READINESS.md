# Three Changes for Enterprise Readiness, and Open-Ended Goals


**Superseded on goals:** Part 2 of this document proposed inferring a goal definition when none existed. That approach is withdrawn. See `CRUCIBLE-GOAL-RESOLUTION.md` and invariant I9, which is now authoritative: definitions are adopted or elicited, never inferred.
---

# Part 1. The three changes

Each preserves the differentiated core: impact and confidence kept separate, the authority matrix, negative findings, the rejection ledger, and corpus-first reading. None of them require softening any of that.

---

## 1. Ship a corpus-only mode as a first-class product, not a fallback

**The problem it solves.** The Samsung pilot is stalled on data sharing. Asking an enterprise for telemetry, transaction logs, and user records is the maximum-sensitivity request and security exists to refuse it.

**The change.** Crucible already reads finished analyses as its primary input under v6. Make that a supported operating mode with its own contract: no raw data access, no direct queries, no dispatch. It reads only artifacts that have already been through internal review — DS reports, research readouts, competitive decks, QBR summaries, retros, post-mortems.

**Why this is not a compromise.** Finished analyses are already circulated internally, already cleared, and already classified lower than the underlying data. More importantly they are the input that produces the differentiated finding, because the whole claim is that nobody read them together. A corpus-only run loses sizing precision and loses almost nothing of the thing that makes the output surprising.

**What changes in the build:**

- The coverage map reports analysis gaps it cannot fill rather than dispatching
- Sizing falls back to whatever the source analyses stated, with wider ranges and explicit labelling
- Confidence caps at medium for anything not independently confirmable
- The output says plainly which findings would sharpen with data access, which becomes the natural expansion path

**The commercial effect.** The ask changes from "give us access to your user data" to "let us read the reports your teams already wrote." Different approval path, different reviewer, materially smaller surface. This is the fastest route to a converted design partner, which is also the gap between where the deck sits and where it needs to be.

---

## 2. Log calibration from the first run and show it

**The problem it solves.** An enterprise will ask how accurate this is. For the first two quarters the truthful answer is that you do not know, and silence is worse than a partial answer.

**The change.** Log every recommendation with its confidence band, its stated range, and its predicted effect. When outcomes land, record whether the result fell inside the range. Surface the running curve in the product.

```
Calibration to date, this account
  High confidence     14 recommendations   11 landed in range   79%
  Medium confidence    9 recommendations    5 landed in range   56%
  Low confidence       4 recommendations    1 landed in range   25%
```

**Why it belongs now.** It is cheap to build and expensive to retrofit, because it needs prediction logging from the first run. And it converts your weakest position into a credible one: "we have twelve recommendations tracked and here is how they landed" is a stronger thing to say to a Samsung reviewer than a claim of accuracy with nothing behind it.

It also fixes the real defect. If high-confidence items are not landing more often than medium ones, every band in every report is decoration, and there is currently no way to discover that.

**Add adoption tracking in the same loop.** Record shipped, modified then shipped, deferred, or rejected, with a reason. The pattern worth watching is recommendations that are consistently right and consistently ignored, which is a communication problem rather than an analysis problem and nothing today would surface it.

---

## 3. Make extraction robust to enterprise corpora, and prove it

**The problem it solves.** Every finding is downstream of one LLM call. At Samsung that call has to handle Korean and mixed Korean-English documents, twenty years of accumulated artifact types, and internal terminology that means something specific to one division.

**The change, in three parts:**

**Cross-lingual extraction with per-language eval.** The eval harness cannot be English-only. Measure recall, strength accuracy, and population accuracy per language, and gate prompt changes on the worst-performing one rather than the average.

**Artifact-type-aware extraction.** A rigorous DS report and a slide from a QBR deck are both analyses and are not equally trustworthy. The manifest already carries `artifactType`; use it to set a strength ceiling. A claim extracted from a deck bullet should never reach `measured` without a linked source.

**Terminology capture as a first-class store.** The `teach` move already exists in plan mode. At an enterprise it needs to run at scale: divisions use the same word for different things, and getting that wrong produces confident, wrong reconciliation. Seed it from a glossary if one exists, and capture corrections aggressively in the first weeks.

**Why this specifically.** Extraction is the single point of failure and the one component that degrades invisibly. Everything else fails loudly.

---

**A fourth, if the pilot expands beyond one team:** multi-team goal conflict. At Samsung two teams routinely optimise against each other, and nothing in the design notices when a recommendation for one team damages another's metric. Not needed for a single-team pilot. Needed before a second team joins.

---

# Part 2. Goals should be open-ended

The current design maps a goal to one of a few archetypes, and the archetype supplies the driver tree. That works for revenue, retention, and activation, and falls off a cliff for everything else. Reduce complaints by 8%. Acquire 40,000 new users. Improve app rating to 4.5. Cut time-to-first-value in half. Increase trust scores. Reduce moderation escalations. Any of these is a legitimate PM goal and most have no archetype.

**The fix is to stop treating the archetype as the source of the tree.** Derive the tree from the metric itself, and use archetypes as accelerators when one happens to fit.

## The four-step ladder

Run these in order and stop at the first that succeeds.

**Step 1. Is the metric in their systems?**
Find it, ground it, pull baseline, variance, trajectory, and seasonality. This works for anything instrumented regardless of whether an archetype exists.

**Step 2. Has anyone in the company already decomposed it?**
This is the step that makes open-ended goals work, and it falls out of the corpus-first architecture for free. **Their existing analyses already contain their decomposition.** If the DS team cuts complaints by reason category and then by product line, that is the tree. If a research readout breaks trust into five drivers, those are the branches.

Reading the decomposition out of their own corpus is better than imposing one, because it uses their vocabulary, matches how the org is structured, and will not be argued with.

**Step 3. Can it be decomposed structurally from its own definition?**
Metrics have shapes and shapes decompose mechanically:

| Shape | Decomposition |
|---|---|
| A rate | Numerator drivers × denominator drivers, then segment both |
| A count | Segment dimensions, then per-segment rate × population |
| A sum | Additive components, then each component's drivers |
| A ratio of two metrics | Decompose each side, then the interaction |
| A score or index | Its published components, if it has any |
| A duration | Sequential stages, then per-stage latency |

This covers most metrics that survive steps 1 and 2 without any archetype at all.

**Step 4. Ask.**
Only when the first three fail. And ask concretely, with a proposed reading rather than an open question.

## What asking looks like

Never "what do you mean by user delight." Always a proposal plus the alternatives found in their systems:

```
I can't find "user delight" as a metric in your systems, so I want to check
what I'm optimising before I start.

The closest things I can see:
  · App store rating, currently 4.1, updated daily
  · CSAT from the post-support survey, 78%, n is small
  · A "delight score" in a 2024 research deck, defined as a weighted blend
    of NPS and task success. Never instrumented.

I'd default to app rating, because it's the only one measured continuously
and it's what your last two QBRs referenced. That changes the analysis
though: rating is driven by review-writing behaviour as much as by product
quality, so a meaningful share of the answer will be about who gets asked
to review and when.

Tell me which one, or describe what you'd want to see move and I'll find
the closest measurable thing.
```

Three properties that make this work: it demonstrates a search rather than admitting ignorance, it proposes a default so the PM can approve rather than compose, and it names the consequence of the choice so they can see why it matters.

## Goals that are not metrics

Some are not. "Make onboarding better." "Fix our reputation with developers." "Get ahead of competitor X."

Handle these by proposing a measurable proxy plus a stated limitation, not by refusing:

```
"Make onboarding better" isn't measurable as written, so I'll read it as
time-to-first-value plus week-one retention, which are the two things your
own onboarding retro used. If you meant something else, say so.
```

## Reduced mode when the tree is thin

When steps 1 through 3 produce a weak decomposition and the PM does not want to spend time refining it, run anyway in a reduced mode: corpus sweep only, findings without full sizing, clearly labelled.

**A partial answer with a stated limitation beats no answer or a confident wrong one.** It also converts the archetype cliff into a slope, which is the actual complaint about the current design.

## What this changes in the spec

| Change | Effect |
|---|---|
| Archetypes become optional priors, not gates | Any goal runs |
| Corpus decomposition inserted as step 2 of tree building | Uses their vocabulary and org shape |
| Structural decomposition by metric shape added as step 3 | Covers most uncovered metrics |
| Clarification only after steps 1 to 3 fail, always with a proposed default | Rarely triggers, and is useful when it does |
| Reduced mode when decomposition is weak | No cliff |
| Non-metric goals get a proposed proxy plus a stated limitation | No refusals |
