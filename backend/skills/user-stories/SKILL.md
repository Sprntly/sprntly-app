---
name: user-stories
description: Package an implementation spec (or a PRD) into tracker-ready tickets — preserving end-to-end traceability — ready to paste into Jira or Linear. Use when the user says "write user stories", "break this into tickets", "acceptance criteria for X", "turn this PRD into stories/tickets", or "story breakdown". If the PRD has a machine-readable Part B (Implementation Spec), it INHERITS the requirements, acceptance tests, and dependency-ordered tasks rather than re-deriving them; with prose only, it generates INVEST stories with Given/When/Then criteria. Also splits out [ESCALATE] decision tickets and routes each ticket agent-ready vs. needs-human.
---

# User Stories & Tickets (spec-aware)

## What it does
Turns a feature/PRD into tracker-ready tickets. Its behavior depends on what it's given:
- **With a machine-readable spec (PRD Part B):** it *packages* what's already there — mapping the spec's dependency-ordered tasks and EARS requirements into human-shaped tickets, **inheriting each ticket's acceptance criteria from the spec's existing Given/When/Then tests** rather than re-writing them (which would risk drift from the spec the coding agent actually builds against). Every ticket carries a traceability chain: `ticket → task (Bx) → requirement (R#) → acceptance test → PRD goal`.
- **With prose only (no Part B):** it falls back to *generating* INVEST stories with Given/When/Then acceptance criteria from scratch (the classic behavior).

Every ticket it emits is **dual-layer**: a human-readable story + context block the team works from, AND a machine-readable acceptance block (the inherited Given/When/Then tests + traceability) the coding agent implements and is verified against. The two coexist in one ticket. It also does two things a pre-spec breakdown couldn't: it turns each **`[ESCALATE]`** item from the spec into a distinct **decision ticket** (assigned to a human, not the coding agent), and it **routes** every ticket as *agent-ready* (fully specified, stakes-reversible) vs. *needs-human* (escalation, or stakes-gated work).

