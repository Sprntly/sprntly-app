---
name: prd-author
description: Turn a feature idea, request, or problem into a single two-part document — Part A a human-readable PRD (for alignment and decisions), a horizontal rule, then Part B an LLM-readable Implementation Spec a coding agent builds and tests against without ambiguity. Use when the user says "write a PRD", "draft a spec", "turn this into requirements", "I need a PRD for X", "agent-ready PRD", "PRD with an implementation spec", or describes a feature to build. The PRD is the only required input; design system / prototype / problem evidence / codebase are optional grounding that raise fidelity and reduce hallucination. Produces problem-goals-scope-metrics (human) + EARS requirements, design/contracts, dependency-ordered tasks, with an independent no-hallucination check.
---

# PRD Author — human PRD + LLM-readable Implementation Spec

## What it does
Produces ONE document in two parts:
- **Part A — Product Requirements Document (human-readable):** problem, evidence, goals, success metrics, scope/non-goals, scenarios, risks, "done when." For stakeholder alignment and decisions.
- *(a horizontal rule separates the two)*
- **Part B — Implementation Spec (LLM-readable / agent-executable):** the Spec-Driven-Development artifact a coding agent implements and tests against — EARS requirements, design/contracts, dependency-ordered tasks — generated and self-checked autonomously.

It refuses to start from the solution (forces a real problem and measurable goals first), and it **never invents** a requirement, business rule, contract, or metric — unknowns are labeled, escalated, or routed to the agent's research gate, never guessed.

**The one hard dependency is the PRD intent itself.** Everything else — a design system/Figma, a prototype, problem evidence, the codebase — is *optional grounding*: each artifact present converts a class of assumptions into sourced facts; each absent becomes a clearly-labeled gap, never a fabrication. The skill produces a complete, usable document from the PRD alone. (Naming: the human half is the *PRD*; the machine half is the *spec* — this is the standard Spec-Driven-Development split.)

## When to use / when NOT to use
- **Use** to specify a feature/problem for both humans and a coding agent in one artifact.
- **Do NOT use** to *critique* an existing PRD (`prd-critique`), to deep-design system architecture (`tech-spec` — Part B *references* contracts, that skill *designs* them), or to cut human tracker tickets (`user-stories`).

## Inputs
- **Required:** a feature idea OR problem (one line is enough).
- **Optional grounding (each raises Part B fidelity, none required):** target user/segment, **problem evidence**, current metric/baseline, **design system / Figma**, **prototype**, **codebase**, constraints (timeline, platform, compliance), the project **constitution** (engineering standards/conventions/prior decisions — in Sprntly from `business-context`/knowledge graph). *Missing optional inputs are derived from the PRD or labeled `[ASSUMPTION]` — never asked of a human mid-run, never invented.*

## Method (methodology)
Part A grounded in Cagan/SVPG (problem before solution), Amazon Working Backwards, Shreyas Doshi (pre-mortem + metric guardrails). Part B implements Spec-Driven Development (Kiro 3-artifact + EARS, Spec-Kit constitution + self-check, BMAD structured handoff), with independent verification.

### Part A — the PRD (human-readable)
1. **De-smuggle the problem.** Restate the request as a problem naming the user, job, and pain — no embedded solution.
2. **Anchor the outcome.** 1 primary success metric (baseline + target if known) + 1–2 guardrails. No vanity metrics.
3. **Scope & non-goals.** What's in v1 + an explicit non-goals list; push extra scope to "later."
4. **Experience.** Key flows + states (empty/error/edge); reference, don't pixel-design.
5. **Three-dimension requirement check.** For each functional requirement verify data source, business rule, and exception handling. **Never invent a business rule the team hasn't confirmed** — mark `[ASSUMPTION]` or open question.
6. **Risk.** A 3-line pre-mortem + the riskiest assumption + cheapest de-risk.
7. **Done.** A testable "done when."

