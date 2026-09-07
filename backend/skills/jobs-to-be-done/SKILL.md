---
name: jobs-to-be-done
description: Frame a customer Job to be Done and the forces acting on it. Use when the user says "JTBD", "jobs to be done", "what job is the customer hiring us for", "job statement", or wants to understand customer motivation beyond demographics. Produces a job statement, the four forces (push, pull, anxiety, habit), and the progress the customer seeks.
---

# Jobs to be Done

## What it does
Articulates the functional, emotional, and social job a customer is trying to get done, and maps the four forces that drive or block switching to your solution. It reframes the product around the progress the customer wants to make, not the customer's attributes.

## When to use / when NOT to use
- **Use** to clarify motivation, position a product, or explain why customers switch (or don't).
- **Do NOT use** as a substitute for evidence — pair with interviews; and for personas use `persona-segment`.

## Inputs
- **Required:** the product/feature and the customer context.
- **Optional:** switch interviews, the "hiring/firing" moments, competing alternatives. *If missing, draft a hypothesized job and label it as needing validation.*

## Method (methodology)
Christensen JTBD + Bob Moesta's forces of progress + switch interview timeline.
1. **Job statement:** "When [situation], I want to [motivation], so I can [expected outcome]."
2. **Layers:** functional, emotional, and social dimensions of the job.
3. **Four forces:** Push (problem with current state), Pull (attraction of the new), Anxiety (fear of the new), Habit (inertia of the old). Switching happens when push+pull > anxiety+habit.
4. **Competing alternatives:** what the customer "hires" today, including non-consumption and duct-tape workarounds.
5. **Implications:** what to amplify (push/pull) and reduce (anxiety/habit).

## Output spec
A job statement, the three job layers, a four-forces diagram with specifics in each quadrant, the competing alternatives, and design/positioning implications.

## Sprntly integration (optional)
- **Inputs from Sprntly:** interview themes (from `interview-synthesis`) and signals describing current workarounds; segment context.
- **Outputs to Sprntly:** the JTBD frame attached to the relevant opportunity; anxiety/habit items become candidate problems in the backlog.
- **Degrades to:** with no context, produce a hypothesized JTBD and flag the validation needed.

## Quality checklist (the bar)
- [ ] Job statement is about progress, not a feature or demographic.
- [ ] All four forces have specific, non-generic entries.
- [ ] Competing alternatives include non-consumption/workarounds.
- [ ] Implications distinguish what to amplify vs. reduce.

## Known gaps / limitations
- Hypothesized jobs masquerade easily as validated ones; always label evidence level.
- JTBD is a lens, not a quantification — size demand separately.

## Worked example
**Input:** "Note-taking app for consultants."
**Output (abridged):** Job: "When I finish a client call, I want to capture decisions and next steps fast, so I look prepared and never drop a commitment." Forces — Push: notes scattered across tools; Pull: one place synced to calendar; Anxiety: "will it be there when I need it live in front of the client?"; Habit: trusty legal pad. Alternatives: legal pad, generic notes app, memory. Implication: reduce anxiety with offline reliability + instant retrieval.
