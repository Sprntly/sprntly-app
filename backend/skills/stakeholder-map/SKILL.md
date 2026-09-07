---
name: stakeholder-map
description: Map stakeholders and plan alignment, including RACI. Use when the user says "stakeholder map", "who are the stakeholders", "stakeholder analysis", "RACI", "who needs to be aligned", or "manage up". Produces a power/interest map, each stakeholder's position and what they need, a RACI for key decisions, and an engagement plan.
---

# Stakeholder Map

## What it does
Identifies who matters for an initiative, plots them by influence and interest, captures each one's position (supporter/skeptic/blocker) and what they actually need, assigns decision roles via RACI, and produces an engagement plan - so alignment is managed deliberately rather than discovered in a meeting going sideways.

## When to use / when NOT to use
- **Use** to plan alignment for a significant initiative or navigate a complex org.
- **Do NOT use** to write one update (`stakeholder-update`) or an exec narrative (`exec-narrative`).

## Inputs
- **Required:** the initiative/decision and the people/roles involved.
- **Optional:** each person's known position, influence, history, the key decisions. *If missing, map by role and label positions as assumptions to verify.*

## Method (methodology)
Power/interest grid + position mapping + RACI + engagement strategy.
1. **List stakeholders** - anyone who can advance, block, or is affected.
2. **Power/interest grid** - manage closely (high power/high interest), keep satisfied (high power/low interest), keep informed, monitor.
3. **Position + need** - for each: supporter/neutral/skeptic/blocker, and the underlying interest driving it.
4. **RACI** for the key decisions - who is Responsible, Accountable, Consulted, Informed (exactly one Accountable each).
5. **Engagement plan** - for the critical few: the ask, the message that addresses their need, the channel, the timing (pre-wire before the room).
6. **Risk** - the blocker most able to derail it, and the plan to convert or contain.

## Output spec
Stakeholder list · power/interest grid · per-stakeholder position + underlying need · RACI for key decisions · engagement plan for the critical few · the top alignment risk + plan.

## Sprntly integration (optional)
- **Inputs from Sprntly:** stakeholder entities + interaction history from the knowledge graph; prior decisions they shaped.
- **Outputs to Sprntly:** the map as a living entity; engagement actions tracked; positions updated as they evolve.
- **Degrades to:** standalone; map by role, label positions.

## Quality checklist (the bar)
- [ ] Each stakeholder's *underlying need* is captured, not just their title.
- [ ] RACI has exactly one Accountable per decision.
- [ ] The plan includes pre-wiring the critical few before key meetings.
- [ ] The top blocker risk has a conversion/containment plan.

## Known gaps / limitations
- Positions are often hidden; map is a hypothesis until tested in 1:1s - flag assumptions.
- Org politics shift; the map needs updating as the initiative moves.

## Worked example
**Input:** "Getting buy-in to deprecate a legacy feature 8% of users still use."
**Output (abridged):** Manage closely: VP Eng (wants the maintenance gone - supporter), Head of Support (fears ticket spike - skeptic, need: migration plan + comms). RACI: Accountable = PM; Consulted = Support, Sales; Informed = the 8%. Engagement: pre-wire Support with a migration + comms plan before the steering meeting. Top risk: Support blocks on customer backlash - convert by co-owning the comms.
