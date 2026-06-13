---
name: story-mapping
description: Build a user story map from an epic. Use when the user says "story map", "user story mapping", "map the user journey to stories", "break down this epic", or "release slices". Produces a Jeff Patton story map - the backbone of user activities with stories beneath, sliced into releases.
---

# Story Mapping

## What it does
Lays out the user's journey as a horizontal backbone of activities/steps, with the stories that fulfill each step arranged vertically beneath, then draws release slices across the map so each release is a coherent end-to-end experience. It keeps the whole journey visible so teams don't ship disconnected fragments.

## When to use / when NOT to use
- **Use** to break a large epic/journey into releasable, coherent slices while keeping the big picture.
- **Do NOT use** to slice a single feature thin (`scope-slicing`) or write individual stories' acceptance criteria (`user-stories`).

## Inputs
- **Required:** the epic / user journey to map.
- **Optional:** personas, priorities, release constraints. *If missing, map the canonical journey and label persona assumptions.*

## Method (methodology)
Jeff Patton user story mapping: backbone -> walking skeleton -> release slices.
1. **Backbone** - the sequence of user activities/steps across the journey (left to right, narrative order).
2. **Stories under each step** - the things the user does at that step, most-essential at top.
3. **Walking skeleton** - the top row: the minimal path through the whole journey end to end.
4. **Release slices** - horizontal lines grouping stories into releases; each release should let the user complete the journey (thinner, then richer).
5. **Spot gaps & alternatives** - missing steps, error paths, variations by persona.

## Output spec
The activity backbone · stories grouped under each step · the walking-skeleton (release 1) line · subsequent release slices · noted gaps/alternative paths.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the epic/PRD; personas + journey context from the knowledge graph.
- **Outputs to Sprntly:** the map as an artifact; release slices written as grouped backlog items feeding `user-stories`.
- **Degrades to:** standalone from the epic.

## Quality checklist (the bar)
- [ ] The backbone is the user's narrative journey, not a feature list.
- [ ] A walking skeleton crosses the whole journey (release 1 is end-to-end).
- [ ] Release slices each leave the user able to complete the journey.
- [ ] Gaps and alternative/error paths are noted.

## Known gaps / limitations
- Maps can get unwieldy for very large products; focus on one journey at a time.
- Visual maps are clearer than text; render the map as a diagram (e.g. mermaid) when a picture helps.

## Worked example
**Input:** "Epic: onboard a new team onto our tool."
**Output (abridged):** Backbone: sign up -> set up workspace -> invite team -> connect tools -> complete first task -> see value. Stories under "connect tools": connect calendar, connect Slack, skip-for-now. Skeleton (R1): sign up -> minimal workspace -> solo first task -> value (no team invite yet). R2: invites + one integration. Gap noted: error path when an integration fails.
