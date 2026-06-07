# Module — Sources & analysis areas (the coverage map)

## Where users post feedback (the source universe)
Pick by product type; don't crawl all of them every run.

**App & software stores:** Apple App Store, Google Play, Mac App Store, Microsoft Store, Amazon Appstore, Steam (games), Chrome Web Store, Shopify App Store, Salesforce AppExchange, Slack/Atlassian marketplaces.
**Software review sites:** G2, Capterra, TrustRadius, GetApp, Gartner Peer Insights, Software Advice, Product Hunt.
**Consumer review sites:** Trustpilot, Google Reviews/Business, Yelp, BBB, ConsumerAffairs.
**Community & Q&A:** Reddit (subreddits + brand mentions), Hacker News, Quora, Stack Overflow, Discord/Slack communities, Discourse forums, Canny / your own feature-request board.
**Social & video:** X/Twitter, YouTube (comments + review videos), TikTok, LinkedIn, Facebook groups, Threads, Bluesky, Mastodon.
**Developer/technical:** GitHub issues & discussions, DEV Community, npm/PyPI issue trackers, Stack Overflow tags.
**First-party / support-adjacent:** Zendesk/Intercom/Freshdesk tickets, in-app feedback, NPS/CSAT verbatims, churn/cancellation reasons, sales-call notes.
**Content/news:** blog/Substack/Medium comments, Google News mentions, podcasts.

## Per-product-type shortlist (start here)
- **B2B SaaS:** G2 · Capterra · TrustRadius · Reddit · X · LinkedIn · support tickets/NPS.
- **Consumer app:** App Store · Google Play · Reddit · TikTok · YouTube · Trustpilot.
- **Dev tool / infra:** GitHub (issues/discussions) · Hacker News · Stack Overflow · Reddit · X.
- **Marketplace app:** the host platform's own store reviews first (Chrome/Shopify/Salesforce/Slack), then Reddit/X.
- **Local/consumer service:** Google Reviews · Yelp · Trustpilot · BBB.

## Free workarounds when paid APIs aren't available
- App stores: public review pages are scrapable; RSS feeds exist for Apple; `google-play-scraper`/`app-store-scraper` style libraries.
- Reddit: public search + subreddit JSON endpoints; search "site:reddit.com <product>".
- YouTube: comments on review videos via search; transcripts for spoken feedback.
- G2/Trustpilot/Capterra: public review pages (respect ToS/robots; read, don't bulk-scrape behind auth).
- X/social: public search; many APIs are now paid — note the limitation rather than fabricate.
- Always: a targeted web search "site:<source> <product> <complaint|bug|feature>" surfaces a lot for free. **If a source can't be accessed, say so in the coverage note — never invent its contents.**

## Analysis areas (what to extract from each item)
1. **Feedback type / intent:** complaint · bug report · feature request · UX/usability issue · praise · churn/cancellation signal · pricing objection · question/confusion · competitor comparison · onboarding friction · performance/reliability · trust/security/privacy · billing.
2. **Sentiment + emotion:** positive/negative/neutral + emotion (frustration, delight, confusion, anger, appreciation).
3. **Aspect / feature attribution:** the specific feature/area the item is about (so "search is slow," not "negative").
4. **Severity:** blocker vs. annoyance.
5. **Frequency / volume:** count, after de-duping the same point across sources.
6. **Impact rank:** frequency × severity.
7. **Trend / spikes:** rising vs. fading; sudden volume spikes (early warning).
8. **Segment & source:** free vs. paid, persona, geography, language, which platform.
9. **Competitor signal:** "switched from/to X", "X does this better" → route to competitive-intelligence-review.
10. **Provenance:** real linked verbatim quote per theme; separate verified-said from inferred.
