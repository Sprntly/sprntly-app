---
name: continuous-discovery
description: Build and run a continuous discovery practice using an Opportunity Solution Tree. Use when the user says "set up discovery", "opportunity solution tree", "weekly discovery cadence", "connect interviews to outcomes", or wants to move from project-based research to ongoing discovery. Produces an OST tied to a desired outcome plus a weekly cadence.
---

# Continuous Discovery

## What it does
Turns a desired business outcome into a structured Opportunity Solution Tree (OST) and a sustainable weekly discovery rhythm, so the team is always learning about customers — not running a one-off research project. It forces opportunities to be customer needs (not features) and keeps solutions traceable to a measured outcome.

## When to use / when NOT to use
- **Use** to stand up an ongoing discovery practice or to map opportunities under an outcome.
- **Do NOT use** for a single interview guide (`interview-guide`), synthesizing notes (`interview-synthesis`), or one-time idea validation.

## Inputs
- **Required:** the desired outcome (ideally a metric to move).
- **Optional:** known customer segments, existing research, team capacity, current solutions in flight. *If missing, ask for the outcome first; everything else can be drafted and labeled.*

## Method (methodology)
Teresa Torres' continuous discovery + Opportunity Solution Tree.
1. **Anchor the outcome** at the root — a measurable result, not an output.
2. **Map opportunities** — customer needs/pains/desires surfaced from research, framed as the customer's language, never as solutions.
3. **Branch sub-opportunities** to the granularity where a solution becomes obvious.
4. **Attach solutions** to the smallest relevant opportunity (many per opportunity).
5. **Plan experiments** for the riskiest assumptions behind chosen solutions.
6. **Set the cadence** — weekly touchpoints with customers, continuous interviewing, assumption tests; define who/when/how.
7. **Pick the next opportunity** to target based on outcome impact × confidence.

## Output spec
An OST (outcome → opportunities → sub-opportunities → solutions → experiments) rendered as an indented tree or mermaid graph, plus a weekly cadence plan (touchpoints, owners, artifacts) and the next target opportunity with rationale.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the outcome + ranked findings from the Monday Brief; customer signals across the 15 sources to seed opportunities; knowledge-graph segments.
- **Outputs to Sprntly:** the OST as a living artifact; target opportunities written to the backlog; experiments registered to the outcome graph for later learning.
- **Degrades to:** with no Sprntly context, ask for the outcome and any research, then build the tree manually.

## Quality checklist (the bar)
- [ ] Root is an outcome/metric, not an output.
- [ ] Every opportunity is a customer need in customer language — zero smuggled solutions.
- [ ] Solutions hang off specific opportunities, not the root.
- [ ] Riskiest-assumption experiments are named.
- [ ] The cadence is realistic for the team's capacity.


## Absorbed from the field (added capability)
**Mode + multi-perspective ideation (added):** explicitly run in NEW-product vs EXISTING-product mode (they need different questions), and when ideating, generate from multiple lenses — PM, designer, engineer — so ideas aren't single-viewpoint. Discovery stays evidence-first; for new products, lean on lean-startup pretotype tests where there's no usage data yet.

## Known gaps / limitations
- An OST is only as good as the research feeding it; with no customer contact it produces a hypothesis tree, not a validated one — label it as such.
- Doesn't run the interviews; pairs with `interview-guide` + `interview-synthesis`.
- Can sprawl; enforce "stop branching when the solution is obvious."

## Worked example
**Input:** "Outcome: increase activation rate of new signups from 38% to 50%."
**Output (abridged):** Root: activation 38→50%. Opportunities: "I don't understand what to do first," "I don't see value before being asked to configure," "I lose my work when I leave." Sub-opp under #1: "the empty state gives no next step." Solutions on it: guided first-task, sample data, checklist. Experiment: fake-door test of guided first-task vs sample data; riskiest assumption = users want guidance over exploration. Cadence: 3 customer interviews/week, 1 assumption test/week, Friday synthesis.
