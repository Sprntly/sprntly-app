---
name: sprint-planner
description: Turn a backlog + a goal into a realistic sprint plan — capacity-checked story selection, a clear sprint goal, dependencies and risks surfaced — so the team commits to what it can actually finish. Use when the user says "plan the sprint", "sprint planning", "what goes in this sprint", "capacity planning", or has a backlog and a timebox. Selects against real capacity, names the one sprint goal, flags over-commitment and dependencies; ends with a committed set and what was deliberately left out.
---

# Sprint Plan

## What it does
Takes a prioritized backlog, a team capacity, and a timebox, and produces a sprint plan the team can actually hit: one clear **sprint goal**, a **capacity-checked** selection of items, surfaced **dependencies and risks**, and an explicit list of what was left out and why. The job is an honest commitment, not a wish list that slips.

## When to use / when NOT to use
- **Use** to plan a single sprint/iteration from a ready backlog.
- **Do NOT use** to prioritize the backlog itself (`prioritize`), build the roadmap (`roadmap`), or write the stories (`user-stories`).

## Inputs
- **Required:** the candidate items (ideally with estimates) + the timebox.
- **Optional:** team capacity/availability, velocity history, dependencies, the sprint objective. *Missing capacity is estimated and labeled; never silently assumed.*

## Method (methodology)
1. **Set one sprint goal** — the outcome this sprint is for; items serve it.
2. **Establish real capacity** — adjust for PTO, on-call, meetings, carry-over; use velocity if available, else a labeled estimate.
3. **Select to capacity, not beyond** — pull highest-priority items that fit; stop at the line. Flag if the goal needs more than fits (that's a scope conversation, not a heroics commitment).
4. **Surface dependencies & risks** — cross-team blockers, sequencing, the riskiest item; mark what must start first.
5. **Name the cut list** — what didn't make it and why, so it's a decision not a surprise.

## Output spec
Sprint goal · committed items (within capacity) · capacity math · dependencies/risks · explicit cut list. Table for the committed set; prose for goal + risks.

## Sprntly integration (optional)
- **Inputs:** ranked backlog from `prioritize`; estimates/velocity and capacity from the knowledge graph.
- **Outputs:** committed set + sprint goal registered; over-commitment flagged as a decision; cut items back to the backlog.
- **Degrades to:** standalone from items + timebox (capacity labeled estimated).

## Quality checklist (the bar)
- [ ] Exactly one sprint goal; items ladder to it.
- [ ] Selection fits real (adjusted) capacity; over-commitment flagged, not hidden.
- [ ] Dependencies/risks and a start-order surfaced.
- [ ] Explicit cut list — what's out and why.

## Known gaps / limitations
- Only as good as the estimates and stated capacity; garbage in, optimistic out.
- It plans; it doesn't manage mid-sprint change (use `retrospective` after).

## Worked example
**Input:** 14 ready items, 2-week sprint, 4 devs, one on-call. Goal: "ship in-flow rejection reason." Capacity fits 6 items; selects the 6 serving the goal, flags the billing item as cross-team blocked (start T0 research first), cuts 8 with reasons.
