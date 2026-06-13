---
name: positioning
description: Define product positioning and messaging. Use when the user says "position this product", "positioning statement", "messaging", "how do we describe this", or "we sound like everyone else". Produces a Dunford-style positioning (competitive alternatives, unique attributes, value, best-fit segment) and the core message hierarchy.
---

# Positioning

## What it does
Establishes how a product should be understood relative to the alternatives a customer already considers, what makes it uniquely valuable, and for whom it's the obvious best choice — then derives the message hierarchy from that. It fixes the "we sound generic" problem by anchoring on real alternatives and differentiated value.

## When to use / when NOT to use
- **Use** to differentiate a product, sharpen messaging, or fix a generic pitch.
- **Do NOT use** for competitor teardown (`competitive-intelligence-review`) or launch planning (`launch-gtm`).

## Inputs
- **Required:** the product and what it does.
- **Optional:** competitive alternatives, target customer, proof points. *If missing, infer alternatives and best-fit segment and label them.*

## Method (methodology)
April Dunford's positioning (Obviously Awesome): alternatives -> attributes -> value -> segment -> market frame.
1. **Competitive alternatives** — what the customer would use if you didn't exist (including DIY/nothing).
2. **Unique attributes** — what you have that they don't.
3. **Value** — the benefit those attributes enable, in customer terms (so-what).
4. **Best-fit segment** — who cares most about that value (positioning is sharpest when narrow).
5. **Market frame** — the category context you want to be understood within.
6. **Message hierarchy** — one-line value prop -> 3 supporting pillars -> proof points.

## Output spec
The five Dunford components · a chosen market frame · a message hierarchy (value prop, 3 pillars, proof) · 2-3 example headlines that follow from it.

## Sprntly integration (optional)
- **Inputs from Sprntly:** `competitive-intelligence-review` output (alternatives + weaknesses), win/loss themes, customer language from `interview-synthesis`.
- **Outputs to Sprntly:** positioning as a reference entity feeding `launch-gtm`, `customer-comms`, `product-one-pager`.
- **Degrades to:** standalone; infer-and-label.

## Quality checklist (the bar)
- [ ] Alternatives include DIY/non-consumption, not just named competitors.
- [ ] Value is stated as customer benefit, not features.
- [ ] The best-fit segment is specific (narrow beats broad).
- [ ] Messaging traces directly from the positioning, not bolted on.

## Known gaps / limitations
- Positioning built on aspirational (not real) differentiation collapses on contact — pressure-test attributes with `red-team-review`.
- Needs customer language to ring true; generic inputs yield generic output.

## Worked example
**Input:** "Position our PM-execution platform."
**Output (abridged):** Alternatives: Jira + docs + manual coordination, or raw Claude. Attribute: organizational context + closed outcome loop. Value: the team builds the right thing and knows if it worked, without the coordination tax. Best-fit segment: AI-native software teams of 5-30. Frame: "execution intelligence," not "PM tool." Value prop: decide, ship, measure, learn - every cycle.
