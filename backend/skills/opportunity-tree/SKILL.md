---
name: opportunity-tree
description: Build an Opportunity Solution Tree (Teresa Torres) from real customer signal — outcome → opportunities → solutions → tests — so solutions stay tied to validated needs instead of pet features. Use when the user says "opportunity solution tree", "OST", "map opportunities to solutions", "outcome to experiments", or wants to structure discovery from an outcome down. Built from interview/feedback evidence, not workshop guesses; flags solutions-disguised-as-opportunities; ends with the next test to run.
---

# Opportunity Solution Tree

## What it does
Turns a desired **outcome** into a structured tree of **opportunities** (real unmet needs/pains, in customer language) → **candidate solutions** → the **assumption tests** that would validate each — so the team can see why a solution exists and which opportunity it serves. The discipline that makes it work: opportunities come from *evidence*, and "users want a better export" is caught as a smuggled solution, not an opportunity.

## When to use / when NOT to use
- **Use** to structure discovery from an outcome down, or to sanity-check that a roadmap serves real needs.
- **Do NOT use** to synthesize raw interviews (`interview-synthesis` feeds this), design a single experiment (`experiment-design`), or prioritize a backlog (`prioritize`).

## Inputs
- **Required:** the desired outcome (a behavioral metric, not an output) + some customer signal.
- **Optional:** interview notes, feedback, usage data. *With thin evidence, opportunities are marked `[hypothesis — validate]`, never presented as established needs.*

## Method (methodology)
Teresa Torres' continuous discovery.
1. **Frame the outcome** as a customer-behavior change (e.g. "more teams complete a build loop"), not "ship X."
2. **Derive opportunities from evidence** — unmet needs/pains/desires in the customer's own words; each tagged with its source. Reject feature-framed "opportunities" (rewrite "wants export" → the underlying need).
3. **Branch solutions** under each opportunity (several per opportunity — avoid one-to-one, which is just a feature list).
4. **Attach the riskiest assumption + a test** to each solution (route to `experiment-design`).
5. **Pick the path:** the opportunity with the best outcome-leverage × evidence, and the cheapest test on its top solution.

## Output spec
The tree (outcome → opportunities → solutions → tests), opportunities tagged with evidence/hypothesis, smuggled-solutions flagged, then the recommended branch + next test. Indented text tree; a visual can be requested.

## Sprntly integration (optional)
- **Inputs:** outcome from the knowledge graph; opportunities from `interview-synthesis` / `voice-of-customer-report` signal.
- **Outputs:** chosen solution + test → `experiment-design`; opportunities registered as discovery entities.
- **Degrades to:** standalone from outcome + described signal.

## Quality checklist (the bar)
- [ ] Outcome is a behavior change, not a feature.
- [ ] Opportunities are evidence-backed needs in customer language; feature-framed ones rewritten or flagged.
- [ ] Solutions branch (not 1:1); each has a riskiest assumption + a test.
- [ ] Ends with the recommended branch + cheapest next test.

## Known gaps / limitations
- A tree built on weak evidence is a hypothesis map, not a discovery artifact — label it so.
- It structures discovery; it doesn't replace talking to customers.

## Worked example
**Input:** outcome "more new advertisers complete a first boost." Opportunities (from feedback): "I don't know why my boost was rejected", "I was charged for nothing." Solutions branch under each; riskiest assumption on the top one ("clarity will lift completion") → cheapest test = instrument the funnel + a copy A/B before building.
