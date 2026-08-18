# Crucible

**Internal engine name.** Users never see it. They talk to a single agent; Crucible is the capability that agent invokes when someone brings it a goal.

**What it does.** Takes a business goal, reads everything the company already knows, and returns a ranked set of recommendations with sizes, confidence, and what was ruled out.

**What it is not.** Not an agent. Not a loop. Not a chatbot with tools. It is an eleven-stage deterministic pipeline with ten bounded LLM call sites and a human approval gate in the middle. If you find yourself writing a `while` loop that decides its own next action, stop, you are building the wrong thing.

---

## Read these in order

| File | What it is | Read when |
|---|---|---|
| `README.md` | This file. Orientation, failure modes, build order. | First, all of it |
| `CRUCIBLE-SPEC.md` | The build specification. Data model, eleven stages, scoring, stores, tests. | Second, all of it |
| `CRUCIBLE-GOAL-RESOLUTION.md` | Stage 0 in full. Goal definition resolution. | Before writing Stage 0 |
| `CRUCIBLE-EXAMPLE-BOOST.md` | Full worked output on a synthetic Facebook Boost revenue goal. The format reference for Stage 11 rendering. | Before building output assembly |
| `CRUCIBLE-CRITIQUE.md` | Honest critique of the spec by someone with no stake in it. | After the spec, before estimating |
| `CRUCIBLE-ENTERPRISE-READINESS.md` | Corpus-only mode, calibration, extraction robustness. | Before any enterprise pilot |

**If you are an LLM being asked to implement this:** read all of `README.md` and `CRUCIBLE-SPEC.md` before writing any code. Section 1 of the spec (invariants) is not advisory. Implement the invariant assertions first, as executable tests, before any pipeline stage exists.

---

## The one-paragraph version

Companies already have the analysis. Data science reports, research readouts, competitive decks, QBR summaries, retros, experiment results, Jira history, the repo. The finding that matters is almost never inside one of those documents. It is the thing that only appears when two of them are read together, and nobody has time to read them together. Crucible reads all of them, normalises what each one claims, works out which sources are allowed to speak to which questions, resolves the conflicts, sizes what is left in the currency of the goal, and returns a ranked list with what it ruled out and why.

---

## The central claim, and why the invariants exist

**The claim:** the highest-value finding is usually quiet. It appears in one careful analysis nobody circulated, contradicts a louder source, and concerns a small population with high value per head.

Every naive implementation destroys this finding, in the same predictable ways:

- It ranks by how many sources agree, so the quiet finding sinks
- It lets the loudest source (tickets, reviews, sales) set the size, so volume beats value
- It treats "we didn't measure it" as "it's zero", so unmeasured things disappear
- It writes causal language over correlational evidence, so everything reads confident
- It invents a definition for the goal, so the whole run answers the wrong question

**The nine invariants in spec section 1 exist to prevent exactly these.** They are not code quality guidance. They are the product. A version of this system with beautiful stages and violated invariants is worth nothing, because it produces confident, well-formatted, wrong answers, which is worse than producing nothing.

---

## Build order, and the one non-negotiable rule about it

### Pass 1, single-threaded. Do not parallelise this.

1. Data model (spec §3), types only
2. **All nine invariants as executable assertions**
3. Causal lint (I5), with its banned-verb list and its test corpus
3b. Error taxonomy and declarative fallback ladders, alongside the connector manifest. Cheap now, expensive once fifteen connectors have ad hoc failure handling.
4. Golden test harness
5. Stage 0 goal resolution (`CRUCIBLE-GOAL-RESOLUTION.md`)

That is the contract. Everything after it gets built against it.

### Pass 2, parallel is fine here.

6. Claim extraction **and its eval harness, together, before anything downstream**
7. Stages 1 to 3 (intake, grounding, plan mode)
8. Stages 4 to 7 (normalisation, coverage, sweeps, adjudication)
9. Stages 8 to 10 (levers, scoring, output)
10. Persistence, calibration logging, outcome write-back

**Why the order matters.** The invariants are cross-cutting. I1 says impact never reads corroboration. I9 says nothing passes Stage 0 unlocked. If stages are built in parallel before the contract exists, these get violated at the seams, and they get violated invisibly, because every individual stage's own tests will pass. You will discover it in week five, in the form of a scoring function that reads a field it was never allowed to see.

---

## Failure modes

This is the most important section in this file. Each of these has been seen or is structurally likely. Each produces a system that runs clean and is wrong.

---

### F1. Extraction is under-built. **This is the most likely failure by a wide margin.**

**What happens.** The spec describes eleven stages in detail and extraction in about one table row. You correctly infer that the stages are the work. You build them well, stub extraction with a sensible prompt, and it returns plausible claims on the first ten documents. It looks done.

