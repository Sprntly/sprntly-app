---
name: saas-metrics-diagnosis
description: Diagnose SaaS health from raw metrics and find the bottleneck. Use when the user says "diagnose our metrics", "what's wrong with our funnel", "SaaS metrics health check", "our growth is stalling", or pastes metric numbers. Computes the standard SaaS metrics, benchmarks them, and pinpoints the limiting constraint rather than generic advice.
---

# SaaS Metrics Diagnosis

## What it does
Takes raw numbers and produces a diagnosis: it computes the standard SaaS metrics, compares them to rough benchmarks, and identifies *the* bottleneck constraining growth — instead of generic "improve retention" advice. It reasons like a growth doctor: find the limiting metric, then prescribe.

## When to use / when NOT to use
- **Use** to find why growth is stalling or to run a health check.
- **Do NOT use** to design a specific experiment (`experiment-design`).

## Inputs
- **Required:** some metric inputs (signups, conversion, churn, ARPU, CAC — whatever is available).
- **Optional:** time series, segment splits, stage/benchmarks. *If missing, compute what's possible and state which metrics are needed to complete the diagnosis.*

## Method (methodology)
Pirate metrics (AARRR) + unit economics + bottleneck analysis (the Digidai-style "diagnose, don't generic-advise" approach).
1. **Compute** available metrics via `scripts/saas_metrics.py` (LTV, CAC, LTV/CAC, payback, churn, magic number, quick ratio, etc.).
2. **Benchmark** each against rough stage-appropriate ranges (label benchmarks as directional).
3. **Locate the bottleneck** — the one stage/metric most constraining growth (e.g., great acquisition but activation leaks, so spend is wasted).
4. **Quantify the prize** — how much the top metric improves if the bottleneck is fixed to benchmark.
5. **Prescribe** the specific lever for that bottleneck, and what NOT to work on yet.

## Output spec
Computed metrics table · benchmark comparison · the identified bottleneck + why · the quantified prize of fixing it · the prescribed lever + explicit "don't optimize X yet." Math via `scripts/saas_metrics.py`.

## Sprntly integration (optional)
- **Inputs from Sprntly:** live metrics from connected analytics/outcome graph (no manual entry).
- **Outputs to Sprntly:** the bottleneck registered as the top opportunity; the prize feeds prioritization.
- **Degrades to:** standalone from pasted numbers.

## Quality checklist (the bar)
- [ ] Metrics are computed (script), not eyeballed.
- [ ] Exactly one primary bottleneck is identified, not a laundry list.
- [ ] The prize of fixing it is quantified.
- [ ] It says what NOT to work on yet.

## Known gaps / limitations
- Benchmarks vary wildly by model/stage; treated as directional, not gospel.
- Without segment splits, it can miss that the "average" hides a healthy and a broken cohort — it flags when splits are needed.

## Worked example
**Input:** "5000 signups/mo, 38% activate, 4.2% monthly churn, ARPU $40, CAC $90."
**Output (abridged):** LTV ~$952 (ARPU/churn), LTV/CAC ~10.6 (strong), payback ~2.25mo. Bottleneck: activation at 38% (benchmark ~50%+) - acquisition and economics are healthy, so spend is leaking at activation. Prize: lifting activation to 50% is ~+32% more activated users at the same spend. Prescribe: fix first-value onboarding. Don't: pour more into acquisition or fight churn yet.
