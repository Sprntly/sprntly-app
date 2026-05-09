# Asurion — Expected Sprntly Output
This is the answer key. When the three input artifacts are fed to an LLM with a prompt asking for the top product opportunities for Asurion, this is the output it should produce.
Three layers:
Weekly Brief — three insight cards
Detailed Evidence — drill-down for each insight
Output quality standards — rules an LLM should follow
## Part 1 — Weekly Brief: Top 3 Cards
Three insight cards, ranked by impact × confidence. Each card scannable in 10 seconds.
────────────────────────────────────────────────────────────
Double down
+$15M LTV opportunity · -42pp churn gap
↑ DOUBLE DOWN  |  RETENTION · CHANNEL  |  CONFIDENCE 0.91
Users who file claims via app churn at 22%. Users who file via phone churn at 64%. 42% of phone filers are active app users.
Phone filers are paying customers who already have the app installed and use it monthly. They default to phone because File a Claim isn't surfaced on the home screen. Promote the entry point and shift the channel.
View evidence →
────────────────────────────────────────────────────────────
What's broken
$2.2M margin bleed · ~9.6K calls/mo deflected
✕ FIX  |  ACTIVATION · MOBILE  |  CONFIDENCE 0.96
iPhone 15 Pro users drop off 25% at photo upload. The 48MP camera produces files that exceed the 30-second timeout.
Failure follows the device, not the OS — same iPhone 15 Pro fails on iOS 17.6, 18.1, and 18.2. Other devices on iOS 18.2 upload normally. Implement client-side image compression.
View evidence →
────────────────────────────────────────────────────────────
What's broken
$143M ARR at risk · 22% of total churn
✕ FIX  |  CHURN · PRICING  |  CONFIDENCE 0.94
Screen repair filers abandon at the deductible step (67% on app), get repair done by third-party, then cancel coverage.
Asurion's $99–$149 deductible is 2× the iFixit market price ($45–$80). Of abandoners: 71% search for third-party repair within 7 days; 54% complete it; 47% cancel coverage within 30 days. Pricing decision.
View evidence →
────────────────────────────────────────────────────────────
## Part 2 — Detailed Evidence (drill-down)
### Insight #1 — Channel migration opportunity
The headline
Filing channel is the single largest predictor of customer churn — bigger than device, carrier, age, tenure, or claim type. App-channel filers churn at 22% within 90 days. Phone-channel filers churn at 64%. The gap is 42 percentage points and holds across every demographic and behavioral cut of the data.
42% of phone-channel filers already have the app installed, and 78% of those have an app session in the last 30 days. They are active app users who simply don't use the app to file claims because File a Claim isn't surfaced on the home screen.
Convergence across sources
Why this is #1
Largest variance in the data — 42pp gap is bigger than any other single dimension
Highest leverage — affects ~30M phone claims/year
Lowest-cost fix — 2-week sprint to promote File a Claim to home
Holds uniformly across every cross-cut — no carrier, age, tenure, or device specificity
Why competing explanations don't hold
Carrier explains nothing — gap is uniform across all four carriers
Age explains nothing — overall age churn variance is <5pp
Tenure explains nothing — gap is uniform across all four tenure buckets
Device explains nothing — gap is uniform across all device models
Channel itself is the driver
Recommendation
Promote File a Claim to the app home screen as a primary CTA. Currently nested under Coverage tab. Surface on home and bottom navigation.
Impact math
Annual phone-channel claims: ~30M
42% have app installed = 12.6M addressable
Shift 20% to app: 2.52M shifted claims
Operational savings: 2.52M × ($22 - $3) = $47.9M
Retention impact (cumulative LTV): ~$148M
Conservative 12-month LTV impact: ~$15M
Verification metrics
App channel claim share — target +5pp in Q1
90-day churn for shifted phone-to-app cohort vs control
file_new_claim support call volume — target -10%
### Insight #2 — iPhone 15 Pro photo upload timeout
The headline
25% of iPhone 15 Pro users abandon their claim at the photo upload step. iPhone 15 Pro Max is even higher at 27%. No other device has this issue. The driver is the 48MP camera producing 15–22 MB photo files that exceed the 30-second upload timeout. Failure follows the device, not the OS — the same iPhone 15 Pro fails on iOS 17.6, 18.1, and 18.2 at similar rates, while other devices on the same iOS 18.2 upload normally.
Convergence across sources
Why this is hardware (48MP sensor), not software (iOS) or network
Same iPhone 15 Pro fails at 27.5% on iOS 17.6, 22% on iOS 18.1, and 28% on iOS 18.2 — failure rate is OS-independent
Other devices on iOS 18.2 (iPhone 14, iPhone 13, iPhone 15 non-Pro) succeed at 99%+
Failure rate scales with file size, which scales with device camera generation
Same pattern visible across all networks — WiFi, 5G, LTE, 4G — only iPhone 15 Pro fails on each
Conclusion: the 48MP sensor output is the cause; nothing else matters analytically
Recommendation
Implement client-side image compression in the iOS app. Compress to 2–3 MB before upload using JPEG quality 0.7. Brings duration well under 30s on all networks while preserving damage-assessment quality.
Impact math
iPhone 15 Pro share of app filings: ~10-12%
25% drop-off × addressable claims = ~120K abandoned/yr
65% convert to support calls = ~78K avoidable calls/yr
Operational savings: 78K × ($22 - $3) = $1.48M
Customer LTV preservation: ~$700K
Total recovered: ~$2.2M/yr
Verification metrics
iPhone 15 Pro upload success rate — target >97%
ERR_UPLOAD_TIMEOUT_30S volume — target baseline
photo_upload_help call volume — target -85%
### Insight #3 — Screen repair deductible drop-off → third-party repair → cancellation
The headline
67% of screen repair filers on app abandon at the deductible disclosure screen. The same screen has only ~6% drop-off for replacement claims — proving the abandonment is driven by price comparison specific to screen repair, not general friction.
The behavioral sequence: customer sees deductible ($99–$149) → abandons claim → searches for third-party repair within 7 days (71% of abandoners) → completes third-party repair within 14 days (54% of abandoners) → cancels Asurion coverage within 30 days (47% of abandoners). This single step is responsible for 22% of total monthly customer churn — the largest churn driver in the business.
Convergence across sources
Why this is pricing, not UX
The deductible screen is functioning as designed
Replacement claims at the same screen drop only ~6% — same UI, different reaction
Abandonment scales linearly with deductible amount across tiers
Abandonment is flat across age and tenure — not demographic
Reordering the flow doesn't solve the economic mismatch
Cost structure context
Total Asurion cost per screen repair: ~$85-145
Current deductible: $99-149
Implied margin: 0-30% (already low)
Lowering deductible has limited downside; recovers retention
Recommendation
Pricing strategy decision for leadership. Two options:
Option A — Lower deductible to $59 across screen repairs
Option B — Tiered loyalty pricing: 1st screen $99, 2nd within 24mo $69, 3rd $39
Option B preserves more revenue while solving the comparison problem for repeat customers.
Impact math
Annual screen repair filings (app): ~12M; 67% abandon = 8M
Of those, 47% churn vs 18% baseline = 2.32M incremental churn
LTV impact: 2.32M × $140 × 4yr tenure = $1.3B over rolling cohort
Annualized exposure: ~$143M ARR at risk
Recovery at 40% retention: ~$57M ARR
Verification metrics
A/B test segmented cohort first
30-day churn for cohort — target <25% (from 47%)
Screen repair completion rate — target >65% (from 33%)
Third-party repair signal in mobile referrer logs — target -50%
## Part 3 — Output Quality Standards
Rules an LLM should follow when generating Sprntly briefs.
### Brief card requirements
Headline tag (Double down / What's broken)
Metric strip — one-line headline numbers
Tags — domain · subdomain · confidence (0–1)
Title — one sentence stating the finding
Description — one sentence on cause + recommendation, max 2 lines
3 metrics — projected impact, scale, effort
View evidence → link
No bullet lists in the brief — narrative only
### Detailed view requirements
Headline restated with full context
Convergence table — sources + strength rating
Why this ranks — reasons it earned its rank
Why competing explanations don't hold — explicitly rule out alternatives
Recommendation — concrete, actionable, with options if relevant
Impact math — show the calculation, not just the result
Verification — measurable success criteria
### Ranking rules
Three insights only — these are the only stories the data tells
Confidence reflects strength of evidence convergence (>0.85 for all three)
Impact reflects projected $ recovered or LTV preserved
Top three must always be high-confidence AND high-impact
### What does NOT belong in the brief
Anything below 3pp variance from baseline
Anything supported by only 1 data source
Anything that's just a metric without a recommended action
Cross-check dimensions that are flat (carrier, age, tenure, OS, network)
Web channel patterns — they exist but are not elevated above other channels
Resolution time patterns — they are operational, not product-direct

[TABLE 0]
| CHURN GAP | LTV IMPACT | EFFORT |
| -42 pp | +$15M/yr | 2 weeks |

[TABLE 1]
| MARGIN RECOVERED | USERS UNBLOCKED | EFFORT |
| $2.2M/yr | ~9,600/mo | 1 sprint |

[TABLE 2]
| ARR AT RISK | CHURN SOURCE | EFFORT |
| $143M/yr | 22% of total | Pricing review |

[TABLE 3]
| Source | Signal | Strength |
| Channel_Retention | App 22% / phone 64% / retail 22% / web 23% over 6 monthly cohorts | Strong |
| Phone_Filer_App_Status | 32.8% of phone filers active in app last 7 days; 9.2% in last 30 days | Strong |
| Channel_Churn_By_Carrier | Gap holds: app 21-23% / phone 63-65% across Verizon/AT&T/T-Mobile/Direct | Strong |
| Channel_Churn_By_Device | Gap holds across all device models — no device-specific pattern | Strong |
| Channel_Churn_By_Tenure | Gap holds across 0-6mo / 6-12mo / 1-2y / 2y+ | Strong |
| App_Sessions | Users in Claims section view File a Claim button only ~38% of the time | Strong |
| Support tickets | 320/mo: "I have the app but couldn't find how to file" | Moderate |

[TABLE 4]
| Source | Signal | Strength |
| Upload_Failure_By_Device_And_OS | iPhone 15 Pro: 22-27% timeout failure across iOS 17.6/18.1/18.2; all other devices ≤1% | Strong |
| Upload_Failure_By_Device_And_OS | Avg file size 18.4 MB on iPhone 15 Pro vs 4-9 MB elsewhere | Strong |
| Upload_Failure_By_OS_Version | When iPhone 15 Pro is excluded, every iOS version is at 99%+ success — OS alone explains nothing | Strong |
| Upload_Failure_By_Network | When iPhone 15 Pro is excluded, every network is at 98.5%+ success — network alone explains nothing | Strong |
| Claim_Funnel_By_Step | screen_repair photo step drops 22.6% on app — only step elevated besides deductible | Strong |
| Support tickets | 1,240/mo: "photo upload not working" — concentrated on iPhone 15 Pro | Strong |

[TABLE 5]
| Source | Signal | Strength |
| Deductible_Drop_Off_By_Channel | App 67% / web 58% / phone 35% / retail 28% — all above baseline; app highest because self-service | Strong |
| Deductible_Abandonment_By_Tier | Linear relationship: Tier 1 22% / Tier 2 60% / Tier 3 70% / Tier 4 72% | Strong |
| Deductible_Abandonment_By_Age | Flat across age — proves age is not the driver | Strong |
| Deductible_Abandon_By_Tenure | Flat across tenure — proves tenure is not the driver | Strong |
| Post_Abandonment_Outcome | 71% search third-party in 7d; 54% complete repair in 14d; 47% cancel in 30d | Strong |
| Churn_By_Drop_Step | Drop-off cohort 30-day churn 47% vs 18% baseline; 22% of total monthly churn | Strong |
| Support tickets | 890/mo explicit deductible objections on screen claims | Strong |
| Competitive pricing | iFixit $59-89, local shops $45-120, Asurion $99-149 | Strong |