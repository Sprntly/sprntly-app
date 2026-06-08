---
name: backlog-triage
description: Clean up and triage a messy backlog. Use when the user says "triage my backlog", "this backlog is a mess", "dedupe these tickets", "what should we close", or pastes a long unstructured list of issues/requests. Produces a clustered, deduped backlog with debt/dupes/no-candidates flagged and a recommended top slice.
---

# Backlog Triage

## What it does
Takes a sprawling, inconsistent backlog and makes it legible: clusters related items, flags duplicates, separates genuine product work from tech debt and bugs, identifies "polite no" candidates, and recommends the top slice to act on. It's the cleanup step before prioritization scoring.

## When to use / when NOT to use
- **Use** when a backlog is too messy to prioritize directly.
- **Do NOT use** to score an already-clean list (`prioritize`) or to break stories down (`story-mapping`).

## Inputs
- **Required:** the backlog (list/export/paste).
- **Optional:** current goal, team capacity, status fields. *If missing, triage structurally and ask for the goal to recommend a top slice.*

## Method (methodology)
Affinity clustering + decline-criteria + value/effort lens.
1. **Normalize** items to a common shape (title, type, apparent value).
2. **Cluster** by theme/feature area; collapse duplicates and near-duplicates.
3. **Classify** each: product opportunity / bug / tech debt / chore / idea. Keep them in separate lanes.
4. **No-candidates** — items that are off-strategy, stale, or low-value: mark for "won't do" with a one-line reason.
5. **Top slice** — recommend the cluster/items to move into prioritization next, tied to the goal.

## Output spec
Clustered backlog by theme · duplicate map · type lanes (opportunity/bug/debt/chore) · "won't do" list with reasons · recommended next slice. Hands the clean opportunity lane to `prioritize`.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the live backlog + signal/confidence on each item; the active goal.
- **Outputs to Sprntly:** a reorganized backlog, dedupe merges, and "won't do" closures written back; top slice flagged for `prioritize`.
- **Degrades to:** standalone from a pasted list.

## Quality checklist (the bar)
- [ ] Duplicates are merged, not just noted.
- [ ] Product work, bugs, and debt are in separate lanes.
- [ ] Every "won't do" has a one-line reason.
- [ ] The recommended next slice ties to the goal.

## Known gaps / limitations
- Without status/age data, staleness detection is weaker.
- It recommends; closing items is a human decision (surfaces, doesn't auto-delete).

## Worked example
**Input:** 60-line mixed list of feature asks, bugs, and "nice to haves."
**Output (abridged):** 7 clusters; 9 dupes merged; 12 bugs split into their own lane; 8 "won't do" (off-strategy/stale) with reasons; recommended next slice = the "onboarding friction" cluster (5 items) since the goal is activation.
