---
name: growth-loop
description: Design a self-reinforcing growth loop. Use when the user says "design a growth loop", "growth engine", "how does this compound", "viral loop", or "we rely too much on paid". Produces a loop where the output of one cycle feeds the input of the next, with the math of whether it actually compounds.
---

# Growth Loop

## What it does
Designs a growth loop — a closed cycle where each turn feeds the next (new users -> create content/invites/value -> attract more users) — and tests whether it actually compounds or leaks. It reframes growth from a linear funnel into a reinforcing system, the Reforge way.

## When to use / when NOT to use
- **Use** to build sustainable, compounding growth beyond paid acquisition.
- **Do NOT use** for growth-direction strategy (`growth-vectors`) or retention analysis (`retention-churn`).

## Inputs
- **Required:** the product and how users currently arrive + get value.
- **Optional:** conversion/referral/retention rates per step, monetization. *If missing, design the loop structurally and mark the rates as the things to measure.*

## Method (methodology)
Reforge growth loops + loop-math (does the cycle's gain exceed its leak?).
1. **Loop type** — viral (user invites user), content (UGC/SEO), paid (revenue -> ads -> users), sales-assisted. Pick the dominant realistic one.
2. **Map the steps** of one full turn, from new input back to generating the next input.
3. **Quantify each step** — the rate at each transition.
4. **Loop math** — does the output per cycle exceed 1 input per input (compounds) or leak (<1, needs topping up)?
5. **Find the leak** — the step killing the loop, and the highest-leverage fix.
6. **Loop fit** — does it match the product's natural usage (forced loops fail)?

## Output spec
The loop type + diagram of one turn · the rate at each step · loop-math verdict (compounds vs leaks + the multiplier) · the limiting step + fix · a loop-fit sanity check.

## Sprntly integration (optional)
- **Inputs from Sprntly:** funnel/referral/retention rates from analytics to quantify the loop.
- **Outputs to Sprntly:** the limiting step becomes a tracked opportunity; the loop diagram a reference.
- **Degrades to:** standalone structural design.

## Quality checklist (the bar)
- [ ] The loop closes (output genuinely feeds the next input).
- [ ] Each step has a rate (or is flagged as needing measurement).
- [ ] The compounding math is shown, not assumed.
- [ ] Loop-fit to natural product usage is sanity-checked.

## Known gaps / limitations
- Loops that don't fit natural usage get bolted on and fail; the fit check guards against this but can't force product-market fit.
- Without real rates the math is illustrative; quantify with data.

## Worked example
**Input:** "Design tool, users export designs to share."
**Output (abridged):** Loop (content/viral): user creates -> exports with subtle watermark -> viewer sees it -> some click through -> sign up -> create. Steps: export rate 60%, view-through 200 viewers/export, CTR 1.5%, signup 25%. Per creator: 0.6 x 200 x 0.015 x 0.25 = 0.45 new users -> leaks (<1), needs paid top-up. Leak: CTR on the watermark. Fix: stronger, value-revealing attribution. Loop fits (sharing is natural).
