---
name: prd-critique
description: Red-team and improve a PRD or spec from multiple stakeholder lenses — leadership, engineering, design, data science, plus QA/security as relevant — then run an adjudication loop where each critique is assessed for whether it actually applies to THIS problem and product before it's accepted-and-fixed or rebutted-with-reason. Use when the user says "review this PRD", "critique this spec", "what's wrong with this doc", "make this PRD better", "stakeholder review", or pastes a PRD and wants feedback. The goal is NOT a list of 30 things — it is the vital few issues that move a PRD from ~60% to ~92%, while protecting the doc's intent, richness, and voice (never flattening it into generic boilerplate). Combines a deterministic lint with role-based judgment; never takes a critique at face value; returns the top issues, the author's accept/rebut response to each, and a go / revise / rework verdict.
---

# PRD Critique

## What it does
Reviews a PRD the way a real cross-functional doc review does — from the distinct lenses of **leadership, engineering, design, and data science** (adding QA, security, or others when the doc warrants) — surfaces the load-bearing weaknesses each role would raise, then runs an **adjudication loop**: every critique is tested for whether it genuinely applies to *this* problem and product, and is either **accepted (with the concrete change)** or **rebutted (with the reason it doesn't apply)**. It does not take critiques at face value, and it does not rubber-stamp the doc either.

**Two rules define the job.** First, it surfaces only the **vital few** — the handful of issues that actually move this PRD from ~60% to ~92%, not an exhaustive lint of everything imperfect. A 20-finding review has failed even if every finding is true: it buries what matters and exhausts the author. Second, it **protects the PRD's intent, richness, and voice** — the bold bet, the specific insight, the opinionated framing are the *value* of the doc; the critique sharpens them, it never sands them into safe, generic mush. It ends with a go / revise / rework verdict and the few changes that matter most.

## When to use / when NOT to use
- **Use** to harden an existing PRD/spec before review or build, especially when multiple functions must sign off.
- **Do NOT use** to write a PRD from scratch (`prd-author`) or produce tickets (`user-stories`).

## Inputs
- **Required:** the PRD/spec text.
- **Optional (sharpen the lenses):** the North Star / current goals (leadership lens), tech constraints/stack (engineering), design system/user context (design), instrumentation/metric definitions (data science), known constraints, the doc's audience. *If missing, critique against general senior standards for each lens and note where role context would sharpen it.*

## Method (methodology)
A deterministic lint, then multi-lens judgment critique, then adjudication — drawing on established review patterns — the "same doc, different readers" multi-audience idea, multi-persona reviewer teams, and the peer-review reviewer→rebuttal→meta-review loop. The reviewer is requirements-aware and **corroborates each finding against the doc before it stands.**
1. **Deterministic lint.** Run `scripts/prd_lint.py` (or its checklist): missing problem statement, no baseline on the primary metric, no guardrail, no non-goals, no "done when", solution language in the problem section. These are structural facts, not opinions.
2. **Multi-lens critique.** Review from each stakeholder lens; each is *objective and scoped to what that role owns* — not personality cosplay:
   | Lens | Asks (objectively) |
   |---|---|
   | **Leadership / CPO** | Does this serve the strategy? What's the bet? Is success defined in numbers (baseline→target)? Is scope a real choice? |
   | **Engineering** | What exactly is built? Constraints, dependencies, every state (happy/empty/error/loading)? Is it one release or three? Riskiest technical assumption? |
   | **Design** | Who is the user in this moment and what are they feeling? Where are the decision points? Accessibility, edge states, the unhappy path? |
   | **Data science** | Is the metric an outcome (not output)? Is it instrumented and attributable? Baseline real or assumed? How will we know it worked — and is the experiment/readout feasible? |
   | *(QA / Security / Legal — add when the doc touches data, billing, auth, migration, or regulated content.)* |
3. **Validity gate (assess before accepting — the core rule).** For *each* finding, test it against THIS problem/product: is it real here, or generic boilerplate that doesn't apply? Corroborate against the doc. A critique that doesn't survive this gate is dropped or downgraded — **critiques are not taken at face value.**
4. **Adjudication — accept or rebut.** Every surviving finding gets an author-style response: **ACCEPT** (state the concrete change to make) or **REBUT** (state why it doesn't apply to this problem/product/stage, with the reasoning). Where a lens disagrees with another (e.g. engineering "cut scope" vs. design "this state is essential"), surface the tension and resolve it.
5. **Select the vital few (the 60→92 filter).** From everything the lenses raised, keep ONLY the issues that materially move the PRD's quality and odds of success — typically **3–6, rarely more**. For each candidate ask: "if this stayed unfixed, would the PRD meaningfully fail or mislead the build?" If no, it's noise — cut it (a one-line "minor nits" footnote at most). Rank the survivors 🔴 blocker → 🟡 should-fix. Do not pad the list to look thorough.
6. **Protect intent & richness (guard against flattening).** Before finalizing, check that no accepted change would strip the PRD's core bet, specific insight, opinionated framing, or voice. Critique sharpens the intent; it never replaces a bold, specific doc with a safe, generic one. If a "fix" would dull the doc, reframe it as a sharpening, or rebut it. Vague/hedged is not the goal — clear-and-bold-but-sound is.
7. **Verdict:** Ship-ready / Revise / Rework, with the 1–3 changes that most raise the bar, and the updated doc sections if asked.

## Output spec
1) **One-paragraph overall read** — where it is now (~X%) and what gets it to ~92%.
2) **What's strong — keep it.** Name the doc's intent, bet, and best instincts explicitly so the author (and any reader) knows what NOT to lose. This is not flattery; it's protecting the richness.
3) **Lint results** (pass/fail per check).
4) **The vital few (3–6 issues, not 30)** — each: which lens raised it · problem → why it matters → fix, ranked 🔴/🟡. The issues that move 60→92, nothing padded.
5) **Adjudication** — each of the vital few marked **ACCEPT (change)** or **REBUT (reason)** after the validity gate; cross-lens tensions resolved.
6) **Minor nits (optional, one line).** Everything below the bar, named in a single sentence so it's acknowledged but not dwelt on.
7) **Verdict** + the 1–3 highest-leverage rewrites (phrased as sharpenings that preserve intent). Narrative-led; a small table for adjudication only.

