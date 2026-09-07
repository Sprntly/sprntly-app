---
name: product-vision
description: Draft a compelling, concrete product vision and narrative. Use when the user says "write a product vision", "vision statement", "where are we going", "paint the future", or needs to inspire a team/investors. Produces a vivid, falsifiable vision of the future state plus a short narrative — not vague platitudes.
---

# Product Vision

## What it does
Produces a product vision that is both inspiring and concrete: a vivid picture of the changed world if you succeed, anchored to a real customer and a definable future state, plus a short narrative that makes people want to build it. It avoids the empty-platitude trap ("delight users everywhere").

## When to use / when NOT to use
- **Use** to align a team or pitch a future; as the top of the strategy stack.
- **Do NOT use** for the full strategy (`product-strategy-stack`) or near-term roadmap (`roadmap`).

## Inputs
- **Required:** the product/company and who it serves.
- **Optional:** time horizon, the change you believe in, target audience for the vision. *If missing, default to a 3-year horizon and a customer-centered frame.*

## Method (methodology)
Future-state narrative + "day in the life" + falsifiability.
1. **Anchor customer + horizon** — a specific user, a specific year.
2. **Paint the future state** — what's true in their world that isn't true today; show it concretely ("a day in the life").
3. **The shift** — the core change you're betting on (behavior, technology, market).
4. **Why it matters** — the stakes; why this future is worth building.
5. **Falsifiability** — what would have to be observably true for the vision to be realized (so it's a destination, not a mood).
6. **Compress** to a memorable statement + a one-paragraph narrative.

## Output spec
A one-sentence vision statement · a one-paragraph narrative · a concrete "day in the life" of the future · the core shift being bet on · the observable markers that the vision is realized.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the company's mission and strongest customer signals to ground the future state.
- **Outputs to Sprntly:** the vision as the root reference the strategy stack and roadmap trace to.
- **Degrades to:** standalone from product + audience.

## Quality checklist (the bar)
- [ ] The future state is concrete (a real scene), not a platitude.
- [ ] It names the core shift being bet on.
- [ ] It's falsifiable — you could tell if it came true.
- [ ] It's memorable enough to repeat without notes.

## Known gaps / limitations
- A vision is a bet, not a plan; without strategy/roadmap it's inert — link onward.
- Inspiring and wrong is dangerous; pressure-test the underlying shift with `red-team-review`.

## Worked example
**Input:** "Vision for an AI execution platform for product teams."
**Output (abridged):** 2028, a 5-person team ships what a 100-person team ships today. A day in the life: the PM opens Monday to a ranked brief of what to build, approves one, and an agent drafts the PRD, hands it to a coding agent, and reports back whether last week's launch moved the metric — the PM spends the day on judgment, not coordination. Shift: organizational context + agents collapse the coordination tax. Realized when: median team size to ship a given scope drops measurably.
