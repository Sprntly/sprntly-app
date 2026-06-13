---
name: okr-nct
description: Set quality goals as OKRs or Reforge NCTs with guardrails. Use when the user says "write OKRs", "set goals", "NCTs", "objectives and key results", or "our goals are just a task list". Produces objectives with measurable, outcome-based key results (or Narrative/Commitments/Tasks), checks for common goal-quality failures, and adds guardrail metrics.
---

# OKRs / NCTs

## What it does
Turns intent into well-formed goals — either OKRs (Objective + measurable Key Results) or Reforge-style NCTs (Narrative, Commitments, Tasks) — and audits them for the classic failures: KRs that are tasks, sandbagged or unmeasurable targets, and goals with no guardrail (so the team games one metric while wrecking another).

## When to use / when NOT to use
- **Use** to set or fix quarterly/annual goals.
- **Do NOT use** to build the roadmap (`roadmap`) or strategy (`product-strategy-stack`).

## Inputs
- **Required:** the outcome you want over the period.
- **Optional:** baselines, the strategy goals these ladder to, format preference (OKR vs NCT). *If missing, ask for baselines on the key metric; assume OKR unless told.*

## Method (methodology)
OKR best practice (Doerr) + Reforge NCT + goal-quality audit.
1. **Objective** — qualitative, ambitious, time-boxed, meaningful.
2. **Key Results** — outcome metrics with baseline to target; 2-4 per objective. Reject KRs that are tasks ("ship X").
3. **Guardrails** — metrics that must NOT regress while chasing the KRs.
4. **NCT alternative** — if commitments matter more than metric targets, frame as Narrative + Commitments (binary outcomes) + Tasks.
5. **Quality audit** — measurable? outcome not output? ambitious-but-real? laddered to strategy? gameable?
6. **Cadence** — how/when progress is checked.

## Output spec
Objective(s) - 2-4 outcome KRs with baseline to target (or NCT structure) - guardrail metrics - goal-quality audit notes - check-in cadence.

## Sprntly integration (optional)
- **Inputs from Sprntly:** baselines from the outcome graph (real numbers, not guesses); strategy goals to ladder to.
- **Outputs to Sprntly:** KRs registered as tracked metrics; guardrails monitored; progress auto-readable from the outcome graph.
- **Degrades to:** standalone; ask for baselines.

## Quality checklist (the bar)
- [ ] KRs are outcome metrics with baseline to target, not tasks.
- [ ] Each goal has at least one guardrail.
- [ ] Targets are ambitious but not fantasy or sandbagged.
- [ ] Goals ladder to strategy and aren't trivially gameable.

## Known gaps / limitations
- Without baselines, targets are guesses - flagged, not hidden.
- OKRs can drive metric-gaming; the guardrail step is the main defense but culture matters more than format.

## Worked example
**Input:** "Goal: improve activation this quarter."
**Output (abridged):** Objective: New users reach their first win fast and reliably. KR1: activation rate 38% to 48%. KR2: median time-to-first-win 3d to 1d. Guardrail: 30-day retention must not drop; support tickets/new-user flat. Audit flag: original draft "ship onboarding revamp" was a task - replaced with the two outcome KRs.