## When to use / when NOT to use
- **Use** once the *what* is decided and you need the delivery breakdown for a tracker — whether from a full spec or a prose PRD.
- **Do NOT use** to decide what to build (`prd-author`), author the machine-readable spec itself (that's `prd-author` Part B), or build a release map across epics (`story-mapping`).

## Inputs
- **Required:** the feature/PRD/epic — ideally including Part B (Implementation Spec).
- **Optional:** personas, the team's story-point scale, tracker (Jira/Linear) for formatting. *If there's no Part B, generate criteria from the prose and flag it. If there's no persona, use a generic one and flag where a real one would change a story.*

## Method (methodology)
INVEST + Gherkin + Jeff Patton thin-slice, now layered over Spec-Driven Development: consume the spec when present, generate when not.

**If a machine-readable Part B exists (spec-aware mode):**
1. **Read Part B.** Take the requirements (EARS), the dependency-ordered tasks (with `[P]` parallel markers), the acceptance tests, the `[ESCALATE]` list, and the stakes gate.
2. **Regroup spec tasks into human stories.** Spec tasks are agent-shaped (e.g. "build read-only connector"); regroup them into vertically-sliced, user-valued, sprint-sized stories — one story may span several tasks, and the slicing follows INVEST, not the spec's task granularity.
3. **Inherit acceptance criteria — don't rewrite.** Attach each ticket's Given/When/Then directly from the spec's acceptance tests (Part B §Acceptance & DoD). Do not author new criteria that could disagree with what the agent implements against; if a story spans tasks, collect their tests.
4. **Carry the traceability chain** on every ticket: `ticket → task → requirement → acceptance test → PRD goal`.
5. **Escalations → decision tickets.** Each `[ESCALATE]` becomes its own ticket: the decision needed, who owns it, and which build tickets it blocks. Marked `needs-human`.
6. **Route each ticket.** `agent-ready` (fully specified + stakes-reversible → can hand to the coding agent) vs. `needs-human` (escalation, or stakes-gated/irreversible work needing review). Preserve dependency order and `[P]` parallel markers; mark the walking skeleton.

**If prose only (generate mode):**
1. Identify actors & capabilities. 2. Slice vertically (user-visible value, not layers). 3. Write `As a <persona>, I want <capability>, so that <outcome>.` 4. Write 2–5 Given/When/Then per story incl. ≥1 negative/edge case. 5. Right-size; note dependencies. 6. Add the un-fun cases (empty/error/permissions/limits). 7. Order by dependency + value; mark the walking skeleton. *(Flag that criteria were generated, not inherited from a spec — weaker guarantee.)*

## Output spec
**Every ticket carries TWO blocks — human-readable AND machine-readable:**
- **Human-readable block (for the team in the tracker):** Title · Story (`As a… I want… so that…`) · why it matters / context · dependencies · suggested size · route.
- **Machine-readable block (for the coding agent / verification):** the inherited Given/When/Then acceptance criteria (verbatim from Part B's tests — structured, testable), the traceability chain (`task → R# → test → PRD goal`), and the `agent-ready`/`needs-human` route + `[P]` markers.

Both blocks live in the same ticket — humans read the story and context, the agent reads (and is verified against) the machine block; neither is dropped. Plus a separate **Decision tickets** list for `[ESCALATE]` items. See `templates/story-template.md`.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the PRD + Part B spec from the PM agent (`prd-author`); personas + existing backlog from the knowledge graph (to avoid duplicate tickets).
- **Outputs to Sprntly:** structured backlog items with the full traceability chain; `agent-ready` tickets carry a `→ Claude Code` handoff; `needs-human` decision tickets routed to the owner; ticket completion verifiable against the inherited acceptance tests (not vibes).
- **Degrades to:** no Part B → generate-from-prose mode; no Sprntly → tracker-ready text to paste manually.

## Quality checklist (the bar)
- [ ] **Spec-aware when a Part B exists** — acceptance criteria are *inherited* from the spec's tests, not rewritten (no drift from what the agent builds against).
- [ ] Every ticket is vertically sliced (user value, not a tech layer), even when regrouped from agent-shaped spec tasks.
- [ ] Each ticket carries the **traceability chain** (`task → R# → test → PRD goal`) in spec-aware mode.
- [ ] Every `[ESCALATE]` item became a **decision ticket** with an owner; build tickets it blocks are linked.
- [ ] Every ticket is **routed** `agent-ready` vs. `needs-human`; dependencies, `[P]` markers, and the walking skeleton are preserved.
- [ ] In prose mode, criteria are present with ≥1 edge/negative case and flagged as generated.
- [ ] **Each ticket has BOTH a human-readable block (story + context) AND a machine-readable block (Given/When/Then acceptance criteria + traceability) — neither dropped.**


## Absorbed from the field (added capability)
**Story formats (added):** in addition to standard user stories, emit on request (a) job stories — 'When [situation], I want to [motivation], so I can [outcome]' — for motivation-first framing, and (b) WWA items — Why / What / Acceptance — for backlog brevity. The spec-aware traceability and acceptance criteria are preserved across whichever format is chosen; format is a surface choice, not a quality change.

## Known gaps / limitations
- Inherited criteria are only as good as Part B; if the spec is weak, the tickets inherit that (pair upstream with `prd-critique`).
- Story sizing is advisory; only the delivery team can truly estimate.
- Regrouping agent-tasks into human stories is judgment — it can mis-slice; the dependency graph from the spec is the safeguard.
- It packages/routes scope; it won't re-litigate scope (`prd-critique`).

## Worked example
**Input:** the Measurement-agent PRD *with* Part B (Implementation Spec).
**Output (abridged, spec-aware mode):**
- **Ticket:** *As a PM, I want a verdict only when there's real before/after data, so that I'm never shown a fabricated outcome.* — **Inherited AC** (from R5/R7 tests): "Given before/after data for the full window, When the verdict computes, Then verdict ∈ {moved,no_movement,regressed} with evidence; Given insufficient data, Then verdict = inconclusive." **Trace:** T4 → R5,R7 → §B8 tests → PRD "measured outcome." **Route:** agent-ready. **Deps:** T2,T3 (`[P]`).
- **Ticket:** *…surface the readout to the PM and write it back, taking no further action.* **Trace:** T5 → R9. **Route:** agent-ready.
- **Decision ticket:** *Resolve the metric-source connector contract.* (`[ESCALATE]`/`[ASSUMPTION→T0]`) — **Owner:** eng lead. **Blocks:** the connector + verdict tickets. **Route:** needs-human.