**Why it is fatal.** Extraction turns a document into a claim: what is asserted, how strong the evidence is, and who it applies to. Every number downstream is computed on top of those claims. Your scoring can be perfectly deterministic and every invariant can hold, and if extraction read a hedge as a finding or attached a claim to the wrong population, the output is confidently wrong with clean provenance. Nothing downstream can detect it, because the pipeline has no way to know its input was bad.

**Why it hides.** It degrades gradually. Ten documents fine. A hundred mostly fine. A thousand and it is drifting on document types nobody considered. There is no moment where it visibly breaks.

**What to do.**
- Hand-label 30 documents from a real corpus before building extraction. Ground truth on what each asserts, how strongly, and about whom.
- Measure recall, strength accuracy, and population accuracy separately. An 85 percent recall with 60 percent population accuracy is a broken system that looks fine on one number.
- Gate every prompt change on that set. No exceptions, including "obviously safe" changes.
- Include near-misses in the set: documents that quote a metric without defining it, that hedge, that report someone else's finding secondhand.
- Log extraction outputs in production and sample them weekly. Drift is real.

**Budget:** extraction quality is roughly 40 percent of total effort. Plan for it as such.

---

### F2. The invariants get quietly relaxed under deadline

**What happens.** I1 says impact never reads corroboration. It is genuinely tempting to add "just a small bonus" when four sources agree, because it makes demo output look more sensible. Someone adds it.

**Why it is fatal.** That single change re-buries the quiet finding, which is the entire reason this system exists over a general LLM. The product becomes a slower, more expensive way to surface the obvious.

**What to do.** The flagship test: `scoreImpact()` output must be byte-identical when `surfacedBy` is mutated. It runs in CI. It is not skippable. If someone needs to change an invariant, that is a product decision, not a refactor, and it goes through the product owner.

---

### F3. Building it as an agent

**What happens.** "Agentic" is the ambient default. Someone writes a loop with tool calls and lets the model decide the next step.

**Why it is fatal.** Reproducibility is the differentiator against a general LLM. A loop that chooses its own steps cannot guarantee that impact never reads corroboration, cannot guarantee the same substrate produces the same ranking, and cannot be audited. It also changes the enterprise security conversation from "deterministic pipeline with a human approval gate" to "autonomous agent with access to our data," which is a different and much worse review.

**What to do.** Fixed stage order. Ten LLM call sites, listed in spec §7. Each one returns data only. **No LLM call anywhere in this system returns a score, a rank, a confidence value, or a decision about what to do next.** That is I2 and it is checkable by reading the return types.

---

### F4. The goal definition gets inferred

**What happens.** The metric is not clearly defined, or two systems define it differently, and the code picks the more recently updated one and proceeds.

**Why it is fatal.** A wrong definition does not produce a slightly wrong answer. It produces a fully coherent, well-sized, well-argued answer to a different question. Nothing downstream can catch it, and the customer will not catch it until after they have shipped something.

**What to do.** I9. Adopt an existing definition verbatim and show it in the clarification, where the user can change it. If none exists, ask. Never infer, never paraphrase, never break a tie silently. Hard error entering Stage 1 without `status === 'locked'`. Full detail in `CRUCIBLE-GOAL-RESOLUTION.md`.

**The half of this that gets missed:** identifying the right metric is only half a definition. The other half is how it is calculated. Two teams both say "revenue," both point at the same dashboard, and mean recognised versus booked, gross versus net, with or without the marketplace line. None of that is visible in the metric name and all of it resizes every recommendation. Resolve method alongside identity (goal resolution §6), ask only where the plausible spread exceeds 5 percent of the target delta, cap at three questions, and flag everything assumed under I8.

---

### F5. Unmeasured becomes zero

**What happens.** A metric is missing, a query returns nothing, a field is null. Somewhere, an aggregation treats it as 0 and the arithmetic keeps working.

**Why it is fatal.** Silent. Everything still adds up. A segment worth 3 million dollars scores as worth nothing and never appears in the output, and there is no error to notice.

**What to do.** I3. `null` propagates and is rendered as "not measured", never as 0. Test the aggregation paths specifically with nulls interleaved, not just with a full null set, because the all-null case usually gets handled and the mixed case usually does not.

---

### F6. Ticket and PR text is treated as evidence about users

**What happens.** A Jira ticket says "users churn because export is slow." Extraction reads it as a claim about user behaviour and it enters the substrate looking like a finding.

**Why it is fatal.** That sentence is one engineer's framing typed into a text field. Nobody measured it. Trackers and repos are full of confident causal assertions about users, and they are among the least reliable statements in the corpus while looking like some of the most specific.

