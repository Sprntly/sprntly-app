---
name: negotiation-prep
description: Prepare for a negotiation or high-stakes ask. Use when the user says "prep me for a negotiation", "BATNA", "I need to ask for X", "vendor negotiation", "negotiate priorities/resources", or faces a consequential ask. Produces interests (both sides), BATNA/ZOPA, anchors, concession plan, and responses to likely pushback.
---

# Negotiation Prep

## What it does
Prepares you for a negotiation by mapping both sides' underlying interests (not just stated positions), establishing your BATNA and the likely zone of agreement, planning your opening anchor and concession sequence, and rehearsing responses to the pushback you'll get. Applies to vendor deals, resource/priority negotiations, cross-team trades, and partnership terms.

## When to use / when NOT to use
- **Use** before any consequential negotiation or high-stakes ask.
- **Do NOT use** to write the message itself (`stakeholder-update`) or the persuasive memo (`exec-narrative`).

## Inputs
- **Required:** what you want and who you're negotiating with.
- **Optional:** their likely interests, your alternatives, constraints/limits, history. *If missing, infer the counterpart's interests and label; ask for your walk-away.*

## Method (methodology)
Interests-over-positions (Fisher & Ury / Getting to Yes) + BATNA/ZOPA + anchoring + concession planning.
1. **Interests, both sides** - the *why* beneath each position; shared interests are where value is created.
2. **BATNA** - your best alternative if no deal; this sets your walk-away. Estimate theirs too.
3. **ZOPA** - the overlap where a deal is possible; if none, the negotiation is about changing the frame.
4. **Anchor** - your opening, justified, ambitious-but-credible.
5. **Concession plan** - what you'll trade, in what order, and what you won't give; trade things cheap-to-you/valuable-to-them.
6. **Pushback responses** - the 3-4 objections you'll hear and your reply to each.
7. **The close** - how to lock agreement and avoid re-opening.

## Output spec
Both sides' interests · your + their estimated BATNA · the ZOPA (or "no ZOPA - reframe") · opening anchor + justification · ordered concession plan + non-negotiables · objection/response pairs · a closing move.

## Sprntly integration (optional)
- **Inputs from Sprntly:** prior dealings/commitments with the counterpart from the knowledge graph; relevant data strengthening your case.
- **Outputs to Sprntly:** agreed terms + commitments tracked.
- **Degrades to:** standalone; infer counterpart interests and label.

## Quality checklist (the bar)
- [ ] Interests (the why) are mapped for both sides, not just positions.
- [ ] Your BATNA and walk-away are explicit.
- [ ] Concessions are sequenced and trade cheap-to-you for valuable-to-them.
- [ ] The likely pushback has prepared responses.

## Known gaps / limitations
- The counterpart's true interests/BATNA are estimates; the skill labels them and advises probing early in the conversation.
- It preps strategy; live negotiation skill (listening, patience) still matters.

## Worked example
**Input:** "Negotiate with another team to borrow an engineer for 6 weeks."
**Output (abridged):** Your interest: hit the launch date. Their interest: protect their own roadmap + not set a precedent. Shared: company-level launch success. Your BATNA: slip the launch 3 weeks (weak) -> negotiate from need, not strength. Anchor: ask for one senior eng full-time, 6 weeks. Concessions (cheap-to-you): offer to take their on-call, give credit publicly, return with a favor owed. Non-negotiable: must be someone who knows the codebase. Pushback "we can't spare anyone" -> "what if it's half-time for 8 weeks, and I cover your on-call?" Close: confirm scope + end date in writing.
