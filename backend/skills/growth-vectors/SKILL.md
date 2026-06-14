---
name: growth-vectors
description: Identify where to grow next across market and product. Use when the user says "where should we grow", "Ansoff matrix", "expansion options", "new market or new product", or "growth strategy options". Produces the four Ansoff vectors mapped to your context with risk levels and a recommended sequence.
---

# Growth Vectors

## What it does
Maps the realistic ways to grow — deeper into the current market, into new markets, with new products, or diversification — and assesses each for fit, risk, and readiness, then recommends a sequence. Stops teams from chasing a risky new-market/new-product bet before exhausting cheaper growth.

## When to use / when NOT to use
- **Use** for growth-direction decisions and expansion planning.
- **Do NOT use** for in-product growth loops (`growth-loop`) or market attractiveness (`market-structure`).

## Inputs
- **Required:** the current product and market.
- **Optional:** current penetration, adjacent markets/products considered, capacity. *If missing, generate candidate vectors and label assumptions.*

## Method (methodology)
Ansoff matrix + risk-adjusted sequencing.
1. **Market penetration** (current product, current market) — more share, usage, retention. Usually cheapest/safest.
2. **Market development** (current product, new market) — new segments/geographies.
3. **Product development** (new product, current market) — expand the offering to existing customers.
4. **Diversification** (new product, new market) — highest risk.
5. **Assess each** for fit, evidence of demand, risk, and readiness.
6. **Sequence** — typically exhaust penetration before riskier vectors; name the recommended next 1-2 moves and what to validate first.

## Output spec
The four vectors populated with concrete options · risk + readiness rating each · the recommended sequence · what to validate before the riskier moves.

## Sprntly integration (optional)
- **Inputs from Sprntly:** penetration/usage data, expansion signals, segment performance from analytics.
- **Outputs to Sprntly:** chosen vectors become strategic initiatives; validation steps become experiments/opportunities.
- **Degrades to:** standalone from product + market.

## Quality checklist (the bar)
- [ ] All four vectors are considered, not just the exciting one.
- [ ] Penetration is assessed before riskier vectors are recommended.
- [ ] Each vector has a risk + readiness rating.
- [ ] The recommendation includes what to validate first.

## Known gaps / limitations
- Demand for new vectors needs validation; the skill flags this rather than assuming.
- It frames options; the actual bet needs `decision-memo` + `lean-canvas` for the numbers.

## Worked example
**Input:** "SMB scheduling tool, ~15% penetration of target SMBs."
**Output (abridged):** Penetration (low risk): 15% leaves room — improve activation/retention first. Market dev (med): adjacent verticals (clinics, salons). Product dev (med): add payments to existing base. Diversification (high): enterprise workforce scheduling — defer. Sequence: penetration now (cheapest), test payments with existing base next (validate attach rate), hold new verticals until penetration plateaus.