**What to do.** Spec §4.5. From execution sources, extract **what was done**, not **why someone said it was done.** Code and tracker are authoritative for `existence`, `attempt`, and `constraint` only, and explicitly barred from `preference` and `magnitude`. Stated rationale is retained as context on the claim and may not vote.

---

### F7. A connector gets special-cased into pipeline code

**What happens.** Zendesk needs slightly different handling. Someone adds `if (sourceId === 'zendesk')` inside a stage. It works.

**Why it is fatal.** Not immediately. It is fatal at customer six, when the pipeline has fourteen special cases and adding a connector requires understanding all of them. Connector extensibility is the difference between a product and a consultancy.

**What to do.** Acceptance criterion 8: adding a connector requires **zero** changes to files under `src/stages/`. Enforce it in CI with a grep for connector ids in the stages directory. Behavioural differences go in the manifest (spec §4.1), not in the pipeline.

---

### F8. Confidence bands are decorative

**What happens.** Bands get assigned by formula, rendered in the output, and never checked against reality. Nobody ever finds out whether "high confidence" items land more often than "medium" ones.

**Why it is fatal.** It is the claim the whole product rests on, and it is the first thing a serious enterprise buyer will probe. If high and medium land at the same rate, every band in every report has been decoration.

**What to do.** Log the prediction, the band, and the stated range on every recommendation from run one. It is cheap now and impossible to retrofit, because you cannot recover predictions you never logged. See `CRUCIBLE-ENTERPRISE-READINESS.md` §2.

---

### F9. Plan mode becomes an approval rubber-stamp

**What happens.** The plan renders as a wall of text with an Approve button. Users click Approve without reading. The team reports high approval rates and concludes it is working.

**Why it is fatal.** Plan mode exists so a human catches a wrong framing before ten minutes of compute goes into answering the wrong question. An unread plan provides zero protection while providing the appearance of it.

**What to do.** **Measure edit rate, not approval rate.** Blocks must be individually addressable and editable, and the plan stays under 600 words. If edit rate is near zero in early testing, plan mode is failing regardless of what approval rate says.

---

### F10. Output over-hedges into uselessness

**What happens.** Every finding is correctly caveated. Confidence is honest. Nothing is overstated. The reader cannot tell what to do on Monday.

**Why it is fatal.** Quiet failure. The system is technically correct and gets ignored, which is indistinguishable from being wrong in terms of business outcome.

**What to do.** TL;DR first, with the top three, their sizes, the total, and what was ruled out. One confidence band per recommendation, never decimals. Name the weaker leg in one sentence rather than hedging every sentence. Assumptions go at the bottom, not woven through. Track adoption alongside outcomes; consistently right and consistently ignored is a real defect and this is the only way to see it.

---

### F11. Cross-customer learning contaminates a customer's definitions

**What happens.** Ninety percent of customers use a 14-day activation window. A customer uses 7. Normalisation logic, or a prior, or a helpful default nudges toward 14.

**Why it is fatal.** Loss of trust is instant and total when a customer notices the engine reporting a number they do not recognise.

**What to do.** Cross-customer priors may inform lever matching and effort comparables. They may **never** touch a definition, a population, or a metric value. Keep them in separate stores and keep the boundary explicit in code.

---

### F12. Degradation happens silently

**What happens.** A connector times out, the fallback ladder substitutes a cached snapshot, the run completes, and the output looks exactly like a complete one.

**Why it is fatal.** A quietly thinner run is indistinguishable from a full one, so nobody discounts it. This is worse than the failure it replaced, because a hard failure at least tells you something is wrong.

**What to do.** Every degradation writes a `CoverageNote` that renders where it affects a finding. Claims obtained through a fallback inherit that step's confidence ceiling automatically. And keep the three error classes separate: a query returning zero rows is a semantic result, not an error, and under I3 it is `null`, never `0`.

---

### F13. Prioritisation quietly becomes the analysis

**What happens.** RICE gets wired in, and because effort sits in the denominator, low-effort items float. Someone notices the ranking looks conservative and tunes the impact model to compensate. Now sizing and ordering are entangled and neither can be trusted.

**Why it is fatal.** Impact is a claim about the world. Priority is a claim about your constraints. Once they contaminate each other, the size of a finding depends on how busy the team is, which is nonsense, and the customer will eventually notice.

**What to do.** I10. Stage 10 reads frozen scores and never writes back. Ship the fixture test that runs the pipeline with prioritisation on and off and asserts the impact and confidence outputs are byte-identical. If effort is underivable, the item is `unrankable` with its reason, never assigned a made-up number to complete the arithmetic.

---

### F14. The considered list becomes a graveyard

**What happens.** Twenty items get one-line dismissals at the bottom of the report. Nobody can tell which were seriously examined and which were pattern-matched away, and asking about one produces a restatement of the same line.

