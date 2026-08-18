# Critique of the Crucible Spec

Written as if reviewing someone else's design with no stake in it.

---

## Strengths

**The impact/confidence separation with a hard invariant behind it.** Most analytical products collapse these into one score and quietly rank on corroboration, which systematically buries small high-value findings. Making it invariant I1, with an executable test asserting byte-identical output when `surfacedBy` is mutated, is the difference between a principle and a comment. This is the strongest idea in the design.

**The authority matrix, and specifically the self-selection rule.** Forbidding self-selected sources from voting on magnitude is a one-line rule that prevents the most common failure in evidence synthesis: letting review volume determine what the biggest problem is. It generalises correctly and it is enforceable at connector onboarding rather than at read time.

**Negative findings as first-class output.** Ruling out the pricing change at Tessellate was worth more than most of the positive findings. Almost nothing else in the category produces these, because nothing rewards testing the salient hypothesis rather than repeating it.

**Effort derivation or nothing.** Returning `null` with a reason rather than guessing is unusual discipline and it is exactly what survives contact with an engineering lead.

**Connector extensibility as an acceptance criterion.** Criterion 8, enforced in CI by a dependency check, is what will keep this maintainable at forty connectors. Most systems assert extensibility in prose and violate it in month three.

**The rejection ledger with reactivation triggers.** Genuinely differentiated. Nothing else surfaces "six things you rejected are now viable because the pricing freeze lifted."

---

## Weaknesses

### 1. Claim extraction is a single point of failure and the spec underweights it

Everything is downstream of one LLM call. If it assigns the wrong `strength`, the wrong `population`, or the wrong `observedAt`, the deterministic scoring produces precise nonsense with an audit trail, which is worse than obvious nonsense because it is believable.

The spec acknowledges this and puts an eval harness in Milestone 2. **That is too late and too small.** 200 hand-labelled documents will not cover the variance across artifact types, and there is no mechanism for detecting drift once live.

### 2. The determinism claim is weaker than the spec implies

"The LLM proposes, deterministic code decides" is true of the scoring layer and misleading about the system. Three LLM calls sit upstream of every score: extraction, tree generation, and clustering. Rerun the pipeline and you may get a different tree, different clusters, and therefore different findings, all scored deterministically.

The golden-file test in section 8 will catch gross drift and will pass while the underlying findings shift. **The honest claim is that scoring is deterministic given a substrate, not that the system is deterministic.** Marketing the stronger claim will eventually embarrass someone in a customer meeting.

### 3. Goal currency assumes linear value per unit

`impact = population × gap × value_per_unit` treats the tenth percent of adoption as worth the same as the first. It is not. Later adopters are systematically lower-value because the highest-need users convert first. Every adoption-driven number in all three worked examples is overstated by an unknown amount, probably 20 to 40%.

The data to fit a concavity curve exists in every one of these companies. The spec does not use it.

### 4. The authority matrix will rot

It is hand-maintained, and it encodes judgements that are true today. When a company's support volume grows tenfold, tickets stop being uninformative. When a survey tool starts sampling properly, it becomes magnitude-authoritative. Nothing in the design notices.

The trust profile learns from outcomes; **the authority matrix does not learn from anything.** That asymmetry is unjustified.

### 5. Archetype coverage is a cliff, not a slope

Inside the library, the system is strong. Outside it, there is no graceful degradation — no archetype means no tree, no substrate, no sweeps. A PM asking "should we move upmarket" or "is our category consolidating" gets nothing.

That is a large share of real strategic questions, and the failure is silent rather than explicit.

### 6. Overlap discount is hand-waved

Section 9 says compute pairwise population and window overlap and deduct. That is a sentence, not a method. In the Nomi example the discount was 0.85 points, the single weakest number in a report whose conclusion was "you reach 34.3% against a 34% target." **The discount decides whether the goal is met**, and it was estimated.

### 7. No adoption tracking

Outcome capture records whether shipped things worked. Nothing records whether recommendations were shipped at all. A system whose recommendations are consistently ignored is failing in a way this design cannot see, and "our confidence bands are well calibrated on the 12% of recommendations that got built" is not a useful statement.

