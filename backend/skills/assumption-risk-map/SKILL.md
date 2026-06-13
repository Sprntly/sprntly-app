---
name: assumption-risk-map
description: Surface the riskiest assumptions behind an idea and design the cheapest tests, including a testable hypothesis. Use when the user says "what are we assuming", "riskiest assumption", "how do we de-risk this", "write a hypothesis", or "assumption mapping". Produces a mapped assumption grid plus testable hypotheses with success criteria.
---

# Assumption & Risk Map (with Hypothesis)

## What it does
Extracts the assumptions a plan depends on, plots them by importance × uncertainty to find the riskiest, and turns each into a falsifiable hypothesis with a cheap test and a clear success threshold. Stops teams from building on unexamined beliefs.

## When to use / when NOT to use
- **Use** before committing to build, or to plan validation for a bet.
- **Do NOT use** to make the final go/no-go (`decision-memo`) or run a formal A/B test design (`experiment-design`).

## Inputs
- **Required:** the idea/plan/bet.
- **Optional:** what's already known/validated, constraints, time to test. *If missing, derive assumptions from the idea and label confidence.*

## Method (methodology)
Assumptions mapping (desirability/viability/feasibility) + riskiest-assumption-test (RAT) + hypothesis framing.
1. **Enumerate assumptions** across desirability (will they want it), viability (does the business work), feasibility (can we build it), and usability.
2. **Plot** each on importance (does the idea die if it's false?) × uncertainty (how sure are we?).
3. **Pick the leap-of-faith assumptions** — high importance, high uncertainty.
4. **Hypothesis per LOFA:** "We believe [X]. We'll know it's true when [measurable result ≥ threshold]."
5. **Cheapest test** that could disprove it (fake door, concierge, prototype, smoke test) and the kill/continue threshold.
6. **Sequence** tests cheapest-and-riskiest first.

## Output spec
An importance×uncertainty grid (or ranked list), the leap-of-faith assumptions, a hypothesis + cheapest test + success threshold for each, and a test sequence.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the bet/finding and its confidence; existing evidence across sources to pre-fill what's known.
- **Outputs to Sprntly:** hypotheses + thresholds registered to the outcome graph; tests written to the backlog; trust-ladder gating (don't graduate past Alpha until LOFAs pass).
- **Degrades to:** standalone from the idea text.

## Quality checklist (the bar)
- [ ] Assumptions span desirability, viability, feasibility, usability — not just one.
- [ ] Riskiest are chosen by importance×uncertainty, not gut.
- [ ] Each hypothesis is falsifiable with a numeric threshold.
- [ ] Tests are the cheapest that could disprove the belief.

## Known gaps / limitations
- Teams under-rate uncertainty on assumptions they like; the grid mitigates but can't eliminate this bias.
- Test design here is lightweight; statistically rigorous tests route to `experiment-design`.

## Worked example
**Input:** "Add an AI summary feature to our docs tool."
**Output (abridged):** LOFA (desirability): users will trust an AI summary enough to act on it. Hypothesis: "≥40% of users who see a summary expand <2 source sections (i.e., they trust it)." Test: Wizard-of-Oz summary on 50 docs; threshold 40%. LOFA (viability): inference cost per doc < margin. Test: cost model on sampled doc sizes. Sequence: trust test first (cheapest, kills the idea fastest).
