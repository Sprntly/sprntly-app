---
name: tech-spec
description: Turn a PRD or feature into an engineering-facing technical specification. Use when the user says "write a tech spec", "engineering spec", "design doc", "technical design for X", or wants to translate product requirements into something engineers can implement. Produces approach, interfaces/data model, dependencies, rollout, observability, and a test plan. Surfaces technical risks and open decisions for engineering to own.
---

# Technical Spec

## What it does
Translates a product requirement into an engineering design document: the proposed approach, key interfaces and data changes, dependencies, rollout/migration, observability, and a test plan — while clearly separating decisions the PM owns from decisions engineering owns. It writes the *PM-authorable* parts well and marks the rest as `[ENG DECISION]` so it never fakes architecture it can't justify.

## When to use / when NOT to use
- **Use** to bridge an approved PRD into an implementable spec, or to give engineering a strong first draft to react to.
- **Do NOT use** as a substitute for an engineer's system design on novel/complex architecture — it produces a scaffold and the obvious parts, not deep architecture.

## Inputs
- **Required:** the PRD or feature description.
- **Optional:** current architecture/stack, existing services/APIs, data model, SLAs, compliance constraints. *If missing, write the spec against stated requirements, mark architecture-dependent choices `[ENG DECISION: needs current stack]`, and list the questions engineering must answer.*

## Method (methodology)
Based on the standard design-doc pattern (context → goals → approach → alternatives → risks → rollout → testing) and the principle that a spec exists to surface decisions, not hide them.
1. **Restate scope & non-goals** from the PRD in engineering terms; list explicit assumptions.
2. **Proposed approach** at a component level; note 1–2 alternatives considered and why not.
3. **Interfaces & data:** API/contract sketches, data model changes, backward-compatibility/migration. Mark anything stack-specific as `[ENG DECISION]`.
4. **Dependencies & sequencing:** services, teams, order of work, feature flags.
5. **Cross-cutting:** performance budget, security/permissions, privacy/compliance, failure modes.
6. **Observability:** what to log/measure to know it works and to debug it.
7. **Rollout & test plan:** flagging, phased %, kill switch; unit/integration/load/manual test coverage tied to acceptance criteria.
8. **Open questions** for eng review.

## Output spec
Sections: Context & links · Goals / non-goals (eng framing) · Assumptions · Proposed approach (+ alternatives) · Interfaces & data model · Dependencies & sequencing · Performance/security/compliance · Observability · Rollout & migration · Test plan · Open questions / `[ENG DECISION]`s.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the approved PRD artifact, plus codebase/architecture entities from the knowledge graph (services, ownership, prior specs) to ground interfaces and dependencies in reality.
- **Outputs to Sprntly:** the spec artifact linked to the PRD; a build-ready handoff package for Claude Code (scope + interfaces + test plan); risk items written to the dependency tracker.
- **Degrades to:** with no codebase context, produce a stack-agnostic spec and explicitly list the architecture questions engineering must resolve.

## Quality checklist (the bar)
- [ ] Every architecture-dependent claim is either grounded in supplied context or marked `[ENG DECISION]` — none are fabricated.
- [ ] Interfaces/data changes address backward compatibility/migration.
- [ ] Failure modes, observability, and a kill switch are covered.
- [ ] Test plan maps to the feature's acceptance criteria.
- [ ] Open questions are explicit and assigned.

## Known gaps / limitations
- Not a replacement for senior engineering judgment on hard systems problems; it scaffolds and surfaces, it does not architect.
- Quality of interfaces/dependencies depends heavily on supplied stack context; with none, it stays deliberately generic.
- Won't estimate effort credibly — that's the team's call.

## Worked example
**Input:** "Tech spec for real-time co-editing (from the collaboration PRD). Stack not provided."

**Output (abridged):**
- **Approach:** OT or CRDT-based sync layer + presence service. **Alternative:** last-write-wins (rejected — loses concurrent edits). `[ENG DECISION: OT vs CRDT — needs current data model + conflict requirements]`
- **Interfaces:** `WS /docs/{id}/stream` for edit/presence events; `GET /docs/{id}/state` for resync. Backward-compat: existing REST save path retained behind flag.
- **Failure modes:** network drop → buffer + resync; conflicting edits → deterministic merge; flood → rate-limit per session.
- **Observability:** sync latency p95, dropped-edit count, reconnect success rate.
- **Open questions:** persistence store for op log? max concurrent editors target? `[ENG DECISION x2]`
