---
name: market-structure
description: Assess market attractiveness and structure. Use when the user says "is this market attractive", "Porter's five forces", "market structure", "should we enter this market", or "TAM SAM SOM". Produces a five-forces read plus a rough market-sizing frame and an attractiveness verdict with the key risks.
---

# Market Structure

## What it does
Evaluates how attractive and defensible a market is: the competitive forces shaping profitability, a structured (if rough) sizing frame, and a clear verdict on attractiveness with the dominant risks. Helps decide whether a market is worth entering or doubling down on.

## When to use / when NOT to use
- **Use** for market entry/expansion decisions and attractiveness reads.
- **Do NOT use** for head-to-head competitor teardown (`competitive-intelligence-review`) or growth direction (`growth-vectors`).

## Inputs
- **Required:** the market/segment in question.
- **Optional:** known players, customer counts, price points, growth rate. *If missing, build the frame and label every number as an estimate to be validated.*

## Method (methodology)
Porter's Five Forces + TAM/SAM/SOM sizing + attractiveness synthesis.
1. **Five forces:** rivalry, new entrants, supplier power, buyer power, substitutes — rate each and explain.
2. **Sizing:** TAM (total), SAM (serviceable), SOM (realistically obtainable) with the assumption behind each number.
3. **Trajectory:** growing, flat, consolidating? Tailwinds/headwinds.
4. **Attractiveness verdict:** attractive / mixed / unattractive, with the 1-2 forces that dominate the call.
5. **Key risks + what would change the verdict.**

## Output spec
Five-forces table (rating + rationale) · TAM/SAM/SOM with assumptions · trajectory · attractiveness verdict + dominant forces · key risks.

## Sprntly integration (optional)
- **Inputs from Sprntly:** market signals across sources; existing customer/segment data for sizing anchors.
- **Outputs to Sprntly:** the market read as a reference for strategy; risks registered.
- **Degrades to:** standalone; estimate-and-label sizing.

## Quality checklist (the bar)
- [ ] Each force is rated AND explained, not just listed.
- [ ] Every sizing number has a stated assumption.
- [ ] The verdict names the forces that dominate it.
- [ ] Sizing distinguishes TAM from realistically obtainable SOM.

## Known gaps / limitations
- Sizing without data is an educated guess; the skill makes assumptions explicit but can't validate them.
- Five Forces is a snapshot; fast-moving markets need re-assessment.

## Worked example
**Input:** "AI PM-tooling market."
**Output (abridged):** Rivalry: high + rising (many entrants). New entrants: very high (low barriers, LLM commoditizing). Buyer power: medium (PMs influence, budgets centralized). Substitutes: high (DIY with raw Claude/ChatGPT). Verdict: mixed-to-tough — defensibility is the whole game; the dominant forces are entrant threat + substitution. Risk: no moat = race to the bottom. Changes verdict: a proprietary data/outcome loop competitors can't replicate.
