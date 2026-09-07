---
name: journey-map
description: Map a specific actor's end-to-end journey toward a goal — phases, actions, thoughts, emotions, and pain points — then turn the breakdowns into prioritized opportunities. Use when the user says "journey map", "customer journey", "map the experience", "where does the experience break", or wants to see a flow from the user's point of view. A discovery and alignment tool (NN/g style), not a feature list; anchored to one actor + one scenario; ends in ranked opportunities, not a pretty diagram.
---

# Customer Journey Map

## What it does
Visualizes the process a specific person goes through to accomplish a goal — as a timeline of actions enriched with their thoughts, emotions, and pain points — then converts where the experience *breaks down* into prioritized opportunities. It's a discovery/alignment artifact that builds a shared mental model of the real experience, deliberately not a roadmap of features.

## When to use / when NOT to use
- **Use** to understand and align on an end-to-end experience and find where it breaks.
- **Do NOT use** for a single-screen UX critique, a service blueprint of internal ops, or backlog prioritization (`prioritize`).

## Inputs
- **Required:** the **actor** (one persona) + the **scenario/goal** (one journey, not "all users").
- **Optional:** research, support data, analytics, the actual flow. *Unresearched emotions/quotes are labeled `[assumed]`, never fabricated as real quotes.*

## Method (methodology)
NN/g journey mapping.
1. **Anchor:** one actor, one scenario, one goal, a starting trigger and an end state. A map of "everyone" maps no one.
2. **Lay the phases** across the timeline (the few real stages, not every click).
3. **Per phase capture:** actions · thoughts (real quotes where available, `[assumed]` otherwise) · emotion (a simple high/neutral/low curve) · pain points.
4. **Find the breakdowns** — the lowest-emotion, highest-pain phases; the drop-off cliffs.
5. **Convert to prioritized opportunities** — each tied to a phase + pain + evidence + impact, ranked. This is the output that matters.

## Output spec
The map (phases × actions/thoughts/emotions/pain), an emotion curve, then **prioritized opportunities** (phase · pain · evidence · impact · candidate move). Table for the phase grid; a visual emotion curve can be requested.

## Sprntly integration (optional)
- **Inputs:** persona from `persona-segment`; pains from `voice-of-customer-report` / `interview-synthesis`; analytics for drop-off.
- **Outputs:** opportunities → `opportunity-tree` / `prioritize`; pains registered as signals.
- **Degrades to:** standalone from actor + scenario (emotions labeled assumed).

## Quality checklist (the bar)
- [ ] One actor + one scenario + one goal — not "all users."
- [ ] Emotions/quotes are real where sourced, `[assumed]` otherwise; no invented quotes.
- [ ] Breakdowns identified, not just a neutral flow.
- [ ] Ends in ranked opportunities tied to phases — not a diagram for its own sake.

## Known gaps / limitations
- Without research it's a hypothesis map — useful for alignment, weak as evidence; pair with `interview-synthesis`.
- One map = one scenario; complex products need several.

## Worked example
**Input:** actor "first-time advertiser," goal "boost a post." Phases: discover Boost → set up → pay → review → live. Emotion craters at "review" (rejection, no reason) and "pay" (charged, nothing ran). Opportunities ranked: in-flow rejection reason (high), no-charge-until-delivery (high).
