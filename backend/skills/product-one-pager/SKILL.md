---
name: product-one-pager
description: Write a one-page product brief to get a decision or alignment from execs/stakeholders. Use when the user says "write a one-pager", "product brief", "exec brief", "pitch this internally", "1-pager for leadership", or needs to propose a bet concisely. Produces a tight brief: problem, why now, the bet, expected impact, cost/size, risks, and the specific ask.
---

# Product One-Pager

## What it does
Produces a genuinely one-page brief designed to secure a decision (fund it / kill it / prioritize it), not to document a feature. It leads with the problem and the ask, sizes the bet, and is ruthless about length — an exec should grasp it in 90 seconds.

## When to use / when NOT to use
- **Use** to propose a bet, get prioritization, or align leadership before deeper specs.
- **Do NOT use** as the build artifact (`prd-author`) or a launch plan (`launch-gtm`).

## Inputs
- **Required:** the idea/bet and the decision you want.
- **Optional:** market/segment size, current metric, strategic goal it serves, rough cost/effort, audience. *If missing, draft with labeled `[ASSUMPTION]`s and flag the 1–2 numbers most worth getting right before sending.*

## Method (methodology)
Based on BLUF (bottom line up front), Amazon's narrative density, and the rule that a one-pager's job is a *decision*.
1. **Lead with the ask + recommendation.** What decision, what you recommend, by when.
2. **Problem & why now.** The pain and the timing/wedge that makes it urgent.
3. **The bet.** What you'd do, in 2–3 sentences. Not a feature list.
4. **Expected impact.** The metric it moves + rough magnitude (range is fine; label assumptions).
5. **Cost & size.** Effort/time/people — order of magnitude.
6. **Risks & the one thing that could kill it.**
7. **Compress.** Cut to one page. If it doesn't fit, cut scope, not font size.

## Output spec
One page: Title + the ask (BLUF) · Problem & why now · The bet · Expected impact · Cost/size · Risks · Decision requested + by when. See `templates/one-pager-template.md`.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the ranked opportunity + confidence from the Weekly Brief, supporting signals across sources (for "why now"), and outcome-graph estimates (for expected impact).
- **Outputs to Sprntly:** the brief artifact + a decision record (approved/declined/deferred) written back so the outcome graph can later check whether the bet paid off.
- **Degrades to:** with no Sprntly context, ask for the idea, the decision wanted, and any known metric; assume + label the rest.

## Quality checklist (the bar)
- [ ] The ask and recommendation are in the first 2 lines.
- [ ] It actually fits on one page.
- [ ] Impact is tied to a metric, with magnitude (even a labeled range).
- [ ] The single biggest risk is named, not buried.
- [ ] No feature dump — it argues a bet, not a backlog.

## Known gaps / limitations
- Impact magnitude is only as credible as the inputs; with no data it estimates and labels, which executives should pressure-test.
- It optimizes for persuasion + clarity; it is not the place for full evidence (link to research/PRD).
- Cultural norms vary — some orgs want narrative prose, some want a slide; it defaults to prose and can be reformatted.

## Worked example
**Input:** "One-pager to get leadership to fund enterprise co-editing. Want a go/no-go next week."

**Output (abridged):**
- **Ask (BLUF):** Fund a 1-quarter, 3-engineer bet on real-time co-editing for enterprise. *Recommend: go.* Decision needed by Fri.
- **Problem & why now:** >50-seat accounts churn citing collaboration; two competitors shipped co-editing last quarter. `[ASSUMPTION: collaboration is top-2 churn driver — confirming via 5 interviews]`
- **The bet:** Ship co-editing for our primary doc type to 2 design partners, convert to a referenceable renewal.
- **Impact:** Defend ~$1.8M ARR at risk; +11–16% retention on the segment (range, assumption-based).
- **Cost:** ~1 quarter, 3 eng. **Biggest risk:** the real driver is permissions, not co-editing → de-risked by interviews first.
