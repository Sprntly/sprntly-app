---
name: strategy-frameworks
description: Pick the right strategy lens for the question, apply it rigorously, and end in a decision — not a filled-in template. Covers SWOT, PESTLE, Ansoff (growth options), and the Business Model / Startup canvases, plus when each is the wrong tool. Use when the user says "run a SWOT", "PESTLE", "macro environment", "Ansoff", "growth options matrix", "business model canvas", "startup canvas", or "which strategy framework should I use". Selects the lens that fits, fills it from evidence (labels assumptions), and converts it into a recommendation.
---

# Strategy Frameworks (lens selection + decision)

## What it does
Most framework outputs die as a tidy 2x2 nobody acts on. This skill does the opposite: it **picks the lens that actually fits the question**, applies it with real evidence (assumptions labeled, never invented), and then **converts the analysis into a decision or recommendation**. One skill spans the classic strategy lenses so you choose deliberately instead of defaulting to whatever you remember.

## When to use / when NOT to use
- **Use** for a structured strategic read: external forces, internal position, growth options, or business-model design.
- **Do NOT use** for the full leadership strategy doc (`product-strategy-stack`), competitor deep-dives (`competitive-intelligence-review`), market attractiveness/sizing (`market-structure`), or pricing (`pricing-packaging`).

## Inputs
- **Required:** the strategic question or the company/product + goal.
- **Optional:** market data, internal metrics, competitor facts, constraints. *Missing facts are labeled `[ASSUMPTION]`, never fabricated.*

## Method (methodology)
1. **Select the lens (the real value):**
   | Question | Lens |
   |---|---|
   | What outside forces are shifting under us? | **PESTLE** (political/economic/social/tech/legal/environmental) |
   | Where do we stand, internal vs external? | **SWOT** (strengths/weaknesses → opportunities/threats), forced into TOWS actions |
   | How should we grow — same/new market × same/new product? | **Ansoff** (penetration / product-dev / market-dev / diversification, by risk) |
   | How does the whole business create/capture value? | **Business Model Canvas** (9 blocks) |
   | New product, need model + strategy together? | **Startup Canvas** (problem→solution→model→moat in one page) |
2. **Fill from evidence**, labeling assumptions; flag the 2-3 cells that actually matter (a SWOT with 5 items per quadrant is noise — name the load-bearing ones).
3. **Convert to a decision.** SWOT→TOWS moves; PESTLE→which forces to act on / monitor / ignore; Ansoff→the chosen growth path + why the riskier ones wait; BMC/Startup→the riskiest block to validate first.
4. **State what would change the call** and the one thing to validate.

## Output spec
The chosen lens (and why it fits), the filled framework with the load-bearing cells flagged, then a **decision section** — the move, the rationale, the riskiest assumption to test. Narrative-led; a compact table per framework.

## Sprntly integration (optional)
- **Inputs:** business-context + competitive-intelligence-review for the external/internal facts; goals from the knowledge graph.
- **Outputs:** the chosen move + riskiest assumption routed to `assumption-risk-map`; growth path to `product-strategy-stack`.
- **Degrades to:** standalone from the question.

## Quality checklist (the bar)
- [ ] The lens is *chosen* to fit the question, with a one-line why; wrong-tool cases named.
- [ ] Load-bearing cells flagged; not an even pile of bullets.
- [ ] Ends in a decision + the riskiest assumption to validate — never a static template.
- [ ] Assumptions labeled, never invented; easy to read.

## Known gaps / limitations
- A framework organizes thinking; it can't supply missing market truth — pair with `market-structure`/`competitive-intelligence-review`.
- SWOT/PESTLE invite list-padding; the skill forces prioritization but judgment still decides what's load-bearing.

## Worked example
**Input:** "Should Sprntly expand from PM teams into adjacent functions?" → **Ansoff** chosen. Penetration (more PM teams) = low risk, do now; market-development (new function, same product) = medium, gated on the loop being proven; diversification = parked. Decision: deepen penetration first; riskiest assumption to test = whether the outcome-loop value transfers to a non-PM buyer.
