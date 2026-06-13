---
name: working-backwards
description: Write an Amazon-style PR/FAQ working backwards from the customer. Use when the user says "PR/FAQ", "press release FAQ", "working backwards", "Amazon-style doc", or wants to validate an idea from the customer outcome backward. Produces a future press release plus the hard FAQ that pressure-tests whether the idea is worth building.
---

# Working Backwards (PR/FAQ)

## What it does
Produces an Amazon-style PR/FAQ: a mock press release written as if the product already launched (forcing clarity on the customer benefit), followed by the internal+external FAQ that surfaces the hard questions - feasibility, economics, why-us, why-now. It's a thinking tool that kills weak ideas before they consume engineering.

## When to use / when NOT to use
- **Use** early, to pressure-test an idea by starting from the customer outcome.
- **Do NOT use** as the build spec (`prd-author`) - the PR/FAQ precedes it.

## Inputs
- **Required:** the product/feature idea and the intended customer.
- **Optional:** the problem evidence, business model, constraints. *If missing, draft and label assumptions; the FAQ will expose the gaps.*

## Method (methodology)
Amazon Working Backwards: press release + FAQ.
1. **Press release** (1 page, as if launched): headline, sub-head, the customer problem, the solution, a customer quote, how to get started. Written in plain customer language - if you can't make it compelling, the idea may be weak.
2. **Customer FAQ** - the questions a customer would ask (what is it, how much, how is it different, do I need X).
3. **Internal/stakeholder FAQ** - the hard ones: why will customers want this, why are we the ones to build it, what's the business model, what's the riskiest part, what could go wrong, what will it cost.
4. **The honesty test** - if the FAQ answers are weak or hand-wavy, the idea isn't ready.

## Output spec
A one-page mock press release · a customer FAQ · an internal FAQ hitting feasibility/economics/why-us/why-now/risks · a verdict on whether the idea survives the FAQ.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the opportunity + evidence/confidence; market read for the why-now/why-us answers.
- **Outputs to Sprntly:** the PR/FAQ as an artifact; weak FAQ answers become assumptions for `assumption-risk-map`; survivors feed `prd-author`.
- **Degrades to:** standalone; label assumptions.

## Quality checklist (the bar)
- [ ] The press release is in customer language and genuinely compelling (or flagged as weak).
- [ ] The internal FAQ asks the hard questions, not softballs.
- [ ] Economics and "why us / why now" are addressed.
- [ ] There's an honest verdict, not just a polished doc.

## Known gaps / limitations
- A compelling PR can mask a bad idea if the FAQ is soft - the skill deliberately makes the FAQ adversarial (pairs with `red-team-review`).
- It tests the idea's framing, not market truth; validate demand separately.

## Worked example
**Input:** "AI that auto-generates PRDs from customer signals."
**Output (abridged):** PR headline: "Ship the right thing - PRDs that write themselves from what your customers actually need." Customer quote, plain-language how-to. Internal FAQ: Why will PMs trust an auto-PRD? (riskiest - needs human-in-loop). Why us? (org-context + outcome loop). Economics? (inference cost vs seat price). Verdict: survives if the trust/HITL answer holds - that's the leap-of-faith assumption to test first.
