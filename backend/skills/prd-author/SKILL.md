---
name: prd-author
description: Turn a feature idea, request, or problem into a lean, human-readable Product Requirements Document for alignment and decisions — problem, evidence, goals, success metrics, scope/non-goals, scenarios, requirements, risks, and a testable "done when." Use when the user says "write a PRD", "draft a spec", "turn this into requirements", "I need a PRD for X", or describes a feature to build. The PRD intent is the only required input; problem evidence / design system / prototype / codebase are optional grounding that raise fidelity and reduce hallucination. Refuses to start from the solution and never invents a number, business rule, or metric — unknowns are labeled or escalated. Pairs with implementation-spec (this = what & why; that = how it's built, on demand).
---

# PRD Author — the human-readable PRD

## What it does
Produces a **human-readable Product Requirements Document**: problem, evidence, goals, success metrics, scope/non-goals, scenarios, requirements, risks, and a testable "done when." For stakeholder alignment and decisions.

It refuses to start from the solution (forces a real problem and measurable goals first), and it **never invents** a requirement, business rule, or metric — unknowns are labeled, escalated, or routed to research, never guessed.

**The one hard dependency is the PRD intent itself.** Everything else — problem evidence, a design system/Figma, a prototype, the codebase — is *optional grounding*: each artifact present converts a class of assumptions into sourced facts; each absent becomes a clearly-labeled gap, never a fabrication. The skill produces a complete, usable document from the intent alone.

The machine-readable, agent-executable half (EARS requirements, contracts, dependency-ordered tasks, acceptance tests) is **NOT** produced here — it is the separate `implementation-spec` skill, generated on demand from this approved PRD when the work is actually handed to a coding agent.

## When to use / when NOT to use
- **Use** to specify a feature/problem for humans to align on and decide.
- **Do NOT use** to produce the machine-readable build spec (`implementation-spec` — fed this PRD), to *critique* an existing PRD (`prd-critique`), to deep-design system architecture (`tech-spec`), or to cut human tracker tickets (`user-stories`).

## Inputs
- **Required:** a feature idea OR problem (one line is enough).
- **Optional grounding (each raises fidelity, none required):** target user/segment, **problem evidence**, current metric/baseline, **design system / Figma**, **prototype**, **codebase**, constraints (timeline, platform, compliance), the project **constitution** (engineering standards/conventions/prior decisions — in Sprntly from `business-context`/knowledge graph). *Missing optional inputs are derived from the PRD or labeled `[ASSUMPTION]` — never asked of a human mid-run, never invented.*

## Method (methodology)
Grounded in Cagan/SVPG (problem before solution), Amazon Working Backwards, and Shreyas Doshi (pre-mortem + metric guardrails). Keep the body lean — operational detail (A/B mechanics, tech notes, competitor scans, detailed rollout) moves to appendices, present but out of the critical reading path.

1. **De-smuggle the problem.** Restate the request as a problem naming the user, job, and pain — no embedded solution. Surface up front the single interpretation assumption you're making about scope, so it can be corrected before everything downstream inherits it.
2. **Anchor the outcome.** Split a **primary success metric** (baseline + target if known) from **1–2 guardrail metrics** (the things you must not break to move the primary). Give each a formula and an explicit baseline; unknown baseline → `[NEED: …]`. No vanity metrics; don't collapse primary and guardrails into one list.
3. **Scope & non-goals.** What's in v1 + an explicit non-goals list; push extra scope to "later." A PRD with no non-goals is under-scoped.
4. **Users & scenarios.** Who, in what situation, hitting what — concrete, not personas-for-decoration. Include key states (empty / error / edge). Reference, don't pixel-design.
5. **Requirements.** Each functional requirement names its data source, business rule, and exception handling, and links to a signal or carries an explicit `[ASSUMPTION → T0]` / `[ESCALATE]`. **Never invent a business rule the team hasn't confirmed** — mark `[ASSUMPTION]` or open question. Tag the load-bearing branches inline so the downstream spec inherits them: `[edge case]` and `[failure]` where they matter.
6. **Risks + the riskiest assumption.** A short risk list, then **one** named riskiest assumption with a three-line pre-mortem (what we believe / how it fails / what we'd see first if it's wrong). Don't bury it in a table.
7. **Open questions.** Real unknowns with owners, not rhetorical ones; product decisions the PRD can't make are `[ESCALATE]`.
8. **Rollout & measurement.** Phasing (flag / % / cohort), the read-out plan (how and when the §2 metrics are read), and the kill criteria. Detailed schedules and A/B mechanics go to an appendix.
9. **Done-when.** A testable exit condition for the cycle.

## Output spec
A single **lean Markdown** document (no typed `:::` blocks) in the order of `templates/prd-template.md`: a title + one-line summary + an Author/Status/Date line, then **1. Problem & evidence · 2. Goals & metrics** (primary split from guardrails, with baselines) **· 3. Non-goals · 4. Users & scenarios · 5. Requirements · 6. Risks & riskiest assumption** (3-line pre-mortem) **· 7. Open questions** (owners) **· 8. Rollout & measurement · 9. Done-when**. **Requirements go in a single Markdown table** — `ID | Requirement | Priority | Signal/Source | Acceptance (Given/When/Then)` — with `[edge case]` / `[failure]` tags inline on load-bearing rows so the `implementation-spec` skill inherits them (acceptance lives in the table's last column, not a separate section). Keep the body lean; push operational detail to appendices. **Optional Word export** via the `docx` skill.

## Sprntly integration (optional)
- **Inputs from Sprntly:** problem + ranked evidence from the Weekly Brief; the **constitution from `business-context`/knowledge graph** (so the PM agent inherits engineering constraints, never re-asks); DS/Synthesis agents' analysis; the connected codebase/design system/prototype as grounding.
- **Outputs to Sprntly:** the human PRD for approval; metrics registered to the outcome graph; trust-ladder stage = `Alpha (PRD draft)`; `[ESCALATE]` items raised as the only human/agent decisions required. When the approved PRD is handed to a coding agent, the `implementation-spec` skill generates the machine-readable spec from it on demand.
- **Degrades to:** PRD-only — produces a complete document from the idea alone, labeling every optional-artifact gap.

## Quality checklist (the bar)
- [ ] **Works from the intent alone**; fewer artifacts → more labeled assumptions, never more invention.
- [ ] Problem has no smuggled solution; the single scope-interpretation assumption is stated up front.
- [ ] Primary metric is separated from guardrails; each has a formula and a baseline (or `[NEED: …]`) — zero invented numbers.
- [ ] Non-goals present; scope is one release; "done when" is testable.
- [ ] Every requirement links to a signal, decision, or explicit `[ASSUMPTION]`/`[ESCALATE]`; no orphan claims; `[edge case]`/`[failure]` tagged where load-bearing.
- [ ] Exactly one riskiest assumption is named with a three-line pre-mortem.
- [ ] Open questions have owners; product decisions the PRD can't make are `[ESCALATE]`, not guessed.
- [ ] Body is lean; operational detail is in appendices; author's intent, domain richness, and voice are preserved.

## Known gaps / limitations
- Formalizes the input — a wrong PRD yields precise-but-wrong output; the checks catch fabrication and gaps, not bad intent (pair with `prd-critique`/`continuous-discovery` upstream).
- A labeled `[ASSUMPTION]` is only as good as the reviewer who confirms it; the PRD surfaces gaps, it doesn't resolve them.
- "Done when" is only a real check where the exit condition is observable; otherwise it's an aspiration.

## Worked example
**Input:** "We keep losing enterprise accounts; build better collaboration." (intent only — no design system, no codebase.)
**Output (abridged):**
- **Problem:** >50-seat accounts churn; exit surveys cite no real-time co-edit `[ASSUMPTION: primary driver — confirm vs churn data]`.
- **Goals & metrics:** primary = logo churn 4.2%→3.4%/qtr; guardrail = p95 doc-load latency (must not regress).
- **Non-goals:** offline, permissions redesign.
- **Requirements:** "two users edit the same doc → non-conflicting edits merge, overlaps surface a conflict UI" `[edge case]`; "realtime channel drops → queue local edits, reconcile on reconnect" `[failure]`.
- **Riskiest assumption:** real-time co-edit is the churn driver — *we believe* exit surveys; *it fails* if churn is really about price; *first signal* would be pilots citing cost, not collaboration.
- **Open questions:** `[ESCALATE]` which document types ship in v1 — a product decision.
- **Done-when:** 2 design partners run a 2-wk pilot and cite it for a renewal.
