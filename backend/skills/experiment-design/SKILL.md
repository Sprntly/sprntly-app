---
name: experiment-design
description: Design a rigorous A/B test or experiment. Use when the user says "design an A/B test", "experiment design", "how do I test this", "sample size for", or "set up an experiment". Produces hypothesis, primary metric, MDE and sample-size estimate, guardrails, and analysis plan defined before launch.
---

# Experiment Design

## What it does
Designs an experiment that can actually settle the question: a falsifiable hypothesis, one primary metric, a minimum detectable effect with a rough required sample size and duration, guardrail metrics, and a pre-registered analysis plan — so the team doesn't p-hack or stop early.

## When to use / when NOT to use
- **Use** to plan an A/B test or controlled experiment.
- **Do NOT use** to interpret results after the fact (`experiment-readout`) or for lightweight assumption tests (`assumption-risk-map`).

## Inputs
- **Required:** the change to test and the metric it should move.
- **Optional:** baseline rate, traffic/sample available, expected effect size. *If missing, ask for the baseline and traffic; without them, give the design and the formula and label sample size as "needs baseline."*

## Method (methodology)
Hypothesis-driven experimentation + power analysis + pre-registration.
1. **Hypothesis:** "We believe [change] will [effect] on [primary metric] because [reasoning]."
2. **Primary metric** — one. Plus guardrails that must not regress.
3. **MDE** — the smallest effect worth detecting; set before running.
4. **Sample size & duration** — estimate via `scripts/sample_size.py` from baseline, MDE, power (0.8), alpha (0.05).
5. **Unit & assignment** — randomization unit, splits, contamination risks.
6. **Pre-register analysis** — the test, the stopping rule (run to sample, no early peeking), success threshold.
7. **Risks** — novelty effects, seasonality, interaction with other tests.

## Output spec
Hypothesis · primary + guardrail metrics · MDE · sample size + duration estimate (with assumptions) · assignment design · pre-registered analysis & stopping rule · risks. Math via `scripts/sample_size.py`.

## Sprntly integration (optional)
- **Inputs from Sprntly:** baseline rates from the outcome graph; the assumption/opportunity under test.
- **Outputs to Sprntly:** the experiment registered with its pre-specified metric + threshold so the readout is automatic; trust-ladder gate (don't graduate until it passes).
- **Degrades to:** standalone; ask for baseline + traffic.

## Quality checklist (the bar)
- [ ] One primary metric, with guardrails.
- [ ] MDE is set before running.
- [ ] Sample size/duration is computed, with assumptions stated.
- [ ] Analysis + stopping rule are pre-registered (no peeking).

## Known gaps / limitations
- Sample-size math assumes a simple two-arm proportion test; complex designs (multi-arm, sequential, ratio metrics) need a statistician — the skill flags this.
- It can't fix low traffic: if the test would take 9 months, it says so and suggests an alternative (e.g., bigger MDE, qualitative test).

## Worked example
**Input:** "Test new checkout. Baseline conversion 5%, want to detect +0.5pp."
**Output (abridged):** Hypothesis: streamlined checkout lifts conversion via fewer steps. Primary: checkout conversion; guardrail: refund rate, AOV. MDE: +0.5pp (5%->5.5%). Sample: ~31k per arm (computed). At 10k visitors/day/arm -> ~3 days, but run >=1 week for day-of-week effects. Pre-register: two-proportion z-test, run to sample, ship if +0.5pp at p<0.05 and guardrails flat.
