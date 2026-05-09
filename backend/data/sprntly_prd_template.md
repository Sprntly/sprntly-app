# [Product Area] — [Feature/Solution Name] PRD
Replace bracket text. Keep the title under 12 words. Format: "[Surface] — [What we're shipping]". Examples: "Mobile App — File-a-Claim home screen entry" or "Claims flow — Client-side image compression".
## TL;DR
Three sentences max. Sentence 1: the problem. Sentence 2: the proposed fix. Sentence 3: the projected impact (with $ or % attached). A senior reading only this should know whether to read the rest.
[Problem in one sentence.] [Proposed fix in one sentence.] [Projected impact in one sentence with concrete numbers.]
Status: [Draft / In Review / Approved / Shipped]
Author: [Name]
Reviewers: [Names]
Last updated: [Date]
────────────────────────────────────────────────────────────
## 1. Context
Set the stage. Why are we looking at this now? What does the reader need to know about the business, the surface, and the customer to make sense of everything below? Two short paragraphs maximum. Do not explain the problem yet — that's section 2. Just orient.
[1–2 paragraphs. Describe the relevant product surface, customer segment, and what's true about the world today. End with a single line: "This PRD proposes [one-sentence intent]."]
────────────────────────────────────────────────────────────
## 2. Problem Framing
### 2a. User problem
Frame the problem from the user's point of view. What are they trying to do? Where does the experience fail them? What's the emotional or practical cost? Use the user's language. Avoid product jargon. One short paragraph.
[The user is trying to [goal]. They run into [friction] which causes [pain]. As a result, [behavioral consequence].]
### 2b. Business problem
Translate the user problem into business terms. This is the cost of leaving the user problem unsolved. Quantify wherever possible: dollars, percentage points of churn, support volume, NPS, retention. If you can't quantify yet, name the metric and flag the gap.
[The user problem causes [business impact] measured as [metric]. Current value: [number]. If unsolved, projected annual cost: [$ or %].]

────────────────────────────────────────────────────────────
## 3. Insights & Analysis
3 to 4 data cuts that build the case. Each cut should reveal something the previous one didn't. Together they should converge on a single hypothesis. Avoid restating the same point with different numbers.
For each cut: a one-sentence finding, the supporting data, and what it rules in or out. Cuts that prove the negative ("X is NOT the cause") are as valuable as cuts that prove the positive.
### Cut 1: [Headline finding]
[1–2 sentences. The signal in the data, with the number.]
Source: [analytics sheet / table / dashboard]
Rules in: [hypothesis this supports]. Rules out: [hypothesis this eliminates].
### Cut 2: [Headline finding]
[Different angle on the same problem. Often a cross-cut that controls for a variable, or a comparison to a baseline cohort.]
### Cut 3: [Headline finding]
[Often the qualitative or causal-chain cut: post-event behavior, support tickets, exit-survey reasons, social mentions.]
### Cut 4 (optional): [Headline finding]
[Cross-check showing the explanation we're NOT going with. e.g., "Carrier doesn't explain it," "OS version doesn't explain it."]
### Hypothesis converged
One sentence stating the single explanation the data supports.
[Based on the cuts above, [user behavior] is caused by [root cause], not [alternative explanations].]
────────────────────────────────────────────────────────────
## 4. Data Sources
Every source referenced above. Anyone reading this should be able to reproduce the analysis. Include both quantitative (analytics/Salesforce/Amplitude) and qualitative (Zendesk/Gong/App Store) sources.

────────────────────────────────────────────────────────────
## 5. Hypothesis & Success Measures
State the hypothesis as an if-then-because chain. Then define what success looks like in measurable terms. The success measures are what we'll evaluate against in section 8 (metrics).
### Hypothesis
If we [proposed change], then [observable user behavior change], because [causal mechanism from section 3]. This will move [primary metric] from [current] to [target].
### Success measures
What we'll observe if the hypothesis is correct. Be specific.
[Primary signal — e.g., "Photo upload success rate on iPhone 15 Pro reaches >97%"]
[Secondary signal — e.g., "photo_upload_help support call volume drops by >70%"]
[User-facing outcome — e.g., "Claim completion rate for affected cohort matches non-affected cohort"]
────────────────────────────────────────────────────────────
## 6. Scope
Most PRDs fail because scope was never written down. List what's in. List what's explicitly out. The "out" list is the more important one.
### In scope
[Component 1]
[Component 2]
[Surface or platform — e.g., iOS app only, not Android]
### Out of scope (and why)
[Item 1] — [reason; e.g., "separate workstream owned by team Y"]
[Item 2] — [reason]
────────────────────────────────────────────────────────────
## 7. Solution — Experience
This is the build. Break the solution into discrete components — each component gets its own block below. An engineer should be able to pick up any single component block and start work.
Use as many sub-sections as the solution has components. Number them 7.1, 7.2, 7.3, etc.
### 7.1 [Component name — e.g., "Home screen File-a-Claim CTA"]
Description: [1–2 sentences on what this component does]
User flow: [Numbered steps the user takes through this component, written from their POV]
Example: "1. User opens app → 2. Sees File-a-Claim button as primary CTA on home → 3. Taps → 4. Lands on claim type selector"
UI / interaction: [Describe the visible elements, where they live, what they look like, what triggers state changes. Reference Figma frames where relevant.]
Data model changes: [New fields, new tables, new events to log, deprecations. If none, write "None."]
Backend / API changes: [New endpoints, modified responses, auth or rate-limit considerations. If none, write "None."]
### Acceptance criteria for 7.1
Each criterion is a Given/When/Then statement. Engineering treats these as the contract for completion.

### 7.2 [Next component]
Repeat the same structure for each additional component. Keep blocks self-contained — an engineer should not need to read 7.1 to understand 7.2.
[Description, user flow, UI, data model, API, acceptance criteria.]
────────────────────────────────────────────────────────────
## 8. Primary & Secondary Metrics + Guardrails
Three categories. Primary metric = the one we're trying to move. Secondary metrics = leading indicators that primary will move. Guardrails = metrics that must NOT degrade as a side effect.

────────────────────────────────────────────────────────────
## 9. Test Plan
How we'll validate this works in production. Distinct from acceptance criteria (which are about component completeness) — this is about rollout and learning.
### Pre-launch validation
[Internal dogfood — duration, target user count, exit criteria]
[Beta / TestFlight cohort — duration, target user count, exit criteria]
### Rollout
[A/B test design — control vs treatment, sample size, MDE on primary metric, duration]
[Rollout schedule — e.g., 1% → 10% → 50% → 100% with health checks]
[Kill criteria — what would cause us to roll back]
### Post-launch monitoring
[Dashboard owner + cadence]
[Review milestone — e.g., 30-day post-launch retro with primary/secondary/guardrail check]
────────────────────────────────────────────────────────────
## 10. Risks & Mitigations
List the things most likely to go wrong. For each, what we'll do about it.

────────────────────────────────────────────────────────────
## 11. Non-Functional Requirements
Performance, reliability, accessibility, security, privacy. List concrete numeric requirements where applicable.
Performance: [e.g., "P95 upload latency under 5s on LTE"]
Reliability: [e.g., "99.9% upload success rate excluding transient network drops"]
Accessibility: [e.g., "WCAG 2.1 AA — all new CTA must have screen reader labels"]
Security: [e.g., "PII handling unchanged; no new auth surface"]
Privacy: [e.g., "No new user data collected; existing consent flow applies"]
Compliance: [e.g., "SOC 2 controls unchanged"]
────────────────────────────────────────────────────────────
## 12. Technical Design
High-level architecture. Engineers will write the detailed RFC; this section is the PRD-side input. Cover the components touched, the data flow, the integrations, and any platform/library decisions.
### Components touched
[e.g., iOS Asurion app — claim filing module]
[e.g., Backend service — uploads-api]
[e.g., S3 bucket — claims-photos]
### Data flow
Plain prose or simple diagram description. Focus on what's new or changed, not the entire stack.
[e.g., "Photo captured → client-side compression (NEW) → existing upload-api → S3 → claim-processing service. No backend changes required."]
### Key technical decisions

### Open technical questions
[Question 1]
[Question 2]
────────────────────────────────────────────────────────────
## 13. Dependencies & Rollout Sequencing
What has to happen and in what order. Useful when shipping anything that crosses team boundaries.
[Dependency 1 — e.g., "Claims review team must approve compression quality threshold before launch"]
[Dependency 2]
[Sequence — e.g., "Phase 1: ship compression behind flag → Phase 2: enable for iPhone 15 Pro only → Phase 3: rollout to 100%"]
[Feature flags — e.g., "image_compression_enabled"]

# Worked Example — iPhone 15 Pro Photo Upload
This is a fully filled example using the Asurion Insight #2 work. Use it as a reference for tone, depth, and what "done" looks like in each section.
## TL;DR
iPhone 15 Pro users abandon 25% of claim filings at the photo upload step because the 48MP camera produces 15–22 MB files that exceed our 30-second upload timeout. We'll add client-side image compression to the iOS app to bring all upload payloads under 5 MB. Projected impact: $2.2M/yr in recovered margin and ~9,600 deflected support calls per month.
Status: Draft
Author: Mobile App PM
Reviewers: Mobile Eng Lead, Claims Ops, Customer Support
Last updated: May 8, 2026
────────────────────────────────────────────────────────────
## 1. Context
Asurion processes ~50M device protection claims per year globally. The mobile app is the most efficient filing channel: app-filed claims cost ~$3 to service vs ~$22 for phone, and app-filed customers retain dramatically better. Photo upload is a required step for screen and physical damage claims (about 60% of all claim types). The current upload pipeline has a 30-second server-side timeout and does not apply client-side compression.
Apple launched the iPhone 15 Pro and iPhone 15 Pro Max in late 2023 with a new 48MP main camera that produces significantly larger photo files than prior generations. These devices now make up roughly 11% of our app filing volume.
This PRD proposes adding client-side image compression in the iOS Asurion app to recover claim completion for iPhone 15 Pro users.
────────────────────────────────────────────────────────────
## 2. Problem Framing
### 2a. User problem
A customer with a cracked screen on their iPhone 15 Pro opens the Asurion app, navigates to file a claim, takes a photo of the damage, and taps Upload. The progress spinner runs for 30+ seconds and then silently fails — the app returns to the home screen with no error message. They retry and it fails again. They give up on the app and call the 1-800 number, where they wait on hold for 25 minutes to speak to an agent who files the same claim by phone in 8 minutes. The customer ends the experience frustrated, having lost an hour to a feature that should have taken 90 seconds.
### 2b. Business problem
23% of iPhone 15 Pro app-filed claims are abandoned at the photo upload step. Of those, 65% convert to support calls (each costing ~$22 to service vs $3 for an app-completed claim). The remaining abandoners either retry until they succeed (acceptable) or churn (unacceptable). The pattern is concentrated entirely on iPhone 15 Pro hardware — every other device uploads at >98% success.

────────────────────────────────────────────────────────────
## 3. Insights & Analysis
### Cut 1: 23% of iPhone 15 Pro app filings abandon at photo upload
From the funnel data, the photo_uploaded step on screen_repair claims drops 22.6% on app overall — and slicing by device shows iPhone 15 Pro at 24.6% and iPhone 15 Pro Max at 27.4% timeout failure. All other devices are at <1%.
Source: Upload_Failure_By_Device_And_OS sheet.
Rules in: device-specific upload friction. Rules out: claim-flow UX, server outage.
### Cut 2: Failure follows the device, not the OS version
iPhone 15 Pro fails at 22-27% across iOS 17.6, 18.1, and 18.2 — three different OS versions, similar failure rate. Conversely, iOS 18.2 across other devices (iPhone 14, iPhone 13, Samsung Galaxy S24) all succeed at >98%. When iPhone 15 Pro is excluded, iOS 18.2's overall success rate is 99.2%, identical to older iOS versions.
Source: Upload_Failure_By_OS_Version sheet.
Rules in: hardware (48MP sensor file size) as the cause. Rules out: iOS regression.
### Cut 3: File size correlates 1:1 with timeout failure
Average iPhone 15 Pro photo file size is 18.4 MB. At our cellular network upload speeds (1.4–3.5 sec/MB on LTE/4G), this puts duration at 26–64 seconds — frequently exceeding the 30-second timeout. Other devices produce 4–9 MB files, which complete well under timeout on every network type.
Source: Photo_Upload_Events raw + Upload_Failure_By_Network sheet.
Rules in: file size as the mechanism. Rules out: network-specific issue (network is fine for everyone else on the same network).
### Cut 4: Qualitative signals confirm — 1,240 monthly support tickets explicitly cite photo upload
Zendesk theme analysis shows 1,240 monthly support tickets matching "photo upload not working" patterns, up 340% YoY since the iPhone 15 Pro launch. App Store reviews show ~340 one-star reviews citing the same issue. Customer language consistently mentions iPhone 15 Pro specifically — never iPhone 15 (non-Pro) or earlier.
Source: Zendesk + App Store reviews + Gong call transcripts.
### Hypothesis converged
iPhone 15 Pro upload failures are caused by 48MP camera output exceeding the server-side 30-second upload timeout. Compressing photos client-side before upload will eliminate the failure mode for all devices and all networks.
────────────────────────────────────────────────────────────
## 4. Data Sources

────────────────────────────────────────────────────────────
## 5. Hypothesis & Success Measures
### Hypothesis
If we add client-side image compression to the iOS Asurion app (target output 2–3 MB at JPEG quality 0.7), then iPhone 15 Pro upload success rate will improve to >97%, because the resulting payload will complete well under the 30-second timeout on every network type. This will move iPhone 15 Pro photo-upload abandonment from 25% to <3% (matching baseline).
### Success measures
iPhone 15 Pro upload success rate reaches >97% (from 73%)
photo_upload_help support call volume drops by >70% (from 9,600/mo)
ERR_UPLOAD_TIMEOUT_30S error volume drops to <100/mo (from ~12K/mo)
iPhone 15 Pro screen-repair claim completion rate matches the all-device baseline within 2 pp
────────────────────────────────────────────────────────────
## 6. Scope
### In scope
iOS Asurion app — claim filing module
Client-side compression for all photo uploads in claim flows (screen_repair and replacement_damage)
Telemetry to verify compression is applied and to measure file size before/after
### Out of scope (and why)
Android app — Android device camera files are smaller and not affected; separate workstream if needed
Web portal — different upload pipeline; smaller affected volume
Server-side timeout increase — would solve symptom but not file size at scale
Compression for non-claim uploads (profile photos, etc.) — separate review
────────────────────────────────────────────────────────────
## 7. Solution — Experience
### 7.1 Client-side image compression in claim flow
Description: After the user takes or selects a photo for a claim, compress the image to ≤3 MB at JPEG quality 0.7 before invoking the upload API. No visible UI change to the user.
User flow: 1. User reaches photo upload step in claim flow → 2. User taps Take Photo or Choose from Library → 3. User selects/captures photo → 4. App displays compressed thumbnail in upload preview (existing UI) → 5. User taps Upload → 6. Compressed image uploads via existing API → 7. Server processes successfully.
UI / interaction: No visible change. The Upload button, progress indicator, and confirmation states remain identical to today's UX. The thumbnail in the preview will be slightly lower resolution but visually indistinguishable at thumbnail size.
Data model changes: None on server. New client-side telemetry events: photo_compression_started, photo_compression_completed (with original_size_mb, compressed_size_mb, duration_ms).
Backend / API changes: None. Existing /uploads endpoint accepts smaller payloads natively.
### Acceptance criteria for 7.1

────────────────────────────────────────────────────────────
## 8. Primary & Secondary Metrics + Guardrails

────────────────────────────────────────────────────────────
## 9. Test Plan
### Pre-launch validation
Internal dogfood — Asurion employees with iPhone 15 Pro / Pro Max devices for 1 week. Exit: zero P0/P1 bugs
Beta TestFlight — 5,000 external users including ~600 iPhone 15 Pro for 2 weeks. Exit: upload success rate >97% on iPhone 15 Pro, no guardrail regressions
### Rollout
A/B test design — 50/50 control vs treatment on iOS users with successful claim filings as outcome metric. Sample size 100K (50K each), MDE 3pp on completion rate, duration 14 days
Rollout schedule — 1% → 10% → 50% → 100% over 14 days with daily health checks
Kill criteria — guardrail breach (claims reviewer rejection up >1pp, OR app crash rate up >5%) triggers immediate rollback via feature flag
### Post-launch monitoring
Mobile Analytics dashboard — daily cadence for first 30 days
30-day retro — full primary/secondary/guardrail check; decision on whether to extend compression to Android
────────────────────────────────────────────────────────────
## 10. Risks & Mitigations

────────────────────────────────────────────────────────────
## 11. Non-Functional Requirements
Performance: P95 compression latency <500ms on devices iPhone 11 and newer; <1s on older devices
Reliability: 99.9% compression success (excluding system-level memory failures)
Accessibility: No new UI elements; existing accessibility labels unchanged
Security: Compression happens in-process; no PII written to disk beyond existing temporary upload cache
Privacy: No new user data collected; existing image-handling consent applies
Compliance: SOC 2 controls unchanged; image compression is industry-standard transformation
────────────────────────────────────────────────────────────
## 12. Technical Design
### Components touched
iOS Asurion app — claim filing module (ClaimPhotoUploadViewController)
iOS Asurion app — analytics layer (new compression telemetry events)
### Data flow
Photo captured via UIImagePickerController → NEW compression step (UIImageJPEGRepresentation, quality 0.7, target ≤3 MB with iterative quality reduction if needed) → existing /uploads API → S3 → claims-processing service. No backend or API changes required.
### Key technical decisions

### Open technical questions
Confirm whether claims-processing service has any minimum resolution validation that compression might fail (Eng to verify week of 5/12)
Decide on android compression workstream timing — separate PRD or follow-up phase
────────────────────────────────────────────────────────────
## 13. Dependencies & Rollout Sequencing
Claims operations review — claims reviewers must approve quality factor 0.7 as sufficient for damage assessment (1 week, week of 5/12)
Mobile platform — feature flag plumbing for `image_compression_enabled` and `compression_quality_factor` (already exists; no work)
Phase 1 — ship behind flag, default off (week of 5/19)
Phase 2 — enable for 1% → 10% iPhone 15 Pro users only (week of 5/26)
Phase 3 — A/B test full iOS user base (50% treatment, week of 6/2)
Phase 4 — 100% rollout to iOS (target 6/16)
Phase 5 — Android workstream kickoff (separate PRD; target Q3)

[TABLE 0]
| Dimension | Quantified impact |
| Affected user volume | [# users / month] |
| Cost per affected user | [$ or churn pp] |
| Annualized business cost | [$X / yr] |
| Comparison to alternative | [benchmark or status quo] |

[TABLE 1]
| Source | What it provided | Owner / link |
| [e.g., Amplitude — Claims funnel] | [Step-by-step drop-off rates by claim type] | [link or DRI] |
| [e.g., Salesforce — Churn cohorts] | [30/90/180-day churn by filing channel] | [link] |
| [e.g., Zendesk — Support tickets] | [Monthly ticket volume by theme] | [link] |
| [e.g., Gong — Call transcripts] | [Recurring complaint themes from Q1] | [link] |

[TABLE 2]
| # | Criterion | Verified by |
| AC1 | [Given X, when user does Y, then Z observable behavior] | [QA / unit test / integration test] |
| AC2 | [Given X, when user does Y, then Z] | [verification method] |
| AC3 | [Edge case — e.g., offline mode, low memory, slow network] | [verification method] |

[TABLE 3]
| Category | Metric | Current | Target | Why it matters |
| Primary | [e.g., 90-day churn for affected cohort] | [X%] | [Y%] | [Direct measure of hypothesis success] |
| Secondary | [e.g., Claim completion rate] | [X%] | [Y%] | [Leading indicator] |
| Secondary | [e.g., Time-to-claim-submit] | [X min] | [Y min] | [Leading indicator] |
| Guardrail | [e.g., App crash rate] | [X%] | [≤ X%] | [Must not regress] |
| Guardrail | [e.g., Support call volume — non-target reasons] | [X/mo] | [≤ X/mo] | [Avoid displacement to other channels] |

[TABLE 4]
| Risk | Likelihood | Impact | Mitigation |
| [Specific risk — e.g., "Compression artifacts cause photo rejection by claims reviewers"] | Med | High | [Mitigation — e.g., "Quality factor 0.8 not 0.7; reviewer audit on first 1,000 compressed photos"] |
| [Risk 2] | Low/Med/High | Low/Med/High | [Mitigation] |
| [Risk 3] | Low/Med/High | Low/Med/High | [Mitigation] |

[TABLE 5]
| Decision | Chosen approach | Alternative considered | Why |
| [e.g., Compression library] | [e.g., native iOS UIImageJPEGRepresentation] | [e.g., third-party Mozjpeg lib] | [e.g., No new dependency, native performance] |
| [Decision 2] | [Chosen] | [Alternative] | [Reason] |

[TABLE 6]
| Dimension | Quantified impact |
| Affected user volume | ~120,000 iPhone 15 Pro app filings/yr abandoned at upload |
| Cost per affected user | $19 incremental service cost (phone vs app channel) |
| Support call deflection opportunity | ~78,000 calls/yr ($1.48M) |
| LTV preservation from abandoned-then-churned cohort | ~$700K/yr |
| Annualized business cost (current) | ~$2.2M/yr |

[TABLE 7]
| Source | What it provided | Owner / link |
| Amplitude — claim_filing funnel | Step-by-step drop-off rates by claim type, channel, and device | Mobile Analytics team |
| Asurion analytics workbook | Upload events, failure codes, device × OS × network breakdowns | Data Eng / asurion_analytics.xlsx |
| Sentry — iOS app errors | ERR_UPLOAD_TIMEOUT_30S volumes | Mobile Eng |
| Zendesk — support tickets | Monthly volume by theme; iPhone 15 Pro mentions | Customer Support Ops |
| Gong — support call transcripts | Photo upload help reason — handle time, device mentioned | Sales Ops |
| App Store reviews (iOS) | Negative review clustering and device mentions | Product |

[TABLE 8]
| # | Criterion | Verified by |
| AC1 | Given an iPhone 15 Pro photo of any size, when the user taps Upload, then the uploaded payload is ≤3 MB | Integration test on iPhone 15 Pro device |
| AC2 | Given any device, when the user taps Upload, then compression completes in <2 seconds (P95) | Performance test across device matrix |
| AC3 | Given a compressed photo viewed at full size by claims reviewer, when reviewer assesses damage, then no degradation in reviewer ability to assess damage (qualified by claims ops review) | Reviewer audit on first 1,000 compressed photos |
| AC4 | Given the upload fails for any reason, then the user sees an error state with retry CTA (vs current silent failure) | QA test |
| AC5 | Given offline mode, when the user attempts upload, then the photo is queued locally and uploaded on reconnection | Offline scenario test |

[TABLE 9]
| Category | Metric | Current | Target | Why it matters |
| Primary | iPhone 15 Pro upload success rate | 73% | >97% | Direct measure of fix effectiveness |
| Secondary | iPhone 15 Pro screen-repair claim completion rate | ~50% | ≥70% | Captures full funnel improvement |
| Secondary | photo_upload_help support call volume | 9,600/mo | <3,000/mo | Indicates user experience improved |
| Secondary | ERR_UPLOAD_TIMEOUT_30S Sentry volume | ~12,000/mo | <100/mo | Direct technical signal |
| Guardrail | Claims reviewer rejection rate | Baseline | Within 1pp of baseline | Compression must not degrade damage assessment |
| Guardrail | App crash rate (iOS) | Baseline | Within 5% of baseline | Compression library must not destabilize app |
| Guardrail | P95 photo upload latency (all devices) | Baseline | ≤ baseline | Compression must not slow down devices that don't need it |

[TABLE 10]
| Risk | Likelihood | Impact | Mitigation |
| Compression artifacts cause photo rejection by claims reviewers | Med | High | Quality factor 0.7 is conservative; reviewer audit on first 1,000 compressed photos before full rollout; quality factor adjustable via feature flag |
| Compression takes too long on older devices, degrading experience | Low | Med | Native iOS compression takes <500ms even on iPhone 11; performance gated in CI |
| Edge case: photos already small (e.g., screenshots) double-compressed and degrade | Med | Low | Skip compression if input is already <3 MB |
| Customer complaints about compressed photos appearing low-quality | Low | Low | Compression invisible at thumbnail size; full-size view is for reviewer only |

[TABLE 11]
| Decision | Chosen approach | Alternative considered | Why |
| Compression library | Native iOS UIImageJPEGRepresentation | Third-party Mozjpeg via SwiftPM | No new dependency, native performance, smaller binary size |
| Compression target size | 3 MB max | 5 MB max | 3 MB completes in <10s on every network including 4G with 2x safety margin |
| Quality factor default | 0.7 | 0.5 / 0.8 / 0.9 | 0.7 balances damage-assessment quality (validated with claims reviewers) and file size |
| Feature flag scope | Full kill switch + quality factor adjustable remotely | Static compile-time config | Allows mitigation without app store update |