## Sprntly integration (optional)
- **Inputs from Sprntly:** North Star + active goals (leadership lens), the originating signal + confidence (does the PRD address the ranked problem?), instrumentation schema (data-science lens), codebase/stack (engineering lens).
- **Outputs to Sprntly:** a structured review object (findings + lens + accept/rebut + severity); a recommended trust-ladder transition (`Alpha → Beta`) only if no 🔴 blockers survive adjudication.
- **Degrades to:** with no Sprntly context, review against general senior standards per lens; ask for the North Star only if strategic-fit is in question.

## Quality checklist (the bar)
- [ ] **The vital few, not the exhaustive list** — typically 3–6 issues that move 60→92%; anything that wouldn't materially change quality or outcome is cut to a one-line nit, not promoted to a finding.
- [ ] **The PRD's intent, richness, and voice are protected** — what's strong is named and kept; no accepted change flattens the bet/insight/framing into generic mush.
- [ ] Reviewed from **all relevant lenses** (leadership, engineering, design, data science; + QA/security where warranted) — each objective and scoped to what the role owns, not caricature.
- [ ] **Every critique passed a validity gate** — tested against THIS problem/product; generic or non-applicable critiques dropped or downgraded, not asserted.
- [ ] **Every surviving finding is adjudicated** — explicitly ACCEPTED (with the change) or REBUTTED (with the reason) — never left as floating criticism.
- [ ] Cross-lens disagreements surfaced and resolved, not averaged away.
- [ ] Every finding names the specific section; each has a concrete fix or sharp question; findings prioritized.
- [ ] Tone is direct and useful, strengthens the author; no self-abasement, no harshness.

## Known gaps / limitations
- Reviews what's on the page; it can't confirm the underlying problem is real or the metric is the *right* one without company context — only whether the doc is internally sound and survives each lens.
- The lenses are structured role perspectives, not real stakeholders — used to *find* the vital few, not to generate one finding per role; they don't replace actual sign-off.
- The linter catches structure, not correctness of domain logic.

## Worked example
**Input:** a PRD whose problem reads "users need a notification preferences center," metric "increase engagement," scope covering channels + digests + quiet hours + admin rules + migration.
**Output (abridged):**
- **Lint:** 🔴 no non-goals; ⚠️ solution-smuggled problem, no baseline, no guardrail, no "done when."
- **Leadership:** "increase engagement" isn't a success metric → ACCEPT: reduce notification-driven unsubscribes X→Y, guardrail no DAU drop.
- **Engineering:** five capabilities = 3+ releases; migration is its own project → ACCEPT: thin slice = account-level opt-out + email frequency; rest to Later/non-goals.
- **Design:** "preferences center" hides the user's actual moment (overwhelmed, about to mute) → ACCEPT: reframe around the mute decision point.
- **Data science:** is "engagement" instrumented and attributable to this change? → ACCEPT: define the event + attribution before build.
- **Rebut example:** engineering flags "no SSO/admin controls" → REBUT: out of scope for a v1 consumer opt-out; not applicable at this stage.
- **Verdict: Rework** — fix problem + metric + scope (all accepted, validity-gated) before build.
