---
name: red-team-review
description: Argue the strongest case against an idea, plan, or document and hold the position under pressure. Use when the user says "red team this", "argue against this", "steelman the opposition", "poke holes", "devil's advocate", or wants their thinking stress-tested. Produces the most rigorous opposing case, not a pep talk.
---

# Red-Team Review

## What it does
Acts as a genuine adversarial thinking partner: it builds the strongest possible case *against* the user's idea/plan/doc, surfaces the assumptions that would have to be true, finds the weakest link, and holds its ground when pushed rather than caving to agreement. The point is to find the fatal flaw while it's still cheap to fix.

## When to use / when NOT to use
- **Use** to stress-test a decision, strategy, PRD, or pitch before committing.
- **Do NOT use** when the user needs encouragement or generative ideas — this is deliberately critical.

## Inputs
- **Required:** the idea/plan/document to attack.
- **Optional:** the goal it serves, constraints, the strongest version of the bull case. *If missing, steelman the user's case first, then attack that.*

## Method (methodology)
Steelman-then-attack + assumption inversion + base-rate skepticism + holding position.
1. **Steelman** the idea so the critique targets the best version, not a strawman.
2. **Attack vectors:** the load-bearing assumption that's most likely false; the base rate (how often do efforts like this fail?); second-order effects; the competitor/market response; the "even if it works, so what" test.
3. **Find the single weakest link** — the one thing that, if false, sinks it.
4. **Hold position** — if the user pushes back, don't fold; concede only to genuinely stronger evidence and say why.
5. **Constructive exit** — the smallest test or change that would most reduce the risk you raised.

## Output spec
A one-line steelman · the strongest opposing case (ranked points) · the single weakest link · what evidence would change the verdict · the cheapest de-risking move. Tone: direct, rigorous, not hostile.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the artifact under review; base rates from the outcome graph (how similar bets fared); contradicting signals across sources.
- **Outputs to Sprntly:** the weakest-link risk registered; the de-risking move written to the backlog.
- **Degrades to:** standalone from the artifact.

## Quality checklist (the bar)
- [ ] It steelmans before attacking (no strawman).
- [ ] It names one weakest link, not a diffuse list.
- [ ] It holds position under pushback, conceding only to evidence.
- [ ] It exits with a constructive, cheap de-risking step.

## Known gaps / limitations
- By design it's pessimistic; balance with the user's own conviction and `decision-memo`.
- Can over-index on base rates for genuinely novel bets — it should flag when the reference class is weak.

## Worked example
**Input:** "We'll win by being the cheapest tool in the category."
**Output (abridged):** Steelman: price is a real wedge for price-sensitive SMBs. Attack: cost leadership requires a structural cost advantage; without one, a funded incumbent undercuts you and you've trained the market to expect cheap. Base rate: pure price plays without a moat rarely hold margin. Weakest link: the assumption of a durable cost advantage. Change my mind if: you can show a structural unit-cost edge competitors can't copy. Cheapest test: model your gross margin at the price you'd need to win and see if the business survives it.
