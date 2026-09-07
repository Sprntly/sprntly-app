---
name: retention-churn
description: Analyze retention and diagnose churn drivers. Use when the user says "analyze retention", "why are users churning", "cohort analysis", "retention curve", or "reduce churn". Produces a read of the retention curve (does it flatten?), cohort patterns, churn-driver hypotheses, and where to intervene.
---

# Retention & Churn

## What it does
Reads retention data to answer the only question that matters first - does the curve flatten (a retained core exists) or decay to zero (no product-market fit yet)? - then segments cohorts, hypothesizes churn drivers, and points to the highest-leverage intervention point (often early, not late).

## When to use / when NOT to use
- **Use** to understand whether the product retains and why users leave.
- **Do NOT use** to measure PMF holistically (`product-market-fit`) or design the fix experiment (`experiment-design`).

## Inputs
- **Required:** retention/churn data in some form (curve, cohort table, or churn rate + context).
- **Optional:** cohort splits, event data, churn-survey/exit reasons, segment. *If missing, interpret what's given and state what's needed.*

## Method (methodology)
Retention-curve shape + cohort analysis + churn-driver hypotheses + intervention timing.
1. **Curve shape** — flattening (good: a sticky core) vs decaying to zero (bad: no retention). This is the first read.
2. **Where it drops** — the period of steepest loss (often week 1 / pre-activation).
3. **Cohort comparison** — are newer cohorts retaining better/worse? Did something change?
4. **Driver hypotheses** — onboarding gap, value not reached, missing habit trigger, wrong users acquired.
5. **Intervention point** — usually earlier than teams expect (fix the early cliff before the late trickle).
6. **Voluntary vs involuntary** churn split (e.g., failed payments) where relevant.

## Output spec
Curve-shape verdict (flattens vs decays) · the steepest-drop period · cohort trend · ranked churn-driver hypotheses · the recommended intervention point + why · voluntary/involuntary note.

## Sprntly integration (optional)
- **Inputs from Sprntly:** cohort + event data from analytics; exit-survey signals from connected sources.
- **Outputs to Sprntly:** the top churn-driver hypothesis as an opportunity; the intervention point feeds discovery/experiments.
- **Degrades to:** standalone from provided data.

## Quality checklist (the bar)
- [ ] The first read is curve shape (flatten vs decay), not the headline churn %.
- [ ] The steepest-drop period is identified.
- [ ] Driver hypotheses are ranked, not listed.
- [ ] Intervention timing is specified.

## Known gaps / limitations
- Drivers are hypotheses until tested; the skill labels them and points to experiments.
- A flat-looking curve can hide a shrinking active core; segment to confirm.

## Worked example
**Input:** "Monthly churn 4.2%, most loss in first 2 weeks."
**Output (abridged):** Curve: need to confirm it flattens after the early cliff - request the cohort curve. Steepest drop: weeks 1-2 = activation failure, not late churn. Top hypothesis: users never reach first value. Intervention: onboarding/first-win, NOT win-back emails for month-6 churners. Check involuntary churn (payment failures) separately.
