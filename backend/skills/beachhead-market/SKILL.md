---
name: beachhead-market
description: Pick the ONE first market segment to win before expanding — scored on urgency of need, reachability, ability to pay, reference value, and competitive whitespace — so go-to-market starts focused instead of spread thin. Use when the user says "beachhead", "first segment", "where do we start", "which market first", "wedge segment", or is choosing an initial target. Forces one choice with the trade-offs explicit, names the expansion path it unlocks, and states what would change the pick.
---

# Beachhead Segment

## What it does
Selects the single best **first** segment to dominate — the wedge — and names the **expansion path** winning it unlocks. It scores candidate segments on the factors that actually determine an early win (urgent need, reachable, can pay, gives reference value, weak incumbent), forces one choice, and makes the trade-offs explicit. Focus is the point: a startup that targets everyone reaches no one.

## When to use / when NOT to use
- **Use** to choose the initial GTM target, or to sanity-check a chosen one.
- **Do NOT use** for full GTM execution (`launch-gtm`), ICP detail (`persona-segment`), or market sizing (`market-structure`).

## Inputs
- **Required:** the product + candidate segments (or enough to propose them).
- **Optional:** evidence per segment (need intensity, reachability, willingness-to-pay, competition). *Thin evidence → scores labeled low-confidence, not asserted.*

## Method (methodology)
Moore's "crossing the chasm" beachhead logic.
1. **List candidate segments** (or derive 3-5).
2. **Score each** on: urgency of the need · reachability (can we get to them cheaply?) · ability/willingness to pay · reference value (do they pull the next segment?) · competitive whitespace.
3. **Pick one** — the highest combined, with the trade-off named (what we're giving up by not picking the others).
4. **Name the expansion path** — which adjacent segments this beachhead unlocks and why (the bowling-pin sequence).
5. **State what would change the pick** + the one thing to validate.

## Output spec
Scored candidates (compact table), the chosen beachhead + rationale, the trade-off, the expansion path, and the validation. 

## Sprntly integration (optional)
- **Inputs:** segments from `persona-segment`; competitive whitespace from `competitive-intelligence-review`.
- **Outputs:** chosen segment → `launch-gtm` / `persona-segment`; expansion path to `product-strategy-stack`.
- **Degrades to:** standalone from product + candidates.

## Quality checklist (the bar)
- [ ] Scored on win-determining factors (need/reach/pay/reference/whitespace), not gut.
- [ ] Exactly ONE beachhead chosen; the trade-off of not picking others is named.
- [ ] Expansion path stated — the beachhead leads somewhere.
- [ ] What-would-change-the-pick + validation given; low-confidence scores flagged.

## Known gaps / limitations
- Scores are judgments; weak evidence → low-confidence pick to validate, not a verdict.
- Picks where to start, not how to sell (`launch-gtm`).

## Worked example
**Input:** Sprntly candidate segments. Scores favor "AI-native PM+eng teams already in Claude Code/Cursor" (urgent need, reachable, reference value high). Trade-off: passing on enterprise now. Expansion path: design partners → mid-market AI-native → broader enterprise once the loop is proven.