### Part B — the Implementation Spec (LLM-readable), generated autonomously
8. **Inventory available artifacts & set grounding.** Record which inputs are present (PRD required; evidence/design system/prototype/codebase optional). Each section below grounds against the relevant artifact if present, or labels the gap if absent. **Fewer artifacts → more labeled assumptions, never more invention.**
9. **Classify stakes → set the autonomy gate.** Reversible / low-blast-radius work → fully autonomous, no human gate. Irreversible / data-loss / billing / security / migration work → insert ONE explicit human checkpoint before implementation. "No human input" means *no unnecessary* input, not *never*.
10. **Constitution.** State the project-wide engineering constraints (stack, conventions, security/compliance, architecture, prior decisions) from context; if absent, state minimal assumed ones as `[ASSUMPTION]`. These bound every requirement so the agent never re-asks.
11. **Requirements in EARS.** Convert each PRD goal into testable criteria: `WHEN <event> THE SYSTEM SHALL …` / `WHILE <state> …` / `IF <condition> THEN …` / `WHERE <feature> …` / ubiquitous `THE SYSTEM SHALL …`. **Each must cover happy path, edge cases, AND failure modes**, trace to a Part A goal, and bind to a grounding artifact (or be labeled `[ASSUMPTION]`).
12. **Design & contracts.** Inputs/outputs, pre/postconditions, invariants, interface/API contracts, data models, state machines, constraints. **Facts/contracts must bind verbatim** to the codebase/design system/PRD (the field/API must literally appear in the source) — anything not literally supported is `[ASSUMPTION → research]` or `[ESCALATE]`, never invented.
13. **Out of scope / not constrained / unresolved.** Explicitly enumerate what the spec does NOT constrain (the silent gaps that break agents), and split unknowns into **`[ASSUMPTION → T0 research]`** (answerable by reading code) vs. **`[ESCALATE]`** (a product/external decision a research step CANNOT settle — these are the only places a human/another agent is genuinely required).
14. **Cross-cutting checklist — address or explicitly waive each:** auth/permissions, idempotency, error taxonomy, observability/logging, migration/rollback, performance budget, concurrency. Forces the silent 40% to be named.
15. **Tasks (dependency-ordered).** Atomic, individually verifiable; `[P]` marks parallelizable; **T0 is a mandatory research-before-coding gate**; each task maps to the requirement it satisfies.
16. **Acceptance & Definition of Done (merged).** For each requirement, write a spec-first acceptance test (Given/When/Then) *derived before implementation*, so the coding agent implements to pass tests it did not author — including the IF/failure branches. Then state ONE Definition of Done that combines them with the non-behavioral gates: **done = every acceptance test passes AND every requirement has a passing test AND no `[ESCALATE]` item is open AND the cross-cutting checklist is addressed/waived AND independent verification passes.** Tests answer "is each behavior right?"; the DoD answers "is the whole thing finished?" — one section, not two.
17. **Independent verification pass (replaces the human approval gate — and is NOT self-grading).** Run a separate, adversarial check (different framing than the generator): find any claim no provided artifact supports; find unstated assumptions; surface cross-artifact conflicts; confirm verbatim binding for every fact/contract; confirm every requirement traces to Part A and every requirement has a verifying task/test. Report the result. *This separation — checker ≠ generator — is the actual anti-hallucination guarantee; "the spec looks grounded" is not.*

## Output spec
A single document: **Part A (PRD)** → `---` (horizontal rule) → **Part B (Implementation Spec)** with sections: Available artifacts & grounding · Stakes & autonomy gate · Constitution · Requirements (EARS, traced + bound) · Design & contracts · Out-of-scope/not-constrained/unresolved (`[ASSUMPTION → T0]` vs `[ESCALATE]`) · Cross-cutting checklist · Tasks (dependency-ordered, `[P]`) · Acceptance tests & Definition of Done (merged: Given/When/Then per requirement + the single done-gate) · Independent verification report. Use `templates/prd-template.md`. **Optional Word export** via the `docx` skill.

## Sprntly integration (optional)
- **Inputs from Sprntly:** problem + ranked evidence from the Monday Brief; the **constitution from `business-context`/knowledge graph** (so the PM agent inherits engineering constraints, never re-asks); DS/Synthesis agents' analysis; the connected codebase/design system/prototype as grounding for Part B.
- **Outputs to Sprntly:** Part A for human approval; Part B handed to the coding agent (Claude Code/Cursor); metrics + contracts registered to the outcome graph; trust-ladder stage = `Alpha (PRD draft)` for Part A, `Beta (autonomous spec, self-verified)` for Part B; `[ESCALATE]` items raised as the only human/agent decisions required.
- **Degrades to:** PRD-only — produces both parts from the idea alone, labeling every optional-artifact gap.

