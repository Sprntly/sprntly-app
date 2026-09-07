---
name: ideation-prioritize
description: Triage and prioritize a pool of product ideas into a visible shortlist. Use when the user says "prioritize my ideas", "triage my backlog", "this ideation list is a mess", "dedupe these ideas", "what should we focus on", or pastes a long unstructured list of issues/requests. Produces a clustered, deduped pool with a 25-30 idea shortlist worth a PM's attention.
---

# Ideation Prioritize

## What it does
Takes a sprawling, inconsistent pool of product ideas and makes it actionable: clusters related items, flags duplicates, separates genuine product work from tech debt and bugs, and — the core output — picks the SHORTLIST of 25–30 ideas actually worth attention now, balancing goal-fit, evidence severity/volume, and topic diversity. Everything else stays in the pool (nothing is deleted) but out of sight until it earns a slot.

## When to use / when NOT to use
- **Use** when an idea pool/backlog is too big or messy to act on directly.
- **Do NOT use** to score an already-clean short list (`prioritize`) or to break stories down (`story-mapping`).

## Inputs
- **Required:** the idea pool (list/export/paste), ordered by any existing priority signal if one exists.
- **Optional:** current goal, business context, team capacity, status fields. *If missing, triage structurally and ask for the goal before committing to a shortlist.*

## Method (methodology)
Affinity clustering + decline-criteria + goal/value lens + diversity check.
1. **Normalize** items to a common shape (title, type, apparent value).
2. **Cluster** by theme/feature area; collapse duplicates and near-duplicates (same project reworded = duplicate; related-but-distinct work is not).
3. **Classify** each: product opportunity / bug / tech debt / chore / idea. Keep them in separate lanes.
4. **Shortlist** — pick the 25–30 ideas worth attention now: weigh goal-fit, evidence severity and volume, and revenue at stake; treat any existing deterministic ordering as a strong prior and deviate only with a reason; keep the set DIVERSE across problem areas (never 28 variants of one theme); give each pick a one-line "why now".
5. **The rest** — everything unpicked stays in the pool with its rationale; it competes again on the next run. Mark off-strategy/stale items so a human can decline them.

## Output spec
Clustered pool by theme · duplicate map · type lanes (opportunity/bug/debt/chore) · the ordered 25–30 shortlist with one-line why-now each · unpicked remainder with reasons. Hands the shortlist to `prioritize` for finer scoring when needed.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the live ideation pool + signal/confidence on each item; the active goal; business context.
- **Outputs to Sprntly:** dedupe merges, per-item tag + rationale, and the shortlist written back (`shortlisted` flag — only shortlisted ideas are shown).
- **Degrades to:** standalone from a pasted list.

## Quality checklist (the bar)
- [ ] Duplicates are merged, not just noted.
- [ ] The shortlist is 25–30 (fewer only when fewer distinct ideas exist) and DIVERSE across problem areas.
- [ ] Every shortlist pick has a one-line "why now" grounded in the evidence.
- [ ] Nothing is deleted — unpicked ideas keep their place in the pool.
- [ ] The shortlist ties to the goal.

## Known gaps / limitations
- Without status/age data, staleness detection is weaker.
- It recommends; declining items is a human decision (surfaces, doesn't auto-delete).

## Worked example
**Input:** 110-line mixed list of feature asks, bugs, and "nice to haves," pre-ordered by an evidence score.
**Output (abridged):** 9 clusters; 14 dupes merged; bugs split into their own lane; 27-idea shortlist covering all 9 clusters (activation friction weighted highest — matches the goal), each with a one-line why-now; 60+ unpicked ideas left in the pool with rationale.