### 8. Confidence is asserted, never measured

The system emits high, medium, low. Nothing checks whether "high" items are right more often than "medium" ones. If they are not, every number in every report is decoration.

### 9. Business model detection is unbounded

`MODEL_TRUST_DEFAULTS` has seven entries. Real companies are hybrids: sales-led upmarket and product-led downmarket, marketplace on one side and direct on the other. The spec offers no blending rule and no confidence on the classification itself.

### 10. Cost is unmodelled

No budget per run, no cost ceiling, no guidance on what to skip when a company has forty connectors and 10,000 documents. The corpus sweep as specified reads everything scoped to the top 50% of the metric, which at enterprise scale is a large and unbounded number.

---

## The six changes that would make this significantly better

Ordered by impact per unit of work.

### 1. Measure confidence calibration, and put it in the product

Log every prediction with its band. When outcomes land, compute whether high-confidence items delivered within their stated range more often than medium ones. Publish the curve internally, then to customers.

**Why this is first:** it converts an assertion into a measurement, it is the only way to discover if the scoring is wrong, and a system that can say "our high-confidence calls have landed within range 82% of the time across 340 recommendations" has something no competitor can claim without having built the same loop. It also creates the feedback signal that fixes weakness 8 and validates or kills the scoring weights in section 9.

Cheap to build. Expensive to retrofit, because it needs prediction logging from day one.

### 2. Track adoption, not just outcomes

Record for every recommendation: shipped, modified then shipped, deferred, or rejected, plus the reason. This gives you three things at once: a fix for weakness 7, the strongest possible product signal about what makes a recommendation actionable, and a much better answer to "is this working" than retention.

The pattern to watch is recommendations that are consistently right and consistently ignored. That is a communication defect, not an analysis defect, and today nothing would surface it.

### 3. Build the extraction eval harness in Milestone 1, and add live drift detection

Move it earlier and expand it. Beyond the 200-document fixture, sample live extractions weekly and hand-label a small batch to detect drift. Gate every prompt change on recall and strength accuracy.

**This is insurance on the entire system.** Everything else is downstream of it, and it is the component most likely to degrade invisibly.

### 4. Fit the value curve instead of assuming linearity

Replace `value_per_unit` with a fitted function per company, estimated from their own adoption-to-value relationship. The data exists in every case examined.

This corrects a systematic overstatement across every recommendation the system produces, which matters most at exactly the moment credibility is established: the first quarter after a plan ships and the numbers come in lower than promised.

### 5. Make the authority matrix learn

Two mechanisms. First, recompute `populationScope` and `selectionBias` periodically from actual data rather than from onboarding classification, so a source that becomes representative is noticed. Second, feed outcome data back: when a source's claims consistently predict correctly outside its declared authority, flag it for review rather than silently continuing to discount it.

Keep the human confirmation step. The point is detecting rot, not automating judgement.

### 6. Add explicit degradation outside the archetype library

When a goal does not map to an archetype, say so and offer a reduced mode: corpus sweep only, no substrate, findings without sizing, clearly labelled as such. **A partial answer with a stated limitation is far better than a bad answer or none**, and it converts weakness 5 from a cliff into a slope.

---

## Two things to cut

**The `politicalWeight` field.** It is asked at intake, it is the kind of question a PM finds slightly uncomfortable to answer honestly, and nothing downstream reads it except a framing sentence. Either wire it into scoring properly (board commitments should genuinely suppress low-confidence items) or drop it. Asking for something you do not use costs trust.

**Corroboration bonus capped at 0.15.** It is small enough to be noise relative to the other confidence components and it reintroduces exactly the pressure that I1 exists to eliminate. Either make it meaningful or remove it. The current value is a compromise that buys nothing.

---

## The one thing I would not change

**Two sweeps rather than three, with the corpus sweep explicitly covering segments excluded by the sample-adequacy floor.** That is the correct decomposition and the justification is exactly right: high-value segments are usually small, small segments fail the adequacy test, so the structural sweep is blind precisely where concentration is highest. Most teams would have kept three because three sounds more thorough.
