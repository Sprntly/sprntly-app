---
name: pricing-packaging
description: Design pricing model and packaging tiers. Use when the user says "how should we price this", "pricing tiers", "packaging", "what should we charge", or "value metric". Produces a value-metric choice, tier structure, and pricing logic grounded in willingness-to-pay and value, not cost-plus guessing.
---

# Pricing & Packaging

## What it does
Designs how a product is priced and packaged: the value metric (what you charge per unit of), the tier structure (who each tier is for and what unlocks the upgrade), and price points anchored to value and willingness-to-pay rather than cost-plus. It separates packaging (what's in each tier) from pricing (the number).

## When to use / when NOT to use
- **Use** to design or revise pricing/tiers.
- **Do NOT use** for the full business model (`lean-canvas`) or competitor pricing teardown (`competitive-intelligence-review`).

## Inputs
- **Required:** the product and its main customer segments.
- **Optional:** WTP data, competitor pricing, cost structure, current pricing, usage patterns. *If missing, design the structure and mark price points as hypotheses to validate (e.g., via Van Westendorp).*

## Method (methodology)
Value-based pricing + value-metric selection + good-better-best packaging + fence design.
1. **Value metric** — the unit that scales with value received (seats, usage, outcomes). A good value metric aligns price with value the customer gets.
2. **Segment WTP** — different segments value different things; identify the axis.
3. **Packaging** — good/better/best tiers; each tier targets a segment; the upgrade trigger (the "fence") is a feature/limit the next segment needs.
4. **Price points** — anchor to value delivered and WTP; sanity-check vs alternatives. Mark as hypotheses if no WTP data.
5. **Expansion path** — how revenue grows within an account over time.
6. **Avoid traps** — too many tiers, fences that punish power users, value metric that caps your own upside.

## Output spec
Chosen value metric + why · tier structure (who, what's included, upgrade fence) · price points (with WTP basis or labeled as hypotheses) · expansion path · trap-check notes.

## Sprntly integration (optional)
- **Inputs from Sprntly:** usage patterns + segment data to inform the value metric and fences; willingness-to-pay signals.
- **Outputs to Sprntly:** pricing hypotheses registered for validation; tier definitions as reference.
- **Degrades to:** standalone; mark prices as hypotheses.

## Quality checklist (the bar)
- [ ] The value metric scales with customer value, not just cost.
- [ ] Each tier targets a clear segment with a real upgrade fence.
- [ ] Prices are value/WTP-anchored or explicitly labeled hypotheses.
- [ ] An expansion path within accounts exists.

## Known gaps / limitations
- WTP is best measured, not guessed; without data, prices are starting hypotheses to test.
- Pricing changes have retention/perception effects beyond the model; pair with experiment + comms.

## Worked example
**Input:** "Price our PM execution platform."
**Output (abridged):** Value metric: active builds/seats (scales with team value), not flat per-seat that caps upside. Tiers: Team $499/mo (small teams, core loop), Build $1,999/mo (multi-team, integrations - fence = advanced connectors), Enterprise $85k+/yr (security, SSO, audit - fence = compliance). Prices = hypotheses pending WTP interviews. Expansion: seats + connectors. Trap avoided: no per-PRD metering that punishes heavy users.
