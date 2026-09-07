---
name: status-report
description: Write a crisp project status update. Use when the user says "write a status update", "project status", "weekly update", "RAG status", or "summarize where the project is". Produces a scannable status with a clear RAG signal, highlights, risks, and explicit asks - not a task diary.
---

# Status Report

## What it does
Produces a project status update that a busy reader absorbs in 30 seconds: an overall health signal (RAG - red/amber/green), the few things that matter, the risks, and - crucially - the explicit asks/decisions needed. It leads with the signal and the ask, not a chronological list of activity.

## When to use / when NOT to use
- **Use** for recurring project/initiative updates to stakeholders or leadership.
- **Do NOT use** for exec strategic narratives (`exec-narrative`) or detailed stakeholder-by-stakeholder comms (`stakeholder-update`).

## Inputs
- **Required:** the project and what's happened/where it stands.
- **Optional:** target reader, prior status (for trend), metrics, blockers. *If missing, default to leadership audience and ask for the current blockers.*

## Method (methodology)
BLUF + RAG + highlight/risk/ask structure.
1. **Overall RAG** + the one-line "why" (and the trend vs last update).
2. **Highlights** - the 2-4 things that actually moved (outcomes, not activity).
3. **Risks/issues** - what's threatening the plan + mitigation/owner.
4. **Asks** - the specific decisions/help/unblocks needed, from whom, by when.
5. **Compress** - scannable; detail lives in links, not the update.

## Output spec
RAG + one-line why (+ trend) · highlights (outcomes) · risks + mitigations · explicit asks (who/what/when) · links for depth. Keep it short.

## Sprntly integration (optional)
- **Inputs from Sprntly:** progress vs goals from the outcome graph; in-flight items + blockers from the backlog (RAG can be data-derived).
- **Outputs to Sprntly:** the update as an artifact; asks become tracked decisions/tasks.
- **Degrades to:** standalone from provided status.

## Quality checklist (the bar)
- [ ] Leads with RAG + the ask, not a chronological diary.
- [ ] Highlights are outcomes, not activity.
- [ ] Risks have a mitigation and an owner.
- [ ] Asks are specific (who, what, by when).

## Known gaps / limitations
- RAG can be gamed (perpetual green); honest status needs a culture that rewards early amber - the skill encourages it but can't enforce it.
- Without prior status, trend is unavailable; note it.

## Worked example
**Input:** "Collab feature: on track, one eng out sick, need design review."
**Output (abridged):** Status: AMBER (was GREEN) - on scope, but a sick eng put sync work ~3 days at risk. Highlights: co-edit skeleton working in staging; beta partner confirmed. Risk: sync latency unverified at scale - load test next week (owner: lead). Ask: design review of conflict-resolution UX by Thu (from Maya) to keep beta on date.
