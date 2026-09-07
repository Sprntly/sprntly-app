---
name: experiment-readout
description: Interpret experiment results and make a ship/kill/iterate call. Use when the user says "read out this experiment", "interpret these A/B results", "is this significant", "should we ship this test", or pastes experiment data. Produces an honest interpretation with statistical caveats and a decision, resisting the temptation to over-claim.
---

# Experiment Readout

## What it does
Interprets the results of an experiment and recommends ship / kill / iterate, with the statistical caveats stated plainly: significance vs practical effect, guardrail movement, segment effects, and the traps (peeking, novelty, multiple comparisons). It protects against both false wins and discarding real signal.

## When to use / when NOT to use
- **Use** after an experiment has run to decide what to do.
- **Do NOT use** to design the test (`experiment-design`).

## Inputs
- **Required:** the results (rates/counts per arm, ideally the pre-registered hypothesis + metric).
- **Optional:** sample sizes, run duration, guardrail readings, segment splits. *If missing, interpret what's provided and flag what's needed for confidence.*

## Method (methodology)
Significance + practical-significance + guardrail + honesty about traps.
1. **Recall the pre-registered question** and primary metric (if absent, note that post-hoc framing weakens conclusions).
2. **Statistical significance** — is the effect distinguishable from noise given the sample? (compute/estimate; flag if underpowered).
3. **Practical significance** — is the effect big enough to matter vs the cost/complexity?
4. **Guardrails** — did anything that must-not-regress move?
5. **Segments** — does the effect hide heterogeneity (helped one group, hurt another)?
6. **Trap check** — early peeking, novelty effect, multiple metrics tested.
7. **Decision:** ship / kill / iterate (and what to change if iterate), with confidence level.

## Output spec
Recap of the question · primary-metric result with significance + practical read · guardrail status · segment notes · trap flags · decision + confidence + next step.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the registered experiment + its pre-specified metric/threshold; live result data from the outcome graph.
- **Outputs to Sprntly:** the result written to the outcome graph (this is the closed loop - did the bet move the metric?); the decision recorded.
- **Degrades to:** standalone from pasted results.

## Quality checklist (the bar)
- [ ] Significance AND practical significance are both addressed.
- [ ] Guardrails are checked, not ignored.
- [ ] Segment heterogeneity is considered.
- [ ] The decision states a confidence level and resists over-claiming.

## Known gaps / limitations
- Post-hoc analysis of a test that wasn't pre-registered is weaker; the skill says so rather than laundering it into certainty.
- Can't recover an underpowered test - it will recommend more data over a false call.

## Worked example
**Input:** "Variant 5.4% vs control 5.0%, ~31k/arm, guardrails flat."
**Output (abridged):** Effect +0.4pp; with ~31k/arm this is around the detectable edge - likely significant (p~0.05) but practically modest. Guardrails flat (good). No segment data - request it before full rollout. Decision: ship to 100% with monitoring, OR hold for segment check if the change is costly to reverse. Confidence: medium. Don't over-claim an 8% relative lift as a blockbuster.