## Quality checklist (the bar)
- [ ] **Works from the PRD alone**; fewer artifacts → more labeled assumptions, never more invention.
- [ ] Part A problem has no smuggled solution; primary metric has a baseline or `[ASSUMPTION]` + a guardrail; non-goals present; scope is one release; "done when" is testable.
- [ ] The two parts are separated by a horizontal rule; Part B is clearly the LLM-readable **Implementation Spec**.
- [ ] Every Part B requirement is **EARS**, covers happy/edge/failure, **traces to Part A**, and **binds to an artifact or is labeled**.
- [ ] Contracts **bind verbatim** to a source; nothing invented; unknowns split into `[ASSUMPTION → T0]` vs `[ESCALATE]`.
- [ ] Cross-cutting checklist addressed or explicitly waived; out-of-scope/not-constrained stated.
- [ ] Tasks dependency-ordered with a research-first T0.
- [ ] **Acceptance tests + Definition of Done present and merged** — a Given/When/Then test per requirement (incl. failure branches) derived before implementation, plus ONE done-gate (all tests pass + no open `[ESCALATE]` + cross-cutting addressed + verification passes).
- [ ] **Independent verification pass run by a checker separate from the generator** (not self-grading); stakes-gate set (one human checkpoint for irreversible work).

## Known gaps / limitations
- Formalizes the input — a wrong PRD yields precise-but-wrong output; the checks catch fabrication and gaps, not bad intent (pair with `prd-critique`/`continuous-discovery` upstream).
- Verbatim binding catches invented facts, not facts faithfully drawn from a wrong source — source quality still matters.
- EARS removes ambiguity in stated behavior, not in unstated assumptions — hence the explicit "not constrained" section; it can't enumerate every unknown.
- `[ASSUMPTION → T0]` assumes the codebase answers the question; genuinely undecidable items must be `[ESCALATE]`, not auto-resolved.
- Acceptance tests are only a real check where executable; otherwise they're criteria, weaker than running code — and the Definition of Done is only as strong as those tests plus the gates it references.

## Worked example
**Input:** "We keep losing enterprise accounts; build better collaboration." (PRD only — no design system, no codebase.)
**Output (abridged):**
- **Part A:** Problem = >50-seat accounts churn; exit surveys cite no real-time co-edit `[ASSUMPTION: primary driver — confirm vs churn data]`. Primary metric: logo churn 4.2%→3.4%/qtr; guardrail: p95 doc-load latency. Non-goals: offline, permissions redesign. Done-when: 2 design partners run a 2-wk pilot and cite it for a renewal.
- `---`
- **Part B — Implementation Spec:** *Artifacts:* PRD only → UI/contract details labeled, routed to research/escalate. *Stakes:* reversible (flag-gated) → autonomous. *EARS:* "WHILE two users edit the same doc THE SYSTEM SHALL merge non-conflicting edits and surface a conflict UI for overlaps." / "IF the realtime channel drops THEN THE SYSTEM SHALL queue local edits and reconcile on reconnect." *Contracts:* realtime transport + conflict-resolution API `[ASSUMPTION → T0: no codebase]`. *Unresolved:* `[ESCALATE] which document types are in v1 — product decision`. *Cross-cutting:* auth (doc-level perms), concurrency (CRDT vs OT — `[ESCALATE]`), observability (edit-latency metric). *Tasks:* T0 research transport/codebase → T1 presence `[P]` → T2 edit-merge → T3 conflict UI (needs T2) → T4 reconnect-reconcile. *Acceptance & Done:* a Given/When/Then test per requirement incl. the IF-failure branches, and **Done = all tests pass + the `[ESCALATE]` doc-type decision resolved + cross-cutting addressed + verifier passes.** *Independent verification:* all requirements trace to Part A ✓; 0 invented contracts (2 labeled) ✓; 1 `[ESCALATE]` flagged ✓ — PASS, with the doc-type decision as the only required human input.