**Why it is fatal.** The considered list is the credibility of the ranking. If it cannot be interrogated, a reader has no way to tell a considered rejection from an oversight, and the top three lose their authority too.

**What to do.** Every ledger entry retains `claimIds`, its screening inputs, and the stage it stopped at, so `deepenCandidate` can resume real analysis on request. Expanding an item never re-ranks the run.

---

### F15. The output arrives as assertion

**What happens.** The pipeline works, the scores are sound, and the report is a list of conclusions with numbers attached. No charts, no quotes, no visible evidence chain. It reads like a consultant's summary.

**Why it is fatal.** A reader has two available responses to an unsupported conclusion: accept it on authority or discount it. Both are bad. Accepting on authority means the customer never learns to trust the mechanism, only the brand, and the first wrong call destroys everything. Discounting means they do the analysis again themselves, which is the outcome the product exists to prevent.

**Why it hides.** Every stage passes its tests. The scores are right. Nothing is broken. The report is simply unpersuasive, and unpersuasive is not a test failure, so nobody catches it until a customer shrugs at a correct answer.

**What to do.** Stage 11b. Every deep recommendation carries at least one rendered evidence artifact, and prose alone fails acceptance. Charts trace to `claimIds`, quotes are verbatim with segment attribution, and the evidence chain is rendered explicitly rather than implied by paragraph order. If a finding cannot produce an artifact, treat that as a signal it is thinner than its score suggests.

---

### F16. Quotes get used to size things

**What happens.** Three advertisers describe the budget field as advice. The report says advertisers read the default as a recommendation, and the reader takes that as a statement about the population.

**Why it is fatal.** It is I4 with better production values. A quote is authoritative for mechanism and never for magnitude, and blurring that turns three interviews into a population claim nobody can defend when it is challenged.

**What to do.** Where a quote and a number appear together, the number carries the size and the quote carries the why, and the sentence must make clear which is doing which. Disclose selection: how many were reviewed, how these were chosen. Never composite quotes from multiple speakers, never tidy the wording.

---

### F17. Doing all of it before proving any of it

**What happens.** Six weeks of connector framework, stores, and stage scaffolding, and the first end-to-end run happens in week five.

**Why it is fatal.** The whole thesis rests on one unproven claim: that reading a corpus surfaces a finding nobody had. That is testable in days on frozen data. If it is false, everything else was wasted.

**What to do.** Build the vertical slice first. One corpus in a folder, no connectors, hardcoded goal definition, extraction, one sweep, ranked JSON out. **Success test: does it surface a real finding without being hinted at?** Answer that before building infrastructure.

---

## What to build first, concretely

**The 48-hour slice.**

- One corpus as a folder of documents. No connectors.
- Goal definition hardcoded. Skip Stage 0's resolution logic entirely.
- Invariant assertions written first, before any stage.
- Extraction → normalisation → one sweep → adjudication → scoring → ranked JSON.
- No persistence, no dispatch, no plan mode, no charts, no narrative.

**Pass condition:** it surfaces a genuine finding from the corpus that was not hinted at in the goal, and the finding is traceable to specific documents.

If it passes, everything else is engineering. If it does not, no amount of connector framework rescues it, and you have learned that in two days instead of six weeks.

---

## Effort

Per I7, an estimate without derivation should be null. There are no comparable prior builds to derive from, so these are judgment, not measurement, and should be treated as such.

| Scope | Conventional | AI-assisted, spec in hand |
|---|---|---|
| 48-hour vertical slice | 2 to 3 days | 1 to 2 days |
| End-to-end on frozen corpora | 6 to 8 weeks | 2 to 3 weeks |
| Enterprise-pilot ready | 3 to 4 months | 6 to 8 weeks |
| Full spec including calibration | 5 to 6 months | Still months. Waiting for outcomes to land is calendar time and does not compress. |

Rough split of total effort: pipeline logic 30 percent, extraction quality 40 percent, connectors and stores 30 percent. Note that the part described in the most detail is the smallest share of the work.

---

## Definition of done, per milestone

**M1.** Stage 0 resolves and locks a definition. Plan mode renders editable blocks. Causal lint blocks a violating sentence. All nine invariant tests green in CI. Connector manifest framework accepts a new connector with no stage changes.

**M2.** Extraction eval harness exists with a labeled set. Stages 4 to 7 produce findings with impact and confidence from a real corpus. Authority matrix rejects an out-of-authority claim in a test. Coverage map distinguishes analysis gaps from data gaps.

**M3.** Full ranked output with both rankings, the rejection ledger, the overlap discount as its own line item, and effort derivation or explicit null. Unshipped-winner check runs. Charts render. Calibration logging writes on every run.

**M4.** Outcomes write back into the lever library and trust profile. Reactivated ledger entries surface at plan time. Calibration curve is visible in-product.
