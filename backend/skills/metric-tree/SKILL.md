---
name: metric-tree
description: Build a complete top-down metric system for a product — North Star → 3–6 supporting metrics → a driver tree down to instrumented base units, plus guardrails and health metrics, every node classified and grounded in the actual business. Use when the user says "metric tree", "build our metrics", "North Star and supporting metrics", "what should we measure", "driver tree", "decompose the North Star", "metric framework", or onboards a product that needs its measurement system. Reasons from the business model, users, product, and (if available) the codebase and designs to derive which metrics matter, not generic ones.
---

# Metric Tree (the metric system)

## What it does
Builds a product's **metric system top-down**, the way a senior data scientist would: start from the **North Star** (the one metric capturing customer value), reason from the **business model, user segments, product, and business context** to derive the **3–6 supporting metrics** that actually drive it, then decompose each into a **driver tree** down to base units a team can move or that are instrumented. It adds the two layers most teams forget — **guardrails** (what must not degrade) and **health metrics** (operational vitals) — and **classifies every node** so it's clear what's the North Star vs. supporting vs. driver vs. guardrail vs. health. If a **codebase or designs** are available, it reads them to ground leaves in what's actually instrumented and where the real value/drop-off moments are.

This skill defines the North Star *and* the full system beneath it (it absorbs the older standalone North-Star definition): one place to go from "what's our one metric?" to "and here's the whole tree that ladders to it."

## When to use / when NOT to use
- **Use** to design or audit a product's measurement system — at onboarding, before goal-setting, or when metrics feel like a disconnected pile.
- **Do NOT use** to set period targets/commitments (`okr-nct` — that consumes this), diagnose SaaS health from existing numbers (`saas-metrics-diagnosis`), or analyze one funnel in depth (`funnel-activation`).

