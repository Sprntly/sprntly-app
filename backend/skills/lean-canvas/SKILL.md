---
name: lean-canvas
description: Build a one-page business thesis and unit economics. Use when the user says "lean canvas", "business model canvas", "business case", "is this viable", "unit economics", or needs a one-page model of the business behind a product. Produces the nine interlocking blocks plus a basic unit-economics check (LTV/CAC, margin).
---

# Lean Canvas

## What it does
Captures the whole business thesis on one page across nine interlocking blocks (problem, customer segments, unique value prop, solution, channels, revenue, cost structure, key metrics, unfair advantage) and adds a basic unit-economics sanity check so the model isn't just a story. Merges the lightweight "business case" need.

## When to use / when NOT to use
- **Use** to model the business behind a product/feature or pressure-test viability.
- **Do NOT use** for product requirements (`prd-author`) or detailed financial modeling (hand to finance for that).

## Inputs
- **Required:** the product/business idea.
- **Optional:** pricing, costs, CAC, retention, market size. *If missing, populate the canvas and mark economics as assumptions to validate.*

## Method (methodology)
Ash Maurya's Lean Canvas + unit-economics check.
1. **Problem** (top 3) + existing alternatives.
2. **Customer segments** + early adopters.
3. **Unique value proposition** (+ high-level concept).
4. **Solution** (top features addressing the problems).
5. **Channels** to reach customers.
6. **Revenue streams** + pricing.
7. **Cost structure.**
8. **Key metrics** — the few numbers that show the model working.
9. **Unfair advantage** — what can't be easily copied/bought (the honest one, not "our team").
10. **Unit economics check:** rough LTV vs CAC, gross margin, payback — does the math survive?

## Output spec
The nine-block canvas · a unit-economics check (LTV/CAC, margin, payback with assumptions) · the riskiest block (where the model most likely breaks).

## Sprntly integration (optional)
- **Inputs from Sprntly:** revenue/retention/cost signals where connected; market read from `market-structure`.
- **Outputs to Sprntly:** the canvas as a reference; the riskiest block becomes a leap-of-faith assumption for `assumption-risk-map`.
- **Degrades to:** standalone; estimate-and-label economics.

## Quality checklist (the bar)
- [ ] "Unfair advantage" is something real and hard to copy, not a platitude.
- [ ] Unit economics are computed with stated assumptions, not asserted.
- [ ] The riskiest block is named.
- [ ] Key metrics are the few that actually prove the model.

## Known gaps / limitations
- Economics are directional without real data; flagged as assumptions.
- A coherent canvas can still be wrong if the problem isn't real — validate with discovery.

## Worked example
**Input:** "Freemium AI writing tool for marketers."
**Output (abridged):** UVP: on-brand copy in seconds. Revenue: $29/mo Pro. Unfair advantage: proprietary brand-voice fine-tuning per customer (accumulates, hard to copy) - not "great UX." Economics: CAC ~$60 (content+ads), Pro LTV ~$260 at 9mo retention -> LTV/CAC ~4.3, but free->paid conversion assumed 4% is the riskiest block. Margin healthy if inference cost < $3/user/mo.
