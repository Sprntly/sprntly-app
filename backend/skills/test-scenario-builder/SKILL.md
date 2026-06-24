---
name: test-scenario-builder
description: Generate test scenarios from a story or spec — happy paths, edge cases, and error/failure handling — in Given/When/Then form so a team (or coding agent) can verify behavior. Use when the user says "test scenarios", "test cases", "QA scenarios", "how do we test this", or has a user story / acceptance criteria to validate. Covers the unhappy paths people forget; ties each scenario to the requirement it verifies; flags the high-risk ones to test first.
---

# Test Scenarios

## What it does
Turns a story, requirement, or spec into a set of concrete **Given/When/Then** scenarios that cover the **happy path, the edge cases, and the failure modes** — especially the unhappy paths teams skip. Each scenario traces to the requirement it verifies, and the high-risk ones are flagged to test first. Pairs naturally with `user-stories`/`prd-author` (which derive acceptance tests) by widening coverage to the edges.

## When to use / when NOT to use
- **Use** to derive test coverage from a story/spec, or to pressure-test acceptance criteria for gaps.
- **Do NOT use** to write the story itself (`user-stories`) or author the spec (`prd-author`).

## Inputs
- **Required:** the story / requirement / acceptance criteria.
- **Optional:** known constraints, data shapes, error taxonomy. *Unknown system behaviors are written as open questions, not invented expected results.*

## Method (methodology)
1. **Happy path** — the core success case(s).
2. **Edge cases** — boundaries, empty/zero/max, concurrency, permissions, localization.
3. **Failure modes** — invalid input, timeouts, downstream failure, partial success, idempotency/retry.
4. **Trace** each scenario to the requirement it verifies; mark any expected-result that's an assumption.
5. **Risk-rank** — the scenarios whose failure is most damaging go first.

## Output spec
Scenarios grouped happy / edge / failure, each Given/When/Then + the requirement it verifies + risk flag. Table or list; coverage gaps noted as open questions.

## Sprntly integration (optional)
- **Inputs:** acceptance tests from `prd-author` Part B / `user-stories`; error taxonomy from the codebase.
- **Outputs:** scenarios registered against requirements; coverage gaps raised.
- **Degrades to:** standalone from the story.

## Quality checklist (the bar)
- [ ] Happy, edge, AND failure paths covered — not just the happy path.
- [ ] Each scenario is Given/When/Then and traces to a requirement.
- [ ] Assumed expected-results flagged; unknowns are open questions, not invented.
- [ ] High-risk scenarios flagged to run first.

## Known gaps / limitations
- Scenarios are only a real check when executable; otherwise they're criteria.
- Can't know undocumented system behavior — flags it rather than guessing.

## Worked example
**Input:** "no charge until a boost begins delivering." Scenarios: happy (delivers→charged once); edge (delivery starts then pauses); failure (charged-then-rejected→auto-refund; double-charge under retry→idempotent). Risk-first: the refund + idempotency cases.
