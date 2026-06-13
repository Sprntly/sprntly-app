---
name: retrospective
description: Facilitate a structured retrospective into action items. Use when the user says "run a retro", "retrospective", "what went well/badly", "post-sprint review", "lessons learned", or wants to turn a period of work into improvements. Produces a structured retro with prioritized, owned action items - not just a vent session.
---

# Retrospective

## What it does
Structures a retrospective so it produces change, not just venting: it organizes observations, gets to root causes rather than symptoms, and converts the top issues into a small number of specific, owned action items with follow-up. It also captures durable lessons for organizational memory.

## When to use / when NOT to use
- **Use** after a sprint, launch, project, or quarter to improve how the team works.
- **Do NOT use** for incident triage (`incident-runbook`) or pre-launch risk (`pre-mortem`).

## Inputs
- **Required:** the period/project and what happened (notes, or prompts to elicit).
- **Optional:** metrics, prior retro action items (to check follow-through), team mood. *If missing, run the structure and elicit observations.*

## Method (methodology)
Structured retro formats (Start/Stop/Continue or 4Ls) + root-cause + action discipline + ABC (Always Be Coaching: leave the team knowing why).
1. **Check prior actions** - did last retro's items happen? (accountability).
2. **Gather observations** - what went well, what didn't, what was confusing, what to try (use a known format).
3. **Theme & root-cause** - cluster, then ask why for the top issues (don't fix symptoms).
4. **Prioritize** - pick the 1-3 highest-leverage issues; you can't fix everything.
5. **Action items** - specific, owned, time-bound; few enough to actually do.
6. **Capture lessons** - the durable insight worth remembering beyond this team.

## Output spec
Prior-action follow-up · themed observations · root causes for the top issues · 1-3 prioritized action items (specific, owned, dated) · durable lessons captured.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the period's shipped items + outcomes; prior retro actions from the knowledge graph.
- **Outputs to Sprntly:** action items written to the backlog with owners; lessons stored as durable knowledge-graph entries.
- **Degrades to:** standalone; elicit observations interactively.

## Quality checklist (the bar)
- [ ] Prior action items are reviewed for follow-through.
- [ ] Root causes are reached, not just symptoms.
- [ ] Only 1-3 action items - specific, owned, dated.
- [ ] A durable lesson is captured.

## Known gaps / limitations
- Retros fail on follow-through, not facilitation; the prior-action check is the main lever but culture decides.
- It can't surface what the team won't say; psychological safety is a precondition it can't create.

## Worked example
**Input:** "Sprint slipped, two features half-done."
**Output (abridged):** Prior actions: 1 of 2 done. Theme: overcommitment. Root cause (why x3): estimates didn't account for review cycles -> reviews queued on one person -> no WIP limit. Action items: (1) add review capacity to estimates - owner PM, next planning; (2) WIP limit of 3 - owner lead, now. Lesson: review is a resource, estimate it explicitly.
