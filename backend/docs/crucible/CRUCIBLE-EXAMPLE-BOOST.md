# Worked Example: Facebook Boost, Revenue

**Purpose.** Demonstrates the full output shape, including the Stage 10 decision point. Synthetic data throughout. Use this as the format reference when building Stage 11 rendering.

**Company.** Facebook Boost. Self-serve advertising for small and mid-sized businesses. 8.33M active advertisers, $25.4B gross annual advertiser spend.

**Goal as stated.** "Drive revenue."

---

# Stage 0. Goal resolution

The goal has no number and no horizon, so Stage 0 could not adopt a target. Metric identity resolved cleanly, one registry match. Stated back:

> Revenue here means Gross Advertiser Spend from the finance metric layer: billed spend net of credits and refunds, excluding internal test accounts and agency rebates. Currently $25.4B trailing twelve months across 8.33M active advertisers.
>
> "Drive revenue" doesn't give me a size or a date, so I'll work to the largest defensible opportunity set rather than to a target. If you have a number for the year, tell me and I'll show you the gap to it instead.

**Locked.** Goal currency: incremental gross advertiser spend, annualised, persistence-adjusted.

---

# TL;DR

**Most of the revenue you are losing is lost in the first ninety seconds of campaign creation, not in the auction.**

Three findings account for **$730M** of annualised recoverable spend, and the largest is not a targeting or ranking problem. It is that the default budget field anchors 41% of advertisers to a number well below what they would have chosen if shown their segment's median.

| | Recommendation | Value | Confidence | Effort |
|---|---|---|---|---|
| 1 | Budget anchoring at campaign creation | $492M | Medium | 4 weeks |
| 2 | Payment recovery for declined SMB cards | $97M | High | 6 weeks |
| 3 | Guided second campaign for solo advertisers | $141M | Medium | 9 weeks |

**Ruled out:** auction-side changes, creative generation, and audience expansion. Not because they are unpromising, but because the evidence base for all three sits in the ranking team's domain and Boost cannot move them unilaterally. Listed with reasons at the bottom.

---

# Diagnosis

## The revenue is concentrated, the losses are not

Revenue splits almost evenly across five segments, which is unusual and important. No segment is more than 24% of spend.

| Segment | Advertisers | Annual spend | Avg $/mo | M3 retention | Runs 2nd campaign |
|---|---|---|---|---|---|
| Solo / micro | 4,820,000 | $4.57B | $79 | 29% | 61% |
| Small local services | 2,140,000 | $6.10B | $237 | 44% | 72% |
| Small ecommerce | 1,060,000 | $5.33B | $419 | 51% | 75% |
| Mid-market multi-location | 184,000 | $4.83B | $2,186 | 68% | 83% |
| Agency-managed SMB | 122,000 | $4.57B | $3,123 | 74% | 88% |

**The losses are concentrated even though the revenue is not.** 83% of advertisers sit in the two segments with the worst retention and the lowest second-campaign rate, and those two segments carry 42% of spend. Whatever is failing is failing at the low-sophistication end, at enormous volume.

## The finding that required two documents nobody read together

Two independent sources, neither of which points at budget on its own:

**Source A, product telemetry.** The budget field at campaign creation is pre-filled with a platform-wide default. 41% of advertisers in solo, local, and ecommerce accept it unchanged. That number alone reads as a healthy default doing its job.

**Source B, advertiser research (Q2, n=340, local services and solo).** When asked how they picked their budget, the dominant response was not a business calculation. It was uncertainty about what was normal. Several described the pre-filled number as a recommendation rather than a placeholder.

*[CHART: distribution. Self-set budget histogram with the default-accept spike overlaid at $20, self-set median line at $30. Annotation marks the pile-up. Series bind to claims C-041 (telemetry) and C-044 (budget distribution).]*

**What the research said, verbatim.** Three of 340 Q2 interviews, selected as the clearest statements of the dominant theme. 61% of coded responses fell into this theme. The quotes explain how the field is read. They do not size how many read it that way, which is what the telemetry is for (I4).

> "I assumed the number in the box was what Facebook thought I should spend. I did not want to look like I was overdoing it, so I left it."
> — Solo advertiser, home services, 4 months tenure

> "Honestly I have no idea what normal is. Twenty dollars, two hundred dollars. There is nothing to compare it to so I just went with what was there."
> — Small local services, restaurant, 11 months tenure

> "I changed it the second month once I saw what a day actually bought me. I would have started higher if someone had told me what other places my size were doing."
> — Small local services, fitness studio, 2 years tenure

**Read together:** the default is not a neutral placeholder. It is being read as advice, by the segment least equipped to disagree with it, at a value below what those advertisers would choose when shown a peer median. A held-out experiment on local services in Q1 that surfaced a segment median alongside the field produced a 16% mean budget increase with no measurable increase in campaign abandonment.

That Q1 experiment is the reason this ranks first. It is the only candidate in the top three with a directly comparable prior on this platform.

**Not claimed:** that the default causes low spend. The Q1 experiment establishes that changing the anchor moves the chosen budget in one segment. Generalisation to solo and ecommerce is an assumption, flagged below.

## The silent revert nobody logged

Payment retry logic for declined cards shipped in March, was reverted in April behind a flag after an unrelated incident, and was never re-enabled. The flag is still off. No ticket records the decision.

*[CHART: timeline. Recovery rate by month. May onward renders as a visible gap because no data exists, never as zero (I3). Flag-off period shaded.]*

11.3% of solo and local advertisers hit a hard card decline in any 90-day window. The retry path that would have caught a third of them has been dark for four months. This is the highest-confidence item in the set because it requires building almost nothing.

---

# Recommendations

## 1. Budget anchoring at campaign creation

**$492M annualised. Medium confidence. 4 weeks.**

Replace the platform-wide default with a segment-derived median, shown as a labelled reference rather than a pre-filled value, at the budget step.

**Derivation.** 3,288,200 advertisers accept the default across solo, local, and ecommerce (41% of 8.02M). Q1 comparable gives 16% mean budget lift. Blended average affected spend $156/mo. Persistence haircut of 50% applied against the Q1 result because that experiment ran 60 days and annualisation beyond its observation window is an assumption.

`3,288,200 × $156 × 0.16 × 12 × 0.50 = $492M`

*[CHART: comparison. Q1 control vs treatment mean daily budget, with a paired abandonment panel. The abandonment panel is included because it is the obvious objection.]*

**Evidence chain**

| Observed | Population | Strength | Implies | Falsified by |
|---|---|---|---|---|
| 41% accept the pre-filled default; self-set budgets centre at $30 | 3.29M advertisers in solo, local, ecommerce | Measured (telemetry) + causally tested in one segment (Q1) | Anchor is read as advice and sits below revealed preference | Solo holdout showing lift under 6% |

**Confidence is medium, not high, and the weaker leg is the problem side.** The Q1 experiment covers local services only. Solo advertisers have a different relationship to budget, lower absolute spend, more personal money, and may respond to a peer median with anchoring in the opposite direction. This is the single largest assumption in the report.

**Falsifier.** If a solo-segment holdout shows budget lift under 6%, the value drops below $200M and this stops being first.

## 2. Payment recovery for declined SMB cards

**$97M annualised. High confidence. 6 weeks.**

Re-enable the reverted retry path, add a backup payment method prompt, and introduce a 72-hour grace window before campaign suspension.

**Derivation.** 786,480 advertisers hit a hard decline in 90 days. 38% recoverable, based on the March cohort's behaviour in the three weeks the retry path was live. Residual monthly value at risk $49 blended. 55% persistence past 12 months.

**High confidence because the mechanism was observed working on this platform.** The March data is a direct measurement, not a comparable. The effort is low because the code exists and is flag-gated.

**This should probably start first regardless of rank.** It is the cheapest item, the surest, and it is currently a live regression rather than a new bet.

## 3. Guided second campaign for solo advertisers

**$141M annualised. Medium confidence. 9 weeks.**

39% of solo advertisers never run a second campaign. Introduce a post-campaign path that starts from the completed campaign's results rather than from an empty form.

**Derivation.** 7 percentage point uplift assumed, from the local-services onboarding comparable. 337,400 advertisers, $79/mo, 44% persistence.

**The weaker leg is the solution side.** We know the drop-off exists. We do not have a Boost comparable for fixing it, only an adjacent one from a different segment. Treat as a pilot, not a build.

---

# Prioritisation

**Framework: RICE (default).** No prioritisation rubric was found in company context. If Boost has one, say so and this re-orders without re-running the analysis.

