---
name: roadmap
description: Build an outcome-based roadmap. Use when the user says "build a roadmap", "Now Next Later", "what's our roadmap", "quarterly roadmap", or "stop the feature-factory roadmap". Produces a Now/Next/Later (or themed) roadmap organized by outcomes and confidence, not dated feature promises.
---

# Roadmap

## What it does
Builds a roadmap organized around outcomes and themes with honest confidence horizons (Now/Next/Later), instead of a dated list of features that pretends to predict the future. It ties each item to a strategic goal and communicates uncertainty truthfully.

## When to use / when NOT to use
- **Use** to plan and communicate direction over time.
- **Do NOT use** to set the strategy it should serve (`product-strategy-stack`) or to break work into stories (`story-mapping`).

## Inputs
- **Required:** the goals/strategy the roadmap serves and the candidate work.
- **Optional:** capacity, timelines, dependencies, stakeholder audience. *If missing, default to Now/Next/Later themed by outcome and flag capacity as unknown.*

## Method (methodology)
Now/Next/Later (Janna Bastow) + outcome-over-output + confidence horizons.
1. **Confirm the goal** each horizon serves (no orphan items).
2. **Theme the work** by outcome/problem, not feature.
3. **Place by confidence:** Now (committed, high confidence), Next (likely, medium), Later (directional, low). Resist false precision on far items.
4. **Tie to metrics** — each theme states the outcome it targets.
5. **Make tradeoffs explicit** — what's *not* on the roadmap and why.
6. **Tailor the view** to the audience (exec vs team vs customer-facing).

## Output spec
A Now/Next/Later (or themed) roadmap; each item = theme · target outcome/metric · confidence · strategic goal it serves. Plus an explicit "not now / not doing" list.

## Sprntly integration (optional)
- **Inputs from Sprntly:** prioritized backlog (`prioritize`), goals, and confidence from the outcome graph (confidence horizons become data-informed).
- **Outputs to Sprntly:** the roadmap as a living artifact; themes linked to their backlog items and outcomes.
- **Degrades to:** standalone from goals + candidate work.

## Quality checklist (the bar)
- [ ] Organized by outcome/theme, not a dated feature list.
- [ ] Every item ties to a strategic goal.
- [ ] Confidence is communicated; far items aren't falsely precise.
- [ ] What's explicitly NOT being done is stated.

## Known gaps / limitations
- Stakeholders often want dates; the skill provides confidence-based commitments and explains why date-certainty is a trap — but org culture may force a compromise view.
- A roadmap is only as good as the strategy above it.

## Worked example
**Input:** "Roadmap for activation goal, mixed backlog."
**Output (abridged):** Now: guided first-task + empty-state fix (outcome: activation 38→44%, high conf). Next: sample data, contextual checklists (medium). Later: AI-assisted setup (directional). Not now: SSO, dark mode (don't serve activation). Each tied to the activation goal.
