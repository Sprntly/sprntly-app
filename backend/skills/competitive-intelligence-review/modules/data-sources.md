# Data-sourcing playbook — free / low-cost workarounds

The richest CI inputs normally sit behind paid tools (Similarweb, Sensor Tower, Crayon). Here's how to get *directional* versions for free, and how honest to be about each. **Rule: a free-tier estimate is labeled soft (🅢) and never presented as a precise figure.**

| Signal | Paid source | Free / low-cost workaround | Confidence |
|---|---|---|---|
| Web traffic & trend | Similarweb, SEMrush | Similarweb free tier (top-line visits); **Google Trends** (relative search interest, head-to-head, over time — great for *trend* direction); Ahrefs Webmaster / Ubersuggest free (keyword volume) | 🅢 directional |
| Keyword / demand | SEMrush, Ahrefs | Google Trends; Google Keyword Planner (free w/ Ads account); answerthepublic free | 🅢 |
| App downloads & rank | Sensor Tower, data.ai | Public App Store / Google Play category rankings + **rating-count growth over time** (count reviews now vs. a month ago); Sensor Tower free snapshots | 🅢 directional |
| App / product reviews & sentiment | Similarweb, AppFollow | Read public G2, Capterra, TrustRadius, app-store reviews directly; focus on 1–2★ and 4–5★; tag themes by hand | 🅢 |
| AI-search visibility | Similarweb AI tools, Profound | **Run the prompts yourself** across ChatGPT, Claude, Perplexity, Gemini ("best <category> tool", "alternatives to <us>") and tally mention share, citation share, sentiment | 🅗 (you observe it directly) |
| Ship cadence / releases | Crayon, Klue | Changelog/release-notes pages, GitHub releases, "what's new" blogs, X/LinkedIn; count & theme releases per quarter | 🅗 |
| Pricing & packaging | Competitive intel tools | Public pricing pages + **Wayback Machine** (archived pricing pages to see the *trend* of pricing changes) | 🅗 |
| Financials (public) | Bloomberg, CapIQ | **SEC EDGAR** (free 10-K/10-Q/8-K); earnings releases; **earnings-call transcripts** (Motley Fool, Seeking Alpha free) | 🅗 cite filing+date |
| Funding / M&A (private) | PitchBook, CB Insights | Crunchbase free; press releases; news; the company's own announcements | 🅢/🅘 |
| Hiring / investment signal | LinkedIn Talent | Public careers pages + LinkedIn job listings (where they staff = where they bet); Glassdoor for culture/sentiment | 🅢 |
| Win/loss | Gong, Clari | Your own CRM lost-deal reasons; sales-team debriefs; churned-customer interviews | 🅗 (your data) |
| Social / brand sentiment | Brandwatch, Sprout | Reddit + X search; LinkedIn engagement on their posts; YouTube comments on their demos | 🅢 |

**Operating rules**
- Prefer signals you can **observe directly** (run the AI prompts, read the reviews, count the releases, read the filing) — those are hard. Treat anything modeled by a free estimator as directional.
- Always record **source + date** next to each number; CI is perishable.
- When no free source exists for a metric, **state it as unknown in prose or omit it** (and, if it matters to a decision, note it once in the data appendix as worth pulling) — never a placeholder tag, **never a fabricated figure.**
- **Hard rule:** every number in the report carries a source + date, or it isn't stated as a number — describe it as unknown instead. A figure with no source is not allowed, even if it looks plausible. Do not populate metrics from model memory — they're stale; say the figure is unknown and pull fresh.
- Do not invent competitor pricing tiers, feature availability, review counts, or quotes. Observe them (visit the page, read the review, open the filing) or label them unverified.
