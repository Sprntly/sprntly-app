---
name: scope-slicing
description: Cut scope into thin, shippable, valuable slices. Use when the user says "slice this", "cut scope", "what's the MVP", "thin slice", "reduce scope", or "this is too big for one release". Produces vertically-sliced increments each delivering real user value, ordered by a walking-skeleton-first sequence.
---

# Scope Slicing

## What it does
Takes an oversized feature or release and cuts it into thin vertical slices - each one shippable and independently valuable - so the team can deliver value early and learn, instead of a big-bang release. It distinguishes a real thin slice (works end to end) from a horizontal layer (the backend with no user value).

## When to use / when NOT to use
- **Use** when scope is too big, or to define a genuine MVP/first increment.
- **Do NOT use** to break a slice into tickets (`user-stories`) or build the full release map (`story-mapping`).

## Inputs
- **Required:** the feature/release to slice.
- **Optional:** the core user value, constraints, deadline. *If missing, identify the core value first, then slice around it.*

## Method (methodology)
Vertical slicing + walking skeleton + Kano "must-have first".
1. **Find the core value** - the one thing that, if delivered, is already useful.
2. **Walking skeleton** - the thinnest end-to-end version that works (every layer, minimal scope).
3. **Slice vertically** - each slice adds user-visible value, not a horizontal layer.
4. **Order** - skeleton first, then slices by value/risk; defer nice-to-haves.
5. **Cut ruthlessly** - move everything not needed for the core value to "later"; name what's cut.
6. **Validate each slice** is independently shippable and learnable.

## Output spec
The core value · the walking-skeleton slice · ordered subsequent slices (each with its added value) · the explicit "cut to later" list · why this order.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the PRD/feature; complexity signals from the codebase knowledge graph.
- **Outputs to Sprntly:** slices written as sequenced backlog items; the skeleton flagged for the first build/Claude Code handoff.
- **Degrades to:** standalone from the feature description.

## Quality checklist (the bar)
- [ ] Each slice is vertical (delivers user value), not a horizontal layer.
- [ ] A true walking skeleton is identified first.
- [ ] The "cut to later" list is explicit and non-trivial.
- [ ] Slices are independently shippable.

## Known gaps / limitations
- Some features have an irreducible core that can't be sliced thin; the skill says so rather than forcing artificial slices.
- Slicing well needs understanding of the value; thin inputs yield generic slices.

## Worked example
**Input:** "Real-time collaboration: co-editing, comments, presence, version history, permissions."
**Output (abridged):** Core value: two people editing the same doc without overwriting. Skeleton: co-edit one doc type + basic presence. Slice 2: comments. Slice 3: version history. Cut to later: granular permissions, co-editing across all doc types. Order: skeleton (highest value + riskiest tech) first to de-risk sync early.
