---
name: tech-discovery-docs
description: Document technical exploration - spike summaries, Architecture Decision Records, and design rationale. Use when the user says "write a spike summary", "ADR", "architecture decision record", "document this technical decision", "design rationale", or "why did we choose this approach". Produces the right technical-discovery doc with options, tradeoffs, the decision, and its consequences.
---

# Tech Discovery Docs

## What it does
Produces the documentation that captures technical decisions and explorations so they're not lost or re-litigated: a **spike summary** (what we learned from a timeboxed investigation), an **Architecture Decision Record** (Nygard format: context, decision, consequences), or a **design rationale** (why this approach over alternatives). It picks the right format for the situation and keeps the focus on the *why*, which is what future readers need.

## When to use / when NOT to use
- **Use** to record a technical investigation, an architecture decision, or the reasoning behind a design choice.
- **Do NOT use** for product requirements (`prd-author`) or the implementation spec itself (`tech-spec`) - this captures decisions and explorations, not the build instructions.

## Inputs
- **Required:** the technical question, decision, or spike to document.
- **Optional:** options considered, constraints, the chosen approach, who decided. *If missing, structure the doc and use `[ENG INPUT NEEDED]` markers where technical facts must come from engineering.*

## Method (methodology)
Spike summary + Michael Nygard ADR + design-rationale (mirrors product-on-purpose's spike-summary/adr/design-rationale trio).
1. **Pick the format:**
   - **Spike summary** - for a timeboxed investigation: question, what was tried, what was learned, recommendation, remaining unknowns.
   - **ADR** - for a decision with lasting architectural impact: **Context** (forces at play), **Decision** (what was chosen), **Status** (proposed/accepted/superseded), **Consequences** (good and bad, what becomes easier/harder).
   - **Design rationale** - for a design choice: the options, the tradeoffs, why this one, what was rejected and why.
2. **Capture options + tradeoffs** honestly - including the ones not chosen (so the decision isn't re-opened blindly later).
3. **State consequences** - especially the negative ones you're accepting; this is the most valuable and most-skipped part.
4. **Mark status & supersession** - ADRs are immutable records; later decisions supersede rather than edit.
5. **Keep the why central** - future readers can see *what* in the code; they need *why*.

## Output spec
The chosen doc type, fully populated: for an ADR - Context · Decision · Status · Consequences (positive + negative); for a spike - Question · Investigation · Findings · Recommendation · Unknowns; for design rationale - Options · Tradeoffs · Choice + reasoning · Rejected alternatives. Engineering facts marked `[ENG INPUT NEEDED]` where unknown.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the decision/spike context; the codebase knowledge graph for affected components; related prior ADRs.
- **Outputs to Sprntly:** the doc stored as a durable knowledge-graph entity linked to the components and the PRD it supports; supersession tracked.
- **Degrades to:** standalone; use `[ENG INPUT NEEDED]` markers.

## Quality checklist (the bar)
- [ ] The format matches the situation (spike vs ADR vs rationale).
- [ ] Options NOT chosen are recorded with their tradeoffs.
- [ ] Negative consequences of the decision are stated, not just benefits.
- [ ] ADR status/supersession is handled (records aren't silently edited).
- [ ] The *why* is the centerpiece.

## Known gaps / limitations
- The PM authors structure and reasoning, but technical facts must come from engineering - hence `[ENG INPUT NEEDED]` markers; never invent technical claims.
- Over-documenting trivial decisions wastes time; reserve ADRs for choices with lasting impact.

## Worked example
**Input:** "Document our choice to use a deterministic event-driven core with an LLM narrator instead of an LLM-with-tools agent."
**Output (abridged, ADR):** **Context:** need reliable, auditable execution; LLM-with-tools is flexible but non-deterministic and hard to trust at handoffs. **Decision:** deterministic EDA core; LLM only narrates/explains, never controls flow. **Status:** Accepted. **Consequences:** + reproducible, auditable, trustable at HITL gates; + cheaper (less inference on the critical path); - more upfront engineering than a thin agent; - new capabilities need explicit events, not just a prompt. Rejected alternative: LLM-with-tools (faster to prototype, fails the trust/determinism requirement).
