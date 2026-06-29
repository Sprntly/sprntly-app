---
name: sales-battlecard
description: Build a sales-ready battlecard for one named competitor — where we win, where we lose, objection handling, and trap-setting questions — grounded in real, sourced facts, never invented claims. Use when the user says "battlecard", "how do we sell against X", "competitor objection handling", "win/loss vs X", or prepping a deal against a rival. One competitor per card; honest about where they're stronger; gives reps language, not spin.
---

# Competitive Battlecard

## What it does
Produces a concise, sales-ready card for beating ONE named competitor in a deal: their strengths (honestly), where we genuinely win, the objections reps will hear + how to handle them, and trap-setting questions that surface our advantages. Grounded in real facts (sourced where external); it equips reps with truthful language, not spin that collapses on contact.

## When to use / when NOT to use
- **Use** to prep against a specific competitor in sales/deals.
- **Do NOT use** for the strategic competitive picture (`competitive-intelligence-review` — feeds this), positioning (`positioning`), or pricing (`pricing-packaging`).

## Inputs
- **Required:** the competitor name + our product.
- **Optional:** their pricing/features/recent moves, our win/loss notes, the deal context. *External claims about the competitor are sourced or labeled; never fabricated — a battlecard built on made-up weaknesses loses deals when reps repeat them.*

## Method (methodology)
1. **Their strengths — honestly.** What they genuinely do well (reps must not be blindsided).
2. **Where we win** — the 2-3 dimensions a customer values where we're clearly better, with proof.
3. **Objection handling** — the top objections reps hear, each with a true, concise response (acknowledge → reframe → evidence).
4. **Trap-setting questions** — questions that lead a buyer to surface our strengths / their gaps, fairly.
5. **Landmines** — where NOT to compete (their turf); steer to our ground.

## Output spec
A one-card layout: their strengths · where we win (with proof) · objection→response table · trap-setting questions · landmines to avoid. Tight, scannable.

## Sprntly integration (optional)
- **Inputs:** the competitor read from `competitive-intelligence-review`; positioning from `positioning`.
- **Outputs:** the card; recurring objections fed back to product as signals.
- **Degrades to:** standalone from competitor + product (external facts labeled).

## Quality checklist (the bar)
- [ ] Their strengths stated honestly — no strawman.
- [ ] Win areas are customer-valued and backed by proof, not assertions.
- [ ] Every objection has a true, concise response; nothing invented about the competitor.
- [ ] Landmines named — where not to fight.

## Known gaps / limitations
- Competitor facts go stale fast — date them and refresh (pair with `competitive-intelligence-review`).
- A battlecard wins conversations, not bad-fit deals — it won't make a wrong-segment prospect convert.

## Worked example
**Input:** "Sprntly vs Atlassian Rovo." Their strength: owns the system of record + 150B-graph context. Where we win: the closed outcome loop they don't have. Objection "we already have Rovo" → reframe to "Rovo tells you what happened; we measure whether what you shipped worked." Landmine: don't fight them on context breadth.
