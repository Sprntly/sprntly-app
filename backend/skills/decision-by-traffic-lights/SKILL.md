---
name: decision-by-traffic-lights
description: A tradeoff framework that turns a hard decision into a one-page, leader-ready artifact using the red/amber/green traffic-light method. Use it whenever options must be weighed against competing criteria (revenue vs risk vs effort vs focus) and a leader has to pick. Triggers: "decision by traffic lights", "traffic light decision", "RAG decision", "weigh the tradeoffs", "help me/leadership decide", "make a call on", "go/no-go", "decision brief for a VP/exec", "build this or not", or any tradeoff needing sign-off. Produces, in order: a title that states the decision, a short context block, the recommendation + ask, then one fully color-filled table that scores each option against the criteria, gives it a status (Recommended / Considering / Do not recommend), and a rationale. Colors are scored objectively from evidence, with a defined color key.
---

# Decision by Traffic Lights

## What it does
A **tradeoff framework**: it weighs each option against the competing criteria that matter (e.g. revenue, opportunity cost, risk, effort) and renders the result as a one-page decision artifact a staff PM puts in front of a VP (or any leader) to get a call made. It reads top-to-bottom the way an exec wants it: a **title that states the decision**, a short **context** block (why it's on their desk now), the **recommendation and ask**, and then a single **fully color-filled traffic-light table** that holds the evidence — each option scored red/amber/green against the criteria that matter, carrying a colored **status** (Recommended / Considering / Do not recommend) and a **Rationale**. Built on Naomi Gleit's (Meta) traffic-light framework, with a **defined color key** so the table is unambiguous and an **objectivity rule** so colors are scored from evidence, never bent toward a preferred answer.

## When to use / when NOT to use
- **Use** for a real tradeoff that needs a call — build vs not, build vs buy, which bet to fund, enter a market or not, ship-now vs wait — especially when a leader must sign off and needs to see the reasoning, not just the answer.
- **Do NOT use** to rank a long backlog (`prioritize`), to classify reversibility / write the options-and-tradeoffs memo (`decision-memo` — pair with it), or to pressure-test the recommendation adversarially (`red-team-review` — run after).

## Inputs
- **Required:** the decision to be made (even one line).
- **Optional:** the broader goal it serves, the options under consideration, the criteria that matter, any data/constraints, who the decision-maker is. *If options or criteria are missing, generate a sensible set and label them `[ASSUMPTION]`; ask only the 1–2 questions that would most change the call. Never invent a fact to justify a color — an unknown is amber-with-a-reason or an open question, not a guessed green.*

## Method (methodology)
Adapts **Naomi Gleit's (Meta) traffic-light framework** + a leader-facing decision-memo frame + goal-anchoring + an **objectivity guard** (every color is scored from evidence, not from the outcome you want). Build the artifact in this order:
1. **Title the decision.** A one-line title that states the actual decision as a question or a clear choice — e.g. "Should we build the auto-execute feature?" — so any reader knows exactly what's being decided. Never a generic "decision brief."
2. **Situate it (context).** 2–4 sentences: where this sits, what triggered it / why now, what's at stake, and what a good outcome looks like (the goal). Every color below is judged against this goal.
3. **State the recommendation + ask — before the table.** The call in 1–2 sentences, then the explicit ask (what you need approved, by when). The leader reads the conclusion first, then the evidence that backs it.
4. **Options become table rows** (always include "do nothing / status quo").
5. **Criteria become columns.** The 3–6 dimensions the decision turns on; flag any **must-have** (a red there disqualifies the option). Pick the criteria the decision actually hinges on, and pick them before scoring so the criteria aren't gamed to fit a preferred answer.
6. **Define the color key, then score every cell objectively.** State plainly what green / amber / red mean (see Output spec), then color each cell from evidence against *that criterion alone*, independent of which option you want to win. Put a one-line reason in every cell; an unknown is amber-with-a-reason, never a guessed green; assumed facts are tagged `[ASSUMPTION]`. The verdict must *emerge from* honest cells, not dictate them — if a reasonable colleague would score a cell differently, say why you chose the color you did.
7. **Give every option a colored status + a rationale.** A status label — **Recommended** (green), **Considering** (yellow), or **Do not recommend** (red) — read off the criteria cells, not decided first. The color-matched **Rationale** carries the *why*: the disqualifying reason (Do not recommend), the specific observable trigger that would advance it (Considering), or why-it-wins now + the **tradeoff accepted** + the **riskiest assumption** (Recommended).
8. **Self-check** against the Quality checklist.

## Output spec
A one-page, color-filled decision artifact (use `templates/decision-narrative-template.md`), in this exact order:
1. **Title** — states the decision (a question or clear choice), reflecting what's actually being decided.
2. **Context** — 2–4 sentences situating it: where it sits, why now, what's at stake, what good looks like.
3. **Recommendation + ask** — the call in 1–2 sentences and exactly what you need approved, by when. **Sits before the table.**
4. **Color key** — a one-line definition of each color, so the table reads unambiguously:
   - 🟢 **Green** — favorable on this criterion / clears the bar. At the option level: **Recommended**.
   - 🟡 **Amber** — mixed, conditional, or unproven; needs a trigger to resolve. At the option level: **Considering**.
   - 🔴 **Red** — unfavorable or blocking; a red on a must-have (✱) disqualifies the option. At the option level: **Do not recommend**.
5. **The decision table** — and nothing after it. Option rows (incl. do-nothing) × criteria columns, with **every cell fully filled with its color and a one-line reason** (colors scored objectively per criterion), must-haves flagged; under each option a colored **status** (Recommended / Considering / Do not recommend); a color-matched **Rationale** column.

The cell **fill is part of the output, not decoration**, so render on a surface that supports it — an HTML/visual artifact or a doc with shaded table cells. (Plain markdown can't fill cells; approximate with a color word + marker per cell and note the true artifact is color-filled.) Hand to `exec-narrative` only if the leader later wants a longer written case; by default the table carries it.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the decision context + supporting evidence/confidence across sources to ground each cell's reason; the goal/North-Star this serves; prior related decisions from the knowledge graph.
- **Outputs to Sprntly:** the decision artifact + the recommendation recorded to the outcome graph; the "Considering" triggers registered as monitored conditions (so when a condition is met, that option can be revisited); the accepted tradeoff and riskiest assumption tracked.
- **Degrades to:** fully standalone — give it the decision and it produces the artifact, asking at most 1–2 questions.

## Quality checklist (the bar)
- [ ] The **title states the actual decision** (a question or clear choice), not a generic "decision brief."
- [ ] Order is **title → context → recommendation + ask → color key → table** — the leader reads the conclusion before the evidence.
- [ ] A **color key defines** what green / amber / red mean.
- [ ] Every cell is fully color-filled with a one-line reason; **colors scored objectively** from evidence against the criterion, not bent toward a preferred option.
- [ ] Must-have criteria are flagged; a red on one disqualifies the option.
- [ ] Every option carries a colored **status — Recommended / Considering / Do not recommend** — that is a readout of the cells, not a foregone conclusion.
- [ ] The **Rationale** column carries the disqualifying reason (Do not recommend), the advance-trigger (Considering), and why-it-wins + tradeoff + riskiest assumption (Recommended).

## Known gaps / limitations
- Colors are judgments; with no data the reasons are reasoned estimates, labeled as such — the artifact is only as honest as its inputs.
- The framework's known failure mode is **bias** — a motivated author paints cells toward the answer they already want, or picks criteria that flatter it. The objectivity rule (steps 5–6), per-cell reasons, and must-have flags mitigate it but can't fully remove a thumb on the scale; for high-stakes calls, run `red-team-review` on the scoring, or have a second person re-color the table blind to the recommendation.
- The cell *coloring* needs a surface that supports fills (HTML/visual artifact or a doc); plain-markdown chat can only approximate it with color words + markers.
- It structures the decision; it does not make the bet correct. Pair with `decision-memo` for reversibility framing and `pre-mortem` for failure modes on the chosen path.

## Worked example
**Input:** "Build the autonomous 'auto-execute' feature (agents act on customer systems without per-step approval), or not? There's real enterprise revenue, it deprioritizes our reliability roadmap, and there's legal risk."

**Output (abridged — the real artifact is one color-filled page):**
1. **Title:** "Should we build the autonomous auto-execute feature?"
2. **Context:** inbound enterprise demand + a competitor marketing it; building it would consume the reliability quarter design partners asked for and introduces a new class of legal liability (autonomous actions on live customer systems). On your desk because only you can accept that revenue-vs-liability tradeoff. Good = grow enterprise revenue without company-threatening liability or derailing the roadmap.
3. **Recommendation + ask:** stay human-in-the-loop now; pursue a scoped/guarded version only once legal + 2 design partners clear it; don't build full auto-execute. *Ask:* approve holding at HITL this quarter and scoping the guarded version for legal + partner validation.
4. **Color key:** 🟢 favorable / Recommended · 🟡 mixed or conditional / Considering · 🔴 blocking / Do not recommend (✱ = must-have).
5. **Table (criteria scored objectively per criterion):**
   - *Build full auto-execute now* — Revenue 🟢 / Deprioritization 🔴 / Legal✱ 🔴 → **Do not recommend.** Rationale: fails the legal must-have + breaks the HITL thesis; revenue can't offset.
   - *Build scoped / guarded* — Revenue 🟡 / Deprioritization 🟡 / Legal✱ 🟡 → **Considering.** Rationale: advance to Recommended *if* legal clears the reversible-only scope AND 2 partners confirm it closes. Riskiest assumption: the scoped version captures enough revenue.
   - *Don't build — stay HITL* — Revenue 🔴 / Deprioritization 🟢 / Legal✱ 🟢 → **Recommended.** Rationale: protects the roadmap and adds no liability while we validate the scoped path. Tradeoff: cedes near-term automation revenue.
