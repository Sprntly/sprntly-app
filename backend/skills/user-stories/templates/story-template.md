# <Feature> — Tickets
*Spec-aware mode: acceptance criteria inherited from PRD Part B. Each ticket has a HUMAN block + a MACHINE block.*

## Build tickets

### <KEY-1> — <short title>
**— Human-readable —**
- **Story:** As a <persona>, I want <capability>, so that <outcome>.
- **Context / why:** <1–2 lines a teammate needs>
- **Dependencies:** <…> · **Size:** <S/M/L> · **Route:** <agent-ready / needs-human> · **Skeleton:** <yes/no>

**— Machine-readable (for the coding agent / verification) —**
```gherkin
# Acceptance criteria — inherited verbatim from Part B §Acceptance tests (do not rewrite)
Given <state>
When <event>
Then <observable result>
# (one scenario per inherited test, incl. failure branches)
```
- **Trace:** `<task> → <R#> → <Part B test> → <PRD goal>`
- **Parallelizable:** `[P]` <yes/no> · **Verifies:** <R#…>

## Decision tickets (needs-human — from Part B `[ESCALATE]` / `[ASSUMPTION → T0]`)
### <KEY-D1> — <decision needed>
- **Decision:** <what must be settled> · **Owner:** <who> · **Blocks:** <tickets> · **Route:** needs-human

## Routing summary
- **Agent-ready:** <…> · **Needs-human:** <…> · **`[P]`:** <…>
- **Definition of Done (feature):** all inherited tests pass · every requirement has a passing test · decisions resolved · cross-cutting addressed · independent verification passes.
