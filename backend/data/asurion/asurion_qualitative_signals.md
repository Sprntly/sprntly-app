# Asurion — Qualitative Signals & Non-Analytics Channels
Customer feedback, support patterns, social listening, and competitive benchmarks. Three themes have elevated signal — they map directly to the three product insights.
## 1. Support Tickets (Zendesk)
Aggregated themes over the last 6 months. Volumes are monthly averages.

### Theme: Photo upload failures (Insight #2)
Concentrated almost entirely on iPhone 15 Pro and iPhone 15 Pro Max users. Reported across all iOS versions on these devices.
"Tried to file a claim through the app for my cracked iPhone 15 Pro screen. The app sat there for like 45 seconds trying to upload the photo and then just went back to the home screen with no error message."
"App is broken. Take a photo, hit upload, spinner forever. I'm on AT&T 5G, this should not be a problem. iPhone 15 Pro Max."
"My iPhone 15 Pro on iOS 17.6 — same problem as my coworker's 18.2. It's the phone, not the OS."
### Theme: Deductible objection on screen repair (Insight #3)
Concentrated on screen repair claims. Customers explicitly compare to third-party repair pricing.
"$120 to fix a cracked screen? iFixit charges $59 for the same repair. What am I paying you for?"
"You want me to pay $99 deductible plus my monthly premium for a screen replacement that costs $45 at any third-party shop? Cancel my coverage."
"Saw the deductible, walked to the repair shop down the street, paid $79, called Asurion to cancel."
### Theme: Can't find File a Claim button (Insight #1)
App users who installed the app but defaulted to phone because the filing entry point isn't visible enough.
"I have the app on my phone. I opened it to file a claim and could not find a clear way to start one. Eventually gave up and called."
"The Coverage tab shows my plan but doesn't have a Start a Claim button. I had to scroll around for 5 minutes before I just gave up."
"Honestly didn't realize the app could file claims until the support agent told me. I always just call."
## 2. App Store Reviews
Average rating: 4.4 stars overall. Negative reviews concentrate in two clusters that match the top insights.
### Cluster A: Photo upload (Insight #2) — ~340 negative reviews
"Used to work great. Now I can't even file a claim because the app won't accept photos from my new iPhone 15 Pro Max. One star until you fix this."
"App is buggy. Tried to file a claim three times. Each time the photo upload fails silently on my iPhone 15 Pro."
"Same iPhone 15 Pro problem on iOS 18.2 and 17.6 — it's not the OS update, it's the phone's camera files being too big."
### Cluster B: Screen repair deductible (Insight #3) — ~215 negative reviews
"Premium is reasonable but the moment you actually need to use the insurance for a screen, the deductible is more than just paying for the repair yourself. Useless."
"Just downloaded the app to file a claim and almost choked when I saw the deductible. $149 for a screen??? Going to iFixit instead."
"The math only makes sense if your phone gets stolen, not for normal screen repairs."
## 3. Social Media and Forum Mentions
### Reddit (r/Asurion, r/iphone, r/insurance)
New iPhone Pro users complaining about photo upload failures (Insight #2)
Comparison of Asurion screen repair deductible to iFixit (Insight #3)
Phone-channel filers mentioning they have the app installed but didn't use it for the claim (Insight #1)
### Sample Reddit excerpts
"I've been paying Asurion through Verizon for 4 years. Cracked my screen on my iPhone 15 Pro and tried to file a claim. The app would not upload the photo. Drove to uBreakiFix and walked out an hour later for $89 out of pocket."
"For screen repairs, third-party is always cheaper than the deductible. Save the protection for actual replacements. Then I cancelled."
"I have the app installed but I always just call. Never realized I could file from the app until last week."
## 4. Sales and Support Call Transcripts (Gong)
### Recurring themes ranked by call volume
"App won't upload my photo" — concentrated on iPhone 15 Pro users (Insight #2)
"Deductible is too high" — concentrated on screen repair claims (Insight #3)
"I have the app but it was easier to call" — phone-channel filers (Insight #1)
### Sample call excerpts
"Customer: I tried to upload a picture of my screen three times and it just kept failing. Agent: What kind of phone? Customer: iPhone 15 Pro Max."
"Customer: Why is the deductible $129 when I can get it fixed at the place down the street for $80? Agent: I understand, sir. Customer: This isn't worth it. Cancel."
"Agent: Did you try filing through the app? Customer: I have the app, but I just figured calling would be faster."
## 5. Competitive Pricing Benchmarks
### Screen repair pricing — iPhone 15 Pro

The pricing comparison is sharpest for screen repair. Asurion's deductible is roughly 2–3x the price of comparable third-party repair, and the customer is also paying a monthly premium on top. This is the comparison customers make when they see the deductible disclosure in the app.
## 6. Customer Behavior — Post-Abandonment Sequence (Insight #3)
For screen repair filers who abandon at the deductible step, the post-abandonment behavior follows a consistent sequence. This pattern is observed in mobile app referrer logs, third-party repair partner data, IMEI lookup at uBreakiFix walk-in, and Salesforce subscription cancellation events.
Stages of the sequence:
100% — abandoned at deductible disclosure
71% — searched for third-party repair within 7 days (iFixit, local shop, uBreakiFix walk-in)
54% — confirmed third-party repair completed within 14 days
47% — cancelled Asurion coverage within 30 days
62.5% — cancelled Asurion coverage within 90 days (cumulative)
Exit-survey cancellation reasons most common in this cohort: "I can fix it cheaper elsewhere" (38%), "Deductible is too high" (24%), "Better off without coverage" (18%), other (20%).
## 7. Internal Team Observations
### Customer Success team
Repair claims drive disproportionate cancellation requests (Insight #3)
Phone-channel filers often mention having the app installed during the call (Insight #1)
Cancellation cohort from screen repair abandonment is the single largest churn driver in the business
### Operations team
Photo-upload-related calls jumped to top 3 contact reasons after the iPhone 15 Pro launch (Insight #2)
These calls span all iOS versions on iPhone 15 Pro hardware — confirming hardware, not software, is the driver
### Product team
File a Claim entry point is currently nested under the Coverage tab (Insight #1)
Image upload pipeline does not currently apply client-side compression (Insight #2)
Deductible disclosure step shows the full deductible upfront before claim commitment (Insight #3)
## 8. Channel-Specific Behavioral Notes
### Mobile app users
Receive push notifications for claim status — high engagement
Native camera integration; no client-side compression
Most retention-positive segment after a successful completed claim
Discover File a Claim only ~38% of the time when navigating Claims section
### Phone-channel filers
42% have the Asurion or carrier-branded app installed
Of those, 78% have an app session in the last 30 days
Default to phone because filing via app is not visible enough on the home screen
Churn at 64% within 90 days — vs 22% for app filers
### Retail walk-in (uBreakiFix)
Strong in-store experience
Same-day repair completion
Limited footprint (~500 US locations)
## 9. Pricing & Cost Structure
### Asurion deductible structure
Tier 1 devices: screen repair $29–$49, replacement $99
Tier 2 devices: screen repair $79–$99, replacement $149–$179
Tier 3 devices (incl. iPhone 15 Pro): screen repair $99–$149, replacement $199–$249
Tier 4 devices: screen repair $149–$199, replacement $249–$299
### Cost structure for screen repair
Replacement screen part: $45–$80
Labor: $25–$40
Logistics and overhead: $15–$25
Total cost per screen repair: ~$85–$145
Current deductible revenue: $99–$149
Implied margin: 0–30% (low margin already)
Implication: lowering deductible to recover retention has limited downside since margin is thin

[TABLE 0]
| Theme | Monthly Volume | Trend (YoY) | Maps to Insight |
| Photo upload not working in app | 1,240 | +340% | #2 (iPhone 15 Pro) |
| Deductible too high for screen repair | 890 | +22% | #3 (deductible drop-off) |
| I can't find File a Claim button in app | 320 | +62% | #1 (channel migration) |
| General billing question | 210 | Flat | (baseline) |
| Coverage transfer to new device | 180 | Flat | (baseline) |
| Account login help | 150 | Flat | (baseline) |
| Update payment method | 130 | Flat | (baseline) |
| Coverage details question | 115 | Flat | (baseline) |

[TABLE 1]
| Provider | Customer Pays | Turnaround | Notes |
| Asurion deductible (with monthly premium) | $129–$149 | Same day at uBreakiFix walk-in | Plus monthly premium |
| Apple Store (out-of-warranty) | $329 | Same day | No subscription required |
| uBreakiFix walk-in (no insurance) | $249–$299 | Same day | Owned by Asurion but priced as third-party |
| iFixit DIY kit | $59–$89 | Self-service | Customer does the repair |
| Local independent repair shops | $45–$120 | Same day, varies by shop | Variable quality |