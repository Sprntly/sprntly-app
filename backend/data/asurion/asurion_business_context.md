# Asurion — Business Context
Business, operational, and product context for Asurion. Primary reference for any analysis or product strategy work.
## 1. Company Overview
Company: Asurion, LLC
Founded: 1994
HQ: Nashville, Tennessee
Reach: 290 million consumers globally
Employees: ~19,000
Estimated Annual Revenue: $2–3 billion
Core Business: Device protection insurance, tech support, and repair services for consumer electronics
## 2. Customer Segments
Smartphone users via carrier bundles (Verizon, AT&T, T-Mobile) — dominant segment
Direct consumers via Asurion Home+ for whole-home appliance protection
Retailer-bundled customers (Amazon, Walmart, Best Buy)
Business customers buying fleet device protection
Device mix: iPhone ~55%, Samsung ~30%, other Android ~15%
## 3. Sales and Distribution Channels

## 4. Revenue Model

### Operational cost per claim by channel
App-filed claim: ~$3 to service end-to-end
Phone-filed claim: ~$22 to service (call handler labor + IVR)
Retail walk-in: ~$8 to service
Web-filed claim: ~$13 to service
Channel mix is one of the largest controllable levers in the business. Each percentage point shifted from phone to app saves ~$19 per claim.
## 5. Product and Service Workflow
### Step 1: Enrollment
Customer signs up at point of phone purchase, online during plan setup, or directly via Asurion.
### Step 2: Claim filing
Customer chooses a channel: mobile app, phone, uBreakiFix walk-in, or web portal.
### Step 3: Filing flow within app or web
Identity verification (phone number, account login)
Device identification
Damage description
Photo upload (required for screen and physical damage claims)
Deductible disclosure ($99–$250 shown to user)
Payment (deductible charged via stored payment method)
Claim confirmation
### Step 4: Claim processing and fulfillment
Replacement claim: refurbished device shipped
Same-unit repair: device sent to repair facility
Walk-in repair: customer brings device to uBreakiFix for same-day fix
### Step 5: Resolution
Customer receives replacement or repaired device. Coverage continues unless cancelled.
## 6. Mobile App — Technical Surface
iOS and Android native apps
Primary digital filing channel
Native camera integration for photo uploads
Push notifications for claim status updates
Home screen prominently displays Coverage, Account, Help
File a Claim is currently nested under the Coverage tab — not a primary home screen CTA
Image upload pipeline accepts photos directly from device camera
Image upload pipeline does NOT currently apply client-side compression before upload
Server-side upload timeout: 30 seconds (industry-standard but inadequate for newer iPhone camera files)
Photo file size at point of capture varies by device generation:
  iPhone 15 Pro / Pro Max: 15–22 MB (48MP main camera)
  iPhone 15 / earlier: 4–8 MB
  Samsung Galaxy flagship: 5–7 MB
  Other Android: 3–6 MB
## 7. Web Portal — Technical Surface
Available at asurion.com and via carrier-branded URLs
Same claim filing flow as mobile app
Receives status updates via email and SMS
Customer can log back in to check claim status
## 8. Competitive Landscape

### Pricing comparison — screen repair (iPhone 15 Pro)
Asurion deductible: $99–$149 (plus monthly premium)
AppleCare+ deductible: $29 (plus monthly fee)
iFixit DIY kit: $59–$89
Local repair shops: $45–$120
uBreakiFix walk-in (no insurance): $249–$299
## 9. Cost Structure for Screen Repair
Replacement screen part: $45–$80
Labor (uBreakiFix technician): $25–$40
Logistics and overhead: $15–$25
Total cost per screen repair: ~$85–$145
Current deductible revenue: $99–$149
Implied gross margin per repair: 0–30%
## 10. Customer Behavior Patterns
### Screen repair abandonment → third-party repair → cancellation
Customers abandoning a screen repair claim at the deductible disclosure step exhibit a clear behavioral sequence:
Abandon claim flow when they see the deductible amount
Search for third-party repair within 7 days (iFixit, local shops, uBreakiFix walk-in without insurance)
Get the repair completed elsewhere within 14 days (~54% of abandoners do this)
Cancel Asurion coverage within 30 days (~47% of abandoners do this)
Exit-survey cancellation reasons most common in this cohort: "I can fix it cheaper elsewhere," "Deductible is too high," "Better off without coverage."
## 11. Key Operational Facts
Approximately 50 million claims per year globally
Approximately 60% phone, 30% app, 6% retail, 4% web
Average phone support call duration: 8–12 minutes per claim
Customer LTV averages ~$140 per year per active subscriber
Customer acquisition cost via carrier channels: ~$85
Average customer tenure: ~3.2 years
## 12. Internal Data Platforms
Salesforce: customer records, billing, churn cohorts, carrier contract data
Amplitude: product analytics for mobile app and web (events, funnels, drop-off)
Sentry: error and crash reporting
Zendesk: support ticket management
Gong: call center transcript recording and analysis
Internal claims processing system (proprietary)
## 13. Glossary
Claim: A request from a customer for replacement or repair
Deductible: Customer fee per claim ($99–$250 by device tier)
Tier: Device classification used for premium and deductible amount
Carrier-channel filer: Sold via Verizon/AT&T/T-Mobile; files via support phone
App-channel filer: Files via the Asurion mobile app or carrier-branded app
Web-channel filer: Files via desktop or mobile web browser
uBreakiFix: Asurion-owned retail repair chain; 500+ US locations
Refurbished replacement: Previously-used device, refurbished and tested
LTV: Customer lifetime value — projected total revenue over tenure
Churn: Customer cancellation; tracked monthly and by cohort
Filing channel: Path used to file: app, phone, web, retail
Drop-off step: Step in the claim filing flow where a user abandoned
Third-party repair: Repair done outside the Asurion claim flow at iFixit, local shop, or uBreakiFix walk-in without insurance

[TABLE 0]
| Channel | Share | Notes |
| Carrier bundles | ~60% | Largest channel; Verizon, AT&T, T-Mobile |
| Retail partner bundles | ~20% | Amazon, Walmart, Best Buy white-label |
| Direct-to-consumer | ~12% | Asurion.com, Asurion Home+ |
| uBreakiFix retail | ~8% | 500+ US locations |

[TABLE 1]
| Stream | How | Margin |
| Monthly premiums | $5–$17 per device per month | 40–50% |
| Claim deductibles | $99–$250 per claim | Variable — see cost structure |
| Replacement device markup | Refurbished sourced wholesale | 30–40% |
| uBreakiFix repair labor | Walk-in fees + Asurion-routed | ~50% |
| Service / handling fees | Shipping, diagnostics | ~60% |

[TABLE 2]
| Competitor | Type | Notes |
| AppleCare+ | Manufacturer-direct | Apple-only; bundled at iPhone purchase |
| Samsung Care+ | Manufacturer-direct | Samsung-only |
| Allstate Mobile / SquareTrade | Third-party | Lower-priced; some carrier integrations |
| iFixit and local repair shops | DIY / third-party repair | Repair-only; popular for screens at $45–$89 |
| uBreakiFix walk-in (no insurance) | Asurion-owned retail repair | Same locations as Asurion-routed; priced as third-party |