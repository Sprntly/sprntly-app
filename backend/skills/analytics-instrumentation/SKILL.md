---
name: analytics-instrumentation
description: Spec event tracking and dashboard requirements. Use when the user says "instrumentation spec", "what events should we track", "analytics requirements", "tracking plan", or "dashboard requirements". Produces a precise event/property tracking plan tied to the metrics you need, plus the dashboard spec to read them.
---

# Analytics & Instrumentation

## What it does
Translates the metrics you need into a precise tracking plan - the events, properties, and identity rules an engineer can implement without guessing - and specifies the dashboard that turns those events into the decisions they support. It prevents the "we shipped it but can't measure it" failure.

## When to use / when NOT to use
- **Use** when a feature needs measurement, or to fix a gappy/inconsistent tracking plan.
- **Do NOT use** to pick the North Star.

## Inputs
- **Required:** the feature + the questions/metrics it must answer.
- **Optional:** existing event taxonomy, analytics tool, identity model. *If missing, propose a consistent taxonomy and flag where it must match existing conventions.*

## Method (methodology)
Metric-first tracking design + naming convention + dashboard-for-decisions.
1. **Start from the metric/question**, not the UI - what must we be able to answer?
2. **Derive events** needed to compute each metric; one event per meaningful user action.
3. **Properties** per event (context needed to slice: segment, source, value).
4. **Naming convention** - consistent verb-object, casing; reuse existing names to avoid taxonomy sprawl.
5. **Identity & dedup** - how users are identified across sessions/devices; avoid double-counting.
6. **Dashboard spec** - the views/charts that answer the original questions, the cut/filters, and who reads them.
7. **QA plan** - how to verify events fire correctly before trusting the data.

## Output spec
Tracking plan table (event · trigger · properties · the metric it serves) · naming/identity rules · dashboard spec (views, filters, audience) · event-QA checklist.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the metrics/KRs that need measuring; existing taxonomy from the knowledge graph to stay consistent.
- **Outputs to Sprntly:** the tracking plan as part of the PRD/tech-spec handoff; events wired to the outcome graph so the closed loop can read them.
- **Degrades to:** standalone from feature + questions.

## Quality checklist (the bar)
- [ ] Every event traces to a metric/question it serves (no orphan tracking).
- [ ] Naming follows a consistent convention; reuses existing names.
- [ ] Identity/dedup rules are specified.
- [ ] A QA plan ensures events are trustworthy before use.

## Known gaps / limitations
- Over-instrumentation creates noise and cost; the metric-first rule fights this but teams still over-track.
- It specs measurement; data quality still depends on correct implementation (hence the QA step).

## Worked example
**Input:** "Instrument the new guided-onboarding feature."
**Output (abridged):** Question: does guided onboarding raise activation? Events: onboarding_started, onboarding_step_completed (prop: step_index), first_value_reached, onboarding_skipped. Properties: user_segment, acquisition_source. Identity: user_id stitched anon->known at signup. Dashboard: activation funnel by cohort + step drop-off chart, filterable by source; read by PM weekly. QA: fire test events, verify counts match a manual run-through.