| Rank | Item | Reach | Impact ($/advertiser/yr) | Confidence | Effort (wks) | RICE |
|---|---|---|---|---|---|---|
| 1 | Budget anchoring | 3,288,200 | $150 | Medium (0.58) | 4 | 71 |
| 2 | Payment recovery | 786,480 | $123 | High (0.80) | 6 | 13 |
| 3 | Guided second campaign | 337,400 | $418 | Medium (0.58) | 9 | 9 |
| 4 | Objective selection correction | 671,274 | $262 | Low (0.35) | 7 | 9 |
| 5 | Multi-location bulk tooling | 62,560 | $4,988 | Medium (0.58) | 22 | 8 |

**Unrankable (1 item).** Agency multi-client permissions. Effort could not be derived: only two comparable prior projects exist on the agency surface, below the three-comparable minimum. Estimated value $91M. Listed here rather than ranked, because assigning it an effort number would launder a guess into a decision.

**Sensitivity.** The order is stable at the top and fragile in the middle. Budget anchoring stays first under any weighting that does not zero out reach. Ranks 3 through 5 sit within one point of each other, so treat them as a tie rather than a sequence. If effort is weighted more heavily, payment recovery takes first place, which is arguably the correct read anyway given it is a regression rather than a new build.

**What I would actually do.** Start payment recovery this week because it is a live regression with code already written. Start the budget anchoring holdout in parallel, in the solo segment specifically, because that is the assumption carrying the largest number in this report. Do not commit to items 3 through 5 until that holdout reads out.

---

# Gap to target

No target was set, so there is no gap to close.

**Reachable in the next four quarters: $1.22B**, or 4.8% of current gross advertiser spend, across the five ranked items plus the unrankable one. That figure assumes all six ship and all persistence haircuts hold, which is optimistic. A defensible planning number is the top two at **$589M**, both of which rest on Boost-specific evidence rather than on transferred comparables.

---

# Considered and not prioritised

Eighteen candidates were examined and not carried into the ranking. Each can be expanded on request, which runs the full analysis for that item rather than restating this line.

| Candidate | Why not carried |
|---|---|
| Auction floor price adjustment | Outside Boost's control. Ranking team owns it. |
| Automated creative generation | Two prior attempts in the tracker, both abandoned at pilot. Rejection ledger entry from Q3. |
| Audience expansion defaults | Evidence base is ranking-side. Boost cannot move unilaterally. |
| Lookalike audience simplification | Real friction, but affects 4% of the affected population. Below materiality. |
| Annual prepay discount | Would shift timing of revenue, not amount. Wrong currency for this goal. |
| Reduce minimum daily budget | Directionally opposite to finding 1. Would lower the anchor further. |
| Campaign duration defaults | Screened at rank 9. Value $34M, effort 5 weeks. Expandable. |
| Post-campaign performance summary | Overlaps 70% with recommendation 3. Folded into it. |
| Ad account verification friction | Compliance-owned. Constraint, not lever. |
| Mobile creation flow parity | Already committed work, shipping Q4. Listed as interaction, not recommendation. |
| SMB credit line | Requires financial product infrastructure that does not exist. |
| Seasonal budget suggestions | Depends on recommendation 1 shipping first. Prerequisite, not parallel. |
| Multi-currency clarity | Value concentrated outside the goal population. |
| Support response time | Real, but the link to spend is unmeasured here. Would need instrumenting. |
| Advertiser education content | No comparable priors. Effort underivable and value unfalsifiable. |
| Re-engagement email for lapsed | Owned by lifecycle marketing. Different team's roadmap. |
| Bulk creative upload | Subsumed by candidate 5, multi-location tooling. |
| Placement control granularity | Requested by agencies, but agencies are 1.5% of advertisers and 18% of spend. Sized at $12M. |

---

# Assumptions to cross-check

1. **Q1 local-services budget result generalises to solo advertisers.** Largest assumption in the report. Carries $340M of the $492M. Untested.
2. **50% persistence haircut on budget lift.** Assumed, not derived. The Q1 experiment ran 60 days.
3. **March payment cohort behaviour represents the current declined population.** Four months stale.
4. **Segments are non-overlapping for the overlap discount.** Agency-managed and multi-location partially overlap. Discount of $31M applied and shown as its own line.
5. **No target date.** All values annualised on a rolling basis rather than to a fiscal year.

# Falsifiers

- Solo holdout shows budget lift below 6% → recommendation 1 drops below $200M
- Payment retry re-enable shows recovery below 20% → recommendation 2 drops below $50M
- Second-campaign uplift below 3 points in pilot → recommendation 3 does not clear the bar
