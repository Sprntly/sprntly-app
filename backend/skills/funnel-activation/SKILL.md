---
name: funnel-activation
description: Analyze an activation funnel and find the leakiest step. Use when the user says "analyze our funnel", "activation analysis", "where are users dropping off", "onboarding funnel", or "improve activation". Produces a step-by-step funnel with drop-off rates, the biggest leak, the likely cause, and the highest-leverage fix.
---

# Funnel & Activation

## What it does
Maps the activation funnel from first touch to first value, quantifies the drop-off at each step, and identifies the single leakiest step where a fix would unlock the most downstream value - plus a hypothesis for *why* users drop there. It anchors activation on reaching first value, not just completing setup.

## When to use / when NOT to use
- **Use** to find where new users fall out before getting value.
- **Do NOT use** for full retention curves (`retention-churn`) or designing the tracking (`analytics-instrumentation`).

## Inputs
- **Required:** the funnel steps and (ideally) counts/rates at each.
- **Optional:** segment splits, time-to-step, the defined "first value" moment. *If missing, define the funnel structurally and mark rates as needed.*

## Method (methodology)
Activation funnel + first-value (aha-moment) definition + leak-prioritization.
1. **Define first value** - the aha moment that predicts retention, not a vanity "completed onboarding."
2. **Map steps** from entry to first value; get the rate at each transition.
3. **Find the biggest absolute leak** - the step losing the most users (absolute matters more than the highest % drop on a tiny step).
4. **Hypothesize the cause** at that step (friction, confusion, value not yet visible, wrong users).
5. **Quantify the prize** - downstream impact of fixing that step to a realistic rate.
6. **Segment check** - does one source/segment leak far worse (acquisition-quality problem vs product problem)?

## Output spec
Funnel steps with counts + drop-off rates · the biggest leak (absolute) · cause hypothesis · quantified prize of the fix · segment note.

## Sprntly integration (optional)
- **Inputs from Sprntly:** funnel event data from analytics; the defined first-value event from the knowledge graph.
- **Outputs to Sprntly:** the leak becomes the top activation opportunity; the prize feeds `prioritize`.
- **Degrades to:** standalone from provided steps/rates.

## Quality checklist (the bar)
- [ ] First value is a real aha moment, not "finished setup."
- [ ] The biggest *absolute* leak is identified (not a high % on a tiny step).
- [ ] A cause hypothesis accompanies the leak.
- [ ] A segment check distinguishes product vs acquisition-quality issues.

## Known gaps / limitations
- Cause is a hypothesis until tested; routes to `interview-guide`/`experiment-design`.
- Aggregate funnels hide segment differences; the segment check mitigates but needs the data.

## Worked example
**Input:** "Signup 5000 -> verified 4200 -> created first project 2100 -> invited/used core feature 1900."
**Output (abridged):** First value = used core feature (1900, 38%). Biggest absolute leak: verified -> first project (4200->2100, lose 2100 users, 50%). Hypothesis: empty state gives no next step. Prize: lifting that step to 70% adds ~840 activated users/mo. Segment check: is the leak worse for paid vs organic? If paid leaks more, it's an acquisition-quality issue, not just UX.
