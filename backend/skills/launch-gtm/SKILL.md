---
name: launch-gtm
description: Plan a product launch and go-to-market. Use when the user says "launch plan", "go-to-market", "GTM", "we're launching X", "launch checklist", or "how do we roll this out". Produces a launch tier, audience/channels, messaging hooks, a readiness checklist, and success metrics - scaled to how big the launch actually is.
---

# Launch / GTM

## What it does
Produces a launch plan sized to the launch: it first classifies the launch tier (a minor update is not a major launch), then plans audience, channels, messaging, rollout mechanics, a readiness checklist, and the metrics that define success. It prevents both over-investing in a minor release and under-preparing a major one.

## When to use / when NOT to use
- **Use** to plan how a product/feature reaches the market.
- **Do NOT use** for positioning/messaging strategy itself (`positioning`) or release notes (`release-notes`).

## Inputs
- **Required:** what's launching and to whom.
- **Optional:** positioning, channels available, timeline, sales involvement, success metric. *If missing, classify the tier and draft the plan, labeling assumptions.*

## Method (methodology)
Launch-tiering + channel fit + readiness gating + success definition.
1. **Classify the tier** - Tier 1 (major, company-level), Tier 2 (notable feature), Tier 3 (minor/incremental). Effort scales with tier.
2. **Audience & message** - who needs to hear it, the hook (pull from `positioning`), per-segment angle.
3. **Channels** - matched to audience and tier (in-product, email, blog, sales enablement, PR, community).
4. **Rollout mechanics** - flag/phased/GA; beta or design-partner stage first if risky.
5. **Readiness checklist** - product, docs, support, sales, legal/compliance, metrics in place.
6. **Success metrics** - what adoption/impact = success, measured how, by when.

## Output spec
Launch tier + rationale · audience + per-segment message · channel plan · rollout mechanics · readiness checklist · success metrics + measurement plan.

## Sprntly integration (optional)
- **Inputs from Sprntly:** the shipped feature + PRD; positioning; readiness signals (docs, instrumentation) from connected sources.
- **Outputs to Sprntly:** the launch plan as an artifact; success metrics registered to the outcome graph; checklist items as tracked tasks.
- **Degrades to:** standalone; classify tier and label assumptions.

## Quality checklist (the bar)
- [ ] The launch is tiered; effort matches the tier.
- [ ] Channels match the audience, not a default everything-everywhere.
- [ ] The readiness checklist covers support, docs, and instrumentation, not just marketing.
- [ ] Success is defined as a measurable metric, not "it went well."

## Known gaps / limitations
- Over-launching minor releases erodes attention; the tiering step is the main guard.
- Channel effectiveness varies by audience; without data, channel choices are best-practice defaults.

## Worked example
**Input:** "Launching real-time collaboration to existing customers."
**Output (abridged):** Tier 2 (notable feature, existing base). Audience: current team accounts (esp. churned-risk enterprise). Hook: "edit together, no more overwrite chaos." Channels: in-product banner, targeted email to multi-seat accounts, sales enablement for at-risk renewals; skip PR. Rollout: design-partner beta -> phased GA. Readiness: docs, support macros, instrumentation (`analytics-instrumentation`), guardrail latency monitor. Success: % multi-seat accounts using co-edit in 30 days; renewal lift in the at-risk cohort.
