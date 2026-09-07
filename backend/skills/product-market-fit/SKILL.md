---
name: product-market-fit
description: Measure and diagnose product-market fit. Use when the user says "do we have PMF", "measure product-market fit", "Sean Ellis test", "are we ready to scale", or "PMF signals". Produces a multi-signal PMF read (the 40% test, retention shape, organic pull, NPS) with a verdict and what to fix if not yet there.
---

# Product-Market Fit

## What it does
Assesses whether a product has product-market fit using multiple converging signals rather than one vanity number, and - crucially - tells you what's missing if you're not there yet. It resists both premature "we have PMF!" claims and endless wandering without a definition.

## When to use / when NOT to use
- **Use** to judge readiness to scale, or to diagnose a stalled product.
- **Do NOT use** for ongoing retention mechanics (`retention-churn`) or market sizing (`market-structure`).

## Inputs
- **Required:** the product, target segment, and some usage/feedback signal.
- **Optional:** Sean Ellis survey results, retention curves, organic vs paid mix, NPS, sales-cycle data. *If missing, identify which PMF signals to gather and how.*

## Method (methodology)
Multi-signal PMF (Sean Ellis 40% test + retention flattening + organic pull + qualitative "very disappointed" segment).
1. **Sean Ellis test** - % who'd be "very disappointed" without the product; ~40%+ is the classic threshold. Look at the very-disappointed segment specifically (who are they, what job).
2. **Retention shape** - does the curve flatten for the core segment? (from `retention-churn`).
3. **Organic pull** - is growth increasingly organic/word-of-mouth, or all paid?
4. **Usage intensity** - are core users using it the way the value thesis predicts?
5. **Triangulate** - PMF is the convergence of these, not any single one.
6. **Verdict + gap** - have it / approaching / not yet, and the specific missing signal + how to close it.
7. **Segment focus** - PMF often exists in one segment first; identify it and recommend doubling down before broadening.

## Output spec
Each signal with its read · the converged verdict (have/approaching/not yet) · the best-fit segment where PMF is strongest · the specific gap + how to close it if not there.

## Sprntly integration (optional)
- **Inputs from Sprntly:** retention curves, organic/paid mix, survey + usage signals across sources.
- **Outputs to Sprntly:** the PMF verdict + best-fit segment as strategic context; the gap becomes a top opportunity.
- **Degrades to:** standalone; specify which signals to collect.

## Quality checklist (the bar)
- [ ] PMF is judged on multiple converging signals, not one number.
- [ ] The very-disappointed/core segment is characterized, not just counted.
- [ ] Retention-curve shape is part of the read.
- [ ] If not at PMF, the specific gap and the best-fit segment are named.

## Known gaps / limitations
- The 40% test is directional, not gospel; small samples mislead - flag n.
- PMF can be segment-specific; an "average" read can hide fit in one niche - the skill segments to avoid this.

## Worked example
**Input:** "B2B tool, 32% 'very disappointed', growth mostly paid."
**Output (abridged):** Sean Ellis 32% - approaching, not there. But the very-disappointed segment is concentrated in ops teams at 10-50-person companies (that niche is ~50%+). Retention: request curve. Organic pull: weak (mostly paid = caution). Verdict: approaching, with real fit in the ops/SMB niche. Gap: broaden too soon and you'll dilute - double down on the ops niche, get organic pull there first.