## Inputs
- **Required:** the product (and the North Star if it's already chosen — e.g. a Sprntly onboarding where the NS is given).
- **Strongly recommended (this is what makes it non-generic):** the **business model** (how value & money flow), **user segments** (who does what), the **product's value moments**, and the **business context** (in Sprntly, from `business-context`). The supporting metrics are *derived from these* — a marketplace, a SaaS tool, and an ads business get different trees.
- **Optional, high-value if present:** access to the **codebase** (to read what events/tables are actually instrumented, so leaves map to real data) and **designs/flows** (to locate value moments and drop-off points). *If absent, build the system from the business reasoning and flag leaves as "to instrument."*
- *If a required input is missing, derive it from what's known and label the assumption; never invent a value.*

## Method (methodology)
North Star framework (Amplitude/Reforge) + business-grounded input derivation + driver-tree decomposition + a guardrail/health layer + node classification. **Build top-down.**

1. **Establish the North Star (L0).** If given, validate it against the value-capture test (does it rise *only* when customers get real value? is it a leading indicator of business success, not just current revenue?) and keep or sharpen it. If not given, derive it from the core value exchange. Exactly **one** NS.
2. **Read the business to derive the supporting metrics — don't guess them.** Look at: the **business model** (what drives value and money — e.g. marketplace → supply, demand, match/liquidity; subscription SaaS → acquisition, activation, engagement, retention, expansion; ads → audience, engagement, ad load, price), the **user segments** (whose behavior matters, buyer vs. user), and the **product's value moments**. From these, derive the **3–6 supporting metrics (L1)** that are the real levers on the NS — the smallest set that, if all moved, would move the NS. State *why each was chosen* from the business, and mark each as a **leading input** (a lever) vs. a **lagging output** (a result).
3. **Decompose each supporting metric into a driver tree (L2…Ln)** using the real arithmetic relationship at each branch (×, +, rate-of). **Stopping rule:** stop a branch when it reaches either (a) a metric a single team can directly act on, or (b) an instrumented event/base unit. This keeps the tree bounded — supporting metrics are 3–6, but the full tree can run deeper.
4. **Ground in code/design if available.** With a **codebase**: read it to find the actual instrumented events, tables, and flags, and map leaves to them (flag any leaf with no backing event as "to instrument"). With **designs**: trace the user flow to locate the value moments and the steps where activation/drop-off happen, and make those explicit nodes. This turns an aspirational tree into a buildable one.
5. **Add guardrails.** The metrics that must **not** degrade while chasing the NS — quality, trust/safety, unit economics, latency, satisfaction — chosen for *this* product. A guardrail is a "do no harm" boundary, not a goal to maximize.
6. **Add health metrics.** Operational vitals that sit outside the value chain but signal the system is sound — reliability/uptime, error rate, performance, support-ticket load, data freshness, cost-to-serve. These don't ladder to the NS; they protect the machine that produces it.
7. **Classify and assign every node.** Tag each: **[NS]** · **[L1-supporting]** · **[driver]** · **[guardrail]** · **[health]**; plus the operator on its edge, an **owner** (team/lever), and leading-vs-lagging. Then **find leverage** — the node where a realistic % change moves the NS most (sensitivity), precise if values exist, directional if not.

## Output spec
A complete, classified metric system (use `templates/metric-system-template.md`):
1. **North Star** — the metric + why it captures value (value-capture & leading-indicator rationale).
2. **Supporting metrics (3–6)** — each with *why it was derived from the business*, its operator-link to the NS, owner, and leading/lagging tag.
3. **Driver tree** — the decomposition beneath each supporting metric (indented or mermaid), edges labeled with operators, leaves as team-controllable levers or instrumented events, bottoming out per the stopping rule.
4. **Guardrails** — the do-not-degrade metrics for this product.
5. **Health metrics** — the operational vitals.
6. **Node legend & leverage** — every node tagged [NS]/[L1-supporting]/[driver]/[guardrail]/[health] with owner; the highest-leverage node(s) called out.
Where code/design was read, note which leaves are backed by real instrumentation vs. "to instrument."

## Sprntly integration (optional)
- **Inputs from Sprntly:** the North Star + goal from `business-context`; the business model, segments, and value moments from the business context; live node values from the outcome graph (so the tree is quantified and sensitivity is precise); the customer's codebase/designs where connected, to ground leaves in real events and flows.
- **Outputs to Sprntly:** the metric system becomes the measurement spine of the outcome graph — every initiative maps to the node it should move; supporting metrics feed `prioritize` (goal mode) and `okr-nct`; guardrails register as monitored do-not-degrade conditions; "to instrument" leaves become an instrumentation backlog (hand to `analytics-instrumentation`).
- **Degrades to:** standalone — build the system from the product + business reasoning, leaving nodes unvalued and leaves flagged "to instrument."

## Quality checklist (the bar)
- [ ] **Built top-down** — North Star first, then supporting metrics derived *from the business* (model/users/product), then the tree — not assembled upward from whatever is measurable.
- [ ] **Exactly one North Star**; it passes the value-capture and leading-indicator tests.
- [ ] **3–6 supporting metrics**, each justified from the business (not generic), tagged leading vs. lagging.
- [ ] Every branch uses the correct arithmetic operator; the tree bottoms out at controllable levers or instrumented events (stopping rule applied).
- [ ] **Guardrails and health metrics are present and distinct** from value metrics — guardrails = do-not-degrade, health = operational vitals.
- [ ] **Every node is classified** ([NS]/[L1-supporting]/[driver]/[guardrail]/[health]) and has an owner; the highest-leverage node is identified.
- [ ] Where code/design was available, leaves are mapped to real instrumentation (or flagged "to instrument"); nothing is invented.

## Known gaps / limitations
- The system is only as good as the business reasoning behind the supporting metrics — a wrong read of the model yields a confident-but-wrong tree; ground it in `business-context` and validate against data where possible.
- Trees model arithmetic, not causation — a lever may correlate without causing; flag where a branch is correlational so it isn't over-trusted.
- Sensitivity/leverage is directional without real node values; precise only with data.
- Reading a codebase reveals what's instrumented, not whether the events are *correct*; treat instrumentation as a lead to verify, and pair with `analytics-instrumentation` to close gaps.

## Worked example
**Input:** "Sprntly onboarding for a B2B SaaS dev-tool. North Star is already given: weekly active teams that ship a change through our loop. Business context available; codebase connected."
**Output (abridged):**
- **North Star [NS]:** weekly active teams shipping a change through the loop — passes value-capture (rises only when teams get real value: shipped work) and is a leading indicator of retention/expansion.
- **Supporting metrics [L1], derived from the business (B2B SaaS dev-tool, team-seat model, value = shipped outcomes):**
  1. *Activated teams* (reached first shipped change) — leading. Owner: onboarding.
  2. *Weekly loop completion rate* (briefs → shipped) — leading. Owner: core product.
  3. *Depth: changes shipped per active team* — leading. Owner: core product.
  4. *Team retention (week-4)* — lagging. Owner: lifecycle.
  5. *Expansion: seats added per account* — lagging. Owner: growth.
  *(5 chosen, not 12 — the smallest set that moves the NS for this model.)*
- **Driver tree (excerpt):** Activated teams = signups × team-setup-completion × first-loop-completion. first-loop-completion = brief-generated × PRD-approved × handoff-success × merge-confirmed → each maps to a real instrumented event from the codebase (e.g. `prd.approved`, `handoff.dispatched`); `merge-confirmed` has **no event yet → flagged "to instrument."**
- **Guardrails:** PRD/output quality score (don't ship junk to hit loop-completion); false-handoff rate; cost-per-loop (unit economics).
- **Health:** loop p95 latency, agent error rate, ingestion freshness, support tickets per active team.
- **Leverage:** `handoff-success` — currently the lowest-converting step in the tree (from instrumented data); a realistic lift there moves the NS most. Owner: engineering.
- **Legend:** every node tagged [NS]/[L1-supporting]/[driver]/[guardrail]/[health] with owner; "to instrument" leaves handed to `analytics-instrumentation`.
