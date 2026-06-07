---
name: decision-memo
description: Structure a product decision with options, tradeoffs, and a recommendation. Use when the user says "help me decide", "write a decision memo", "options for X", "should we do A or B", "pivot or persevere", or faces a fork. Produces a memo classifying the decision as reversible or not, laying out options with tradeoffs, and giving a clear recommendation.
---

# Decision Memo

## What it does
Frames a real decision for action: states the decision and why now, classifies it as a one-way or two-way door (so the team spends judgment proportionally), lays out the viable options with honest tradeoffs, and commits to a recommendation with the reasoning and the conditions that would change it. Includes pivot/persevere as a decision type.

## When to use / when NOT to use
- **Use** for a consequential fork: build vs buy, A vs B, pivot vs persevere, kill vs continue.
- **Do NOT use** to rank many items (`prioritize`) or to map assumptions (`assumption-risk-map`).

## Inputs
- **Required:** the decision to be made.
- **Optional:** the options considered, constraints, data, who decides. *If missing, generate plausible options and label assumptions.*

## Method (methodology)
Bezos one-way/two-way doors + options-with-tradeoffs + decision-reversal conditions.
1. **State the decision** and why it must be made now.
2. **Classify the door** — reversible (decide fast, low ceremony) vs irreversible (slow down, more rigor).
3. **Options** — 2–4 real ones (include "do nothing"); for each: pros, cons, cost, risk.
4. **Recommendation** — pick one, explain the reasoning, name what you're trading off.
5. **Reversal conditions** — what evidence would make you change your mind (pre-commit, to fight escalation bias).
6. **Decision owner + by-when.**

## Output spec
Decision + why now · door type · options table with tradeoffs · recommendation + reasoning · "I'd change my mind if…" · owner & deadline.

## Sprntly integration (optional)
- **Inputs from Sprntly:** relevant evidence/findings + confidence; prior related decisions from the knowledge graph.
- **Outputs to Sprntly:** a decision record written to the outcome graph (so the org can later check if it was right); reversal conditions become monitored triggers.
- **Degrades to:** standalone from the decision statement.

## Quality checklist (the bar)
- [ ] Door type is classified and matched to the rigor applied.
- [ ] "Do nothing" is one of the options.
- [ ] The recommendation names its tradeoff, not just its upside.
- [ ] Reversal conditions are pre-committed.

## Known gaps / limitations
- Quality depends on options being genuinely distinct — it will flag straw-man option sets.
- For purely financial decisions, pair with `lean-canvas` (unit economics) for the numbers.

## Worked example
**Input:** "Build our own billing or use Stripe Billing?"
**Output (abridged):** Door: largely two-way early (can migrate), trending one-way as data accrues — decide pragmatically now. Options: build / Stripe Billing / hybrid / do nothing. Recommendation: Stripe Billing now; trade off long-run flexibility + fees for 4 months of eng time and faster revenue. Change my mind if: billing logic needs >3 custom rules Stripe can't model, or fees exceed $X/mo at projected scale.
