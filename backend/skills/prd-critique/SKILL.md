---
name: prd-critique
description: Red-team and improve an existing PRD or product spec. Use when the user says "review this PRD", "critique this spec", "what's wrong with this doc", "make this PRD better", or pastes a PRD and wants feedback. Flags solution-smuggling, weak/absent metrics, overscoping, missing non-goals, and delivery risk, then returns prioritized fixes.
---

# PRD Critique

## What it does
Takes a finished or draft PRD and reviews it the way a demanding CPO would in a doc review: it finds the load-bearing weaknesses (not cosmetic ones), explains *why* each is a problem, and gives a concrete rewrite or question for each. It ends with a go / revise / rework verdict.

## When to use / when NOT to use
- **Use** to evaluate or harden an existing PRD/spec before review or build.
- **Do NOT use** to write a PRD from scratch (`prd-author`) or to produce tickets (`user-stories`).

## Inputs
- **Required:** the PRD/spec text.
- **Optional:** the product's North Star / current goals, known constraints, the audience for the doc. *If missing, critique against general senior-PM standards and note where company context would sharpen the review.*

## Method (methodology)
Combines a deterministic lint pass with judgment-level critique. Inspired by Digidai's PRD-critique patterns (solution-smuggling, weak metrics, overscoping, delivery risk) and Shreyas Doshi's "what would make this fail review."
1. **Deterministic lint.** Run `scripts/prd_lint.py` (or apply its checklist) to flag structural gaps: missing problem statement, no baseline on the primary metric, no guardrail, no non-goals, no "done when", solution language in the problem section.
2. **Solution-smuggling check.** Does the "problem" actually describe a feature? Rewrite it as a true problem.
3. **Metric integrity.** Is the primary metric outcome (not output)? Baseline + target present? Guardrails named? Vanity metrics flagged.
4. **Scope discipline.** Is this one release or three? Identify the thinnest valuable slice; recommend what to cut to "later".
5. **Delivery & risk.** Unstated dependencies, edge/error states, riskiest assumption, and how it'll be tested.
6. **Prioritize fixes.** Sort findings: 🔴 blockers → 🟡 should-fix → ⚪ nice-to-have. Each finding = problem · why it matters · fix.
7. **Verdict:** Ship-ready / Revise / Rework, with the 1–3 changes that would most raise the bar.

## Output spec
1) One-paragraph overall read. 2) Lint results (pass/fail per check). 3) Prioritized findings (🔴/🟡/⚪) each with *problem → why → fix*. 4) The 3 highest-leverage rewrites. 5) Verdict.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the company North Star + active goals (to test strategic fit), and the originating Monday-Brief finding + confidence (to test whether the PRD actually addresses the ranked problem).
- **Outputs to Sprntly:** a structured review object (findings + severity), and a recommended trust-ladder transition (`Alpha → Beta`) only if no 🔴 blockers remain.
- **Degrades to:** with no Sprntly context, review against general senior-PM standards; ask for the North Star only if strategic-fit is in question.

## Quality checklist (the bar)
- [ ] Every finding names the specific line/section, not a vague "be clearer".
- [ ] Each finding has a concrete fix or a sharp question — never just criticism.
- [ ] Findings are prioritized; the user knows what to fix first.
- [ ] Tone is direct and useful, not harsh; it strengthens the author.

## Known gaps / limitations
- Reviews what's on the page; it can't tell whether the underlying problem is real or the metric is the *right* one for the business — only whether the doc is internally sound.
- Without company goals, strategic-fit critique is generic.
- The linter catches structure, not correctness of domain logic.

## Worked example
**Input:** a PRD whose problem reads "users need a notification preferences center," metric "increase engagement," scope covering channels + digests + quiet hours + admin rules + migration.

**Output (abridged):**
- 🔴 **Solution-smuggled problem.** "Users need a preferences center" is a feature. Rewrite: "Users disable notifications entirely because they can't control frequency, costing re-engagement." 
- 🔴 **Metric not measurable.** "Increase engagement" has no baseline/target/guardrail. Propose: reduce notification-driven unsubscribes from X→Y; guardrail = no drop in DAU.
- 🟡 **Overscoped.** Five capabilities = multiple releases. Thinnest slice: account-level opt-out + email frequency. Push admin rules + migration to later.
- **Verdict: Rework** — fix problem + metric before this goes to build.
