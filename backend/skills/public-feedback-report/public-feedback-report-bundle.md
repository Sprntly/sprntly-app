# public-feedback-report — complete bundle

Everything needed to run this skill in one file. Built by VoidAI.

**Contents:** the skill spec · the readme · the capture spec that governs pass 1 · the query guide for follow-up questions.
A worked example (`examples/strava-public-feedback-report.html` and `examples/strava-feedback-records.json`) ships separately in the zip — a real run on Strava, July 2026.

---

# ══ SKILL.md ══

---
name: public-feedback-report
description: Mine PUBLIC, external feedback about a company — App Store & Google Play reviews, Reddit, G2/Capterra/Trustpilot, X, Facebook, YouTube, forums, Discord, GitHub, comparison sites — and turn it into a report of what users are saying, how it is trending, what is new, what is chronic, and what to build. Runs in two passes: a CAPTURE pass that logs every piece of feedback as an individual record tagged product or non_product, then an ANALYSIS pass that reports ONLY the product-actionable records, with non-product feedback (pricing, billing, support, sales, marketing, company) held in a short separate section so it cannot crowd the build list. Reports relative direction over collected records — never fabricated counts, never invented quotes, never guessed root cause, never percentages of the user base. Also answers follow-up questions against the captured record set ("what did the App Store say", "how long has this been raised", "show me March"). This is the PUBLIC/external counterpart to `voice-of-customer-report`; it does NOT use calls, support tickets, interviews, or internal feedback. Use when the user says "what are people saying about us online", "review mining", "app-store/Reddit/G2/Twitter feedback", "online reputation", "public sentiment", "what's trending in our reviews", or asks a question about feedback already captured.
---

# Public Feedback Report

## What it does
Reads public feedback about a company and produces **one report** answering: what are users struggling with · how is it trending · what is new · what is chronic · what should we build. Built around **change over time** — a single snapshot is the weak version.

Two things make this skill different from a generic review summary:

1. **It separates product feedback from everything else, and only analyses the product half.** A PM reading this should be able to act on every line. Pricing gripes, billing failures and support rage are captured, counted and shown — but in their own short section, because they are somebody else's job.
2. **It never invents a number.** Every percentage is computed over the records actually collected, and the report says so in plain words.

## Two passes — do not skip the first

### Pass 1 — CAPTURE (produces `<company>-feedback-records.json`)
Run `reference/capture-spec.md` in full. It governs collection. In summary:
- **One record per distinct piece of feedback.** Never merge, collapse or deduplicate. Three people saying the same thing is three records — repetition is the signal.
- **Tag every record `product` or `non_product`.** This is the most important field in the file. `non_product` records also get a subcategory (pricing, packaging, billing, sales, support, onboarding_service, marketing, company, other).
- **Every record carries an owner** — which team can actually act on it.
- **Sources are unbounded.** Never decline a source for not being on a list, and never reject one for being low quality. Record the source; capture the content.
- **When an item contains both kinds of feedback, create separate records.** One review complaining about price, a bug and a rude rep is three records.
- **When it will not sort cleanly, tag `unclear`.** Never discard.

The record set is a deliverable, not scratch. It ships with the report and is what follow-up questions are answered from.

### Pass 2 — ANALYSE (produces the report)
Everything below runs **over the `product` records only**, except the three places explicitly noted: the competitor comparison, the non-product section, and the totals.

## The sorting test (get this right and the rest is detail)
**Could a product, design or engineering team change this?** Not "could an engineer build it" — the broader test.

`product` includes bugs · missing capability · usability and workflow friction · performance and reliability · integration and ecosystem gaps · **discoverability** (the feature exists and they could not find it — that is a product problem, not a user problem) · documentation gaps · **workaround sharing** · public support-seeking · **evaluation and switching reasoning** · category and unmet-need discussion · competitor capability comparison.

Flag two as high-value whenever found: **workaround sharing**, because a published workaround specifies the real requirement better than any feature request; and **evaluation reasoning**, because it comes from people who never talked to the company and cannot be heard any other way.

### Boundary cases that decide the report's quality
| They said | Sorts as | Because |
|---|---|---|
| "Too expensive" | non_product / pricing | It is a price opinion |
| "The feature I need is locked behind a tier I can't justify" | **product** | Packaging is shaping capability access, and product can change that |
| "Support never got back to me" | non_product / support | Channel performance |
| "I had to contact support because I couldn't figure it out" | **product** | The real issue is discoverability |
| "The onboarding call was useless" | non_product / onboarding_service | Service delivery |
| "Setting it up took three days and I gave up" | **product** | In-product activation friction |
| "Their marketing said it does X and it doesn't" | **both** | One marketing record for the claim, one product record for the gap |
| "I switched to [rival] because of [capability]" | **product** / evaluation_reasoning | Names a capability |
| "I switched to [rival] because it's cheaper" | non_product / pricing | Names a price |
| "It's fine, just not worth the money" | non_product / pricing **+** any specific complaint inside it as its own product record | Two records |

## The discipline (non-negotiable)
- **Public data is a partial, self-selected sample.** Report relative direction and rank order. **Never present a percentage of the user base.** Percentages are permitted only over the collected records, and must be labelled as such in plain words wherever they appear.
- **Real quotes only** — sourced, dated, platform-attributed. Never invented, never a paraphrase in quotation marks. Reproduce one short quote per source and summarise the rest.
- **No root-cause guessing.** Report what users say, not a guessed why.
- **Never infer switching from tone.** Count someone as leaving only if they said so outright. Angry is not leaving.
- **Never present undated or stale content as current.** Date every record; flag anything older than the window; any recommendation resting on stale records carries a check-this-first line.
- **Strengths count.** Surface praise, not only problems.
- **No fabricated trend lines.** If sentiment was not scored across a real time series, do not draw a numeric axis. Show collected volume by month and mark months with no coverage as gaps.

## Write for the reader, not for us
The report is read by people inside the company — PMs, engineers, execs — not by the team that built the skill. **No internal vocabulary in reader-visible text.** Banned: corpus, denominator, record set, capture layer, staleness flag, share-of-mention, signal, impact × severity, tier, extrapolate, prevalence. Say the plain version instead:

| Don't write | Write |
|---|---|
| "corpus contamination flag" | "some of what we found was marketing, not real users" |
| "the denominator is the corpus" | "these percentages are out of the 52 posts we found, not out of our users" |
| "17 records staleness-flagged" | "17 of these are from 2024 or 2025 and may already be fixed" |
| "2 explicit switching signals" | "two people said plainly they're leaving" |
| "weight band" | "how often it came up" |

Problems are written **as the user experiences them**, in their voice, with a plain gloss underneath naming what would be fixed and who owns it. `"My real rides get flagged and I can't get the flag removed"` — not "anomaly-detection false positives."

## Method
0. **Scope.** Company, products, handles, app IDs. Platforms. Window + prior window. The metrics the business is protecting. Any prior run. **Competitors:** user-named, or the skill picks the top 2 — most-named destination in leaving posts, closest substitutes, names appearing in "switched to ___". State which 2 and why. Exclude any competitor the company owns.
1. **Capture.** Run pass 1. Produce the record file.
2. **Compute the mix.** Product vs non-product split, and sentiment split within each, over collected records. **If non-product is the majority, that is itself a finding and must be stated, never hidden.**
3. **Group product records into problems**, each with: how often it came up (rank band, never a percentage), how people felt, direction, first seen → last seen, and 1–2 real quotes.
4. **Time split, in this order:** **New** (did not exist before this period) · **Still unresolved** (raised for a long time, still live) · **Looks fixed** (people have stopped raising it). The "looks fixed" column is proof of progress — never drop it.
5. **Volume and sentiment by month across 24 months.** Green positive, amber neutral, red negative. Show months with no coverage as gaps and say so.
6. **Platform cut.** What surfaces where, and why the audiences differ.
7. **Strengths.** What people praise, in their words.
8. **Competitors.** What users love about each rival · where we win and lose, on product dimensions · who said they are leaving and why. Rival data is equally partial — same honesty bar.
9. **Recommend** ~5 product-actionable items, each led by its user-facing problem line, ordered by how many people it affects × how badly × what the business is protecting.
10. **Non-product section** — the true proportion, plus the top 3 with their owning teams.
11. **Coverage note**, on demand rather than on the page.

## Output spec
**A. Header** — "Generated by VoidAI" mark top right · period in the title · prepared date · what this covers / where we looked / what we were trying to answer, in plain sentences.
**B. Count strip** — feedback collected · product-actionable · owned elsewhere · sources checked · how many said they are leaving. Show the real number however large; 52 and 1,400 are both fine and the reader needs to know which.
**C. TL;DR — five points.** Three biggest problems, then **#4 what people are actually leaving over** and **#5 what is brand new this period**. Each in the user's voice with a short plain explanation and a real quote. Close with the single thing to do first.
**D. Volume & sentiment across 24 months** — green/amber/red, event-annotated, coverage gaps marked.
**E. The problems people are running into** — user-voice problem · how often · mood · direction · a real quote. Plain gloss naming the fix and the owner.
**F. What's new · what's stuck · what's fixed** — in that order — beside **the feedback mix panel** (product vs non-product, sentiment within each, computed over collected records).
**G. By platform.**
**H. How we compare** — external ratings with any conflicts shown · what users love, us vs each rival · where we win and lose · who said they are leaving.
**I. Recommendations** — product-actionable only. No per-item buttons.
**J. Also worth a look — not for the product team** — the true proportion, top 3, owning teams.
**K. Next steps** — one shared block: *"To address these, use VoidAI to turn them into a PRD, or add them straight to your backlog."* Two actions for the whole set, never per item.
**L. Coverage note** — collapsed behind a single line, plus a machine-readable metadata block (see below). Not shown by default.

Render as a clean white report — no coloured surround, no card shadow, reads like a document.

## Report metadata (makes the report queryable)
Embed `<script type="application/json" id="report-metadata">` carrying at minimum: generated_by, generated_at, window, totals, sentiment splits, **by_source** (per platform: totals, sentiment, product/non-product, earliest and latest post, any caution), **by_month** for the full chart window, **themes** (label, record ids, first_seen, last_seen, months_raised, status, category, owner), resolved items, switching, competitors, external ratings, and limits. This block is what follow-up questions are answered from — if it is thin, the report is a dead end.

## Query mode
After delivering the report, the skill answers questions against the record set and metadata. Read the question, work out which of these it is, answer from the data, and say when the data cannot support an answer:

| They ask | Answer from | Also say |
|---|---|---|
| "What's the feedback from the App Store?" | `by_source` + records filtered by platform | How many posts, the sentiment split, and any caution on that source |
| "How long has X been raised?" | `themes[x].first_seen` and `months_raised` | Whether it is still live, and whether the records are stale |
| "Show me feedback from March" / a date range | `by_month` + records filtered by date | Flag if the range has no coverage |
| "What's new?" / "What's fixed?" | themes by `status` · `resolved` | Dates for each |
| "Why are people leaving?" | `switching` | The exact count, and that it is only explicit statements |
| "What do people like?" | the praise theme | Their words, not ours |
| "How many complained about X?" | count of records | **Always add** that it is how many posts we found, not how many users |
| "Is this getting worse?" | `by_month` for that theme | Whether the change is big enough to mean anything given coverage gaps |

**Query rules.** Answer from captured records only — never from memory of the company. If a source returned nothing, say so plainly rather than implying silence means satisfaction. Never let a filtered count get restated as a share of users. If a question needs data not captured, say what would need collecting.

## Guardrails
- Public/external sources only; calls, tickets and interviews belong to `voice-of-customer-report`.
- Analysis section contains product-actionable feedback **only**. Non-product appears in its own section with its true proportion.
- No percentages of the user base, ever. No fabricated counts, quotes, or trend lines.
- No internal vocabulary in reader-visible text.
- Switching counted only on explicit statements.
- Stale records included but labelled, with a check-first line on anything built from them.
- Marketing content published by rivals is captured and tagged, never counted as user feedback and never as switching evidence.

## Relationship to neighbours
- **`voice-of-customer-report`** — the first-party counterpart (calls, tickets, interviews, with user identity).
- **`feedback-synthesis`** — a quick thematic pass over a small pile; this is the trended public report.
- **`competitive-intelligence-review`** — competitor findings here can feed it.
- **`prd-author`** — where recommendations route.
- **`prioritize`** — where the backlog set routes.

## Quality checklist
- [ ] Capture pass run first; record file produced and shipped.
- [ ] Every record tagged product / non_product with an owner; nothing merged or deduplicated.
- [ ] Analysis contains product-actionable items only.
- [ ] True product / non-product proportion stated, even when non-product is small.
- [ ] Percentages computed over collected records and labelled in plain words.
- [ ] TL;DR has five points including what people are leaving over and what is brand new.
- [ ] Problems written in the user's voice with a plain gloss and an owner.
- [ ] Time split ordered new → unresolved → fixed; the "fixed" column is present.
- [ ] 24-month volume and sentiment chart, green/amber/red, coverage gaps marked.
- [ ] Competitor block: named + why, what users love about each, where we win and lose, who said they are leaving.
- [ ] Recommendations product-only, ~5, each led by its user problem.
- [ ] Non-product section with the top 3 and owning teams.
- [ ] One shared next-steps block naming VoidAI; no per-item buttons.
- [ ] "Generated by VoidAI" mark present.
- [ ] Metadata block rich enough to answer source, date-range and duration questions.
- [ ] No internal vocabulary in anything the reader sees.

## Known limits
- Public data is self-selected and skews to the unhappy and the highly engaged. It shows direction and themes, never true prevalence.
- Sentiment assigned by hand is consistent but subjective; sarcasm and mixed posts are the weak spot.
- Collection reach varies by platform. Gaps in collection become gaps in the report — mark them rather than smoothing over them.
- Review manipulation and rival marketing content both exist. Capture, tag, and keep them out of switching evidence.

---

# ══ README.md ══

# public-feedback-report

Mines **public feedback** about a company from wherever people post it, and turns it into a report a product team can act on — plus a queryable record of everything found.

Built by **VoidAI**.

## What makes it different

Most review-mining output is a mood board: 60% of it is pricing gripes and support rage, and a PM reading it cannot act on any of it. This skill fixes that with two rules.

**1. It separates product feedback from everything else, and only analyses the product half.**
Every piece of feedback is tagged: could a product, design or engineering team change this? Only those drive the analysis and the recommendations. Pricing, billing, support, sales, marketing and company feedback is still captured, still counted, still shown — in its own short section, with the team that owns it. It never crowds the build list, and its true proportion is always stated.

**2. It never invents a number.**
Every percentage is computed over the feedback actually collected, and the report says so in plain words: *"these percentages are out of the 52 posts we found, not out of our users."* No share-of-mention figures, no fabricated trend lines, no counting someone as leaving because they sounded annoyed.

## How it runs — two passes

**Pass 1 — Capture.** Every distinct piece of feedback becomes one record: the quote, where it came from, when it was posted, whether it is product or not, who owns it, how the person felt, and whether they said they were leaving. Nothing is merged or deduplicated — three people saying the same thing is three records, because how often something comes up is the whole point. Output: `<company>-feedback-records.json`.

**Pass 2 — Analyse.** The report is built over the product records only. Output: a self-contained HTML report.

## What the report contains

1. **Count strip** — how much feedback, how much is product-actionable, how many sources, how many said they are leaving.
2. **TL;DR, five points** — the three biggest problems, plus what people are actually leaving over and what is brand new this period.
3. **24 months of volume and sentiment** — green, amber, red, with the months we found nothing marked as gaps rather than smoothed over.
4. **The problems people are running into** — written in their voice, with a plain gloss naming the fix and the owner.
5. **What's new · what's stuck · what's fixed** — in that order, beside the feedback mix.
6. **By platform** — what surfaces where, and why the audiences differ.
7. **How we compare** — external ratings, what users love about each rival, where we win and lose, and who said they are leaving.
8. **Recommendations** — product-actionable only, each led by its user problem.
9. **Also worth a look — not for the product team** — the top 3 non-product items and who owns them.
10. **Next steps** — one shared block: use VoidAI to turn these into a PRD, or add them to your backlog.
11. **Coverage note** — collapsed by default, plus a machine-readable metadata block.

## Asking it questions

The report carries a metadata block, and the record file sits next to it, so the skill answers follow-ups without re-running:

- "What's the feedback from the App Store?"
- "How long has the flagging problem been raised?"
- "Show me everything from March 2026."
- "What's new since last time?" · "What have people stopped complaining about?"
- "Why are people leaving?" · "What do people actually like?"
- "How many complained about X?"

Answers come from the captured records only. If a source returned nothing, it says so rather than letting silence read as satisfaction. Counts are always framed as posts found, never as a share of users.

## When to use / when not

**Use it** for online reputation, review mining, "what are people saying about us," public sentiment trends, or any follow-up question about feedback already captured.

**Don't use it** when you have direct access to users — that's `voice-of-customer-report` (calls, tickets, interviews). For a single meeting, `meeting-summary`. For a competitor deep-dive, `competitive-intelligence-review`.

## Sources

Unbounded by design. App stores and marketplaces · Reddit and niche forums · review sites · X, YouTube, TikTok, LinkedIn · Discord and community platforms · GitHub and developer forums · blogs, newsletters, podcasts, press · comparison and alternatives sites · comment sections anywhere. A source is never rejected for being low quality — it is recorded and read in light of what it is.

## Routes to

`prd-author` for the items worth specifying · `prioritize` for the backlog set · `competitive-intelligence-review` for rival findings. Non-product items route to the teams that own them, not to the backlog.

## Files

| File | What it is |
|---|---|
| `SKILL.md` | The spec the agent runs from |
| `README.md` | This file |
| `reference/capture-spec.md` | Governs pass 1 — the capture rules and record shape |
| `reference/query-guide.md` | How to answer follow-up questions from the record set |
| `examples/strava-public-feedback-report.html` | A real worked run on Strava, July 2026 |
| `examples/strava-feedback-records.json` | The 52 records that report was built from |
| `public-feedback-report-bundle.md` | Everything above as one file, for pasting into a context window |

---

# ══ reference/capture-spec.md ══

# External Feedback Capture — pass 1

You are reading publicly available or third-party commentary about a company and its product. Capture every relevant piece of feedback as an individual record, and correctly separate product feedback from non-product feedback so each can be read on its own.

You do not summarize, count, rank, or recommend. You produce records.

## 1. Sources are unbounded
Read whatever you are given. This list is illustrative, not a whitelist:

Reddit and niche forums · X/Twitter · Hacker News · app store reviews (iOS, Android, Chrome, Shopify, Salesforce, Atlassian, any marketplace) · review sites (G2, Capterra, TrustRadius, Trustpilot, Gartner Peer Insights) · Discord, Slack and community platforms · Stack Overflow and developer forums · GitHub issues and discussions · YouTube videos and comments · TikTok · blogs and newsletters · Substack · podcasts · news and analyst coverage · LinkedIn · Quora · Product Hunt · Glassdoor · Discourse instances · Facebook groups · WhatsApp and Telegram groups · comparison and alternatives sites · SEO listicles · conference talks · Twitch · comment sections anywhere.

**Never decline a source for not being on a list.** If content is provided, read it. If a new platform appears, treat it as valid and record what it is.

**Never rate a source as too low-quality to capture from.** Source affects how a record is interpreted, never whether it is admitted.

## 2. The distinction that matters
Every record is tagged into one of two top-level categories. This is the most important job.

### Category 1 — `product`
**Test: could a product, design, or engineering team change this?** Not "could an engineer build it" — the broader test.

- Bugs and defects
- Feature requests and missing capability, including when framed as a competitor comparison
- Usability and workflow friction
- Performance and reliability — slowness, crashes, downtime, data loss
- Integration and ecosystem gaps — "does this work with X", missing connectors, API limits
- **Discoverability** — the capability exists and they could not find it. A product problem, not a user problem
- Documentation gaps
- **Workaround sharing** — someone publishing a script, spreadsheet, template or manual process to compensate for a gap
- Public support-seeking — "how do I do X?" Both a real problem and a discoverability signal
- **Evaluation and switching reasoning** — why someone chose this, rejected it, or left
- Category and unmet-need discussion, even when the company is not named
- Competitor capability comparison

Flag two as high-value whenever found: **workaround sharing**, because a published workaround specifies the real requirement better than any feature request; and **evaluation reasoning**, because it comes from people who never talked to the company and cannot be heard any other way.

### Category 2 — `non_product`
Real feedback a product team cannot act on. Capture all of it. It is separated so it does not crowd a product view, not because it is unimportant.

| Subcategory | Covers |
|---|---|
| `pricing` | Cost, perceived value, "too expensive", price increases |
| `packaging` | Plan structure, tier design, what is bundled with what |
| `billing` | Invoicing, refunds, charges, payment failures, cancellation |
| `sales` | Rep experience, demos, procurement, contracts, negotiation |
| `support` | Responsiveness, quality, channels, escalation |
| `onboarding_service` | Implementation and professional services, distinct from in-product onboarding |
| `marketing` | Positioning confusion, misleading claims, ads, messaging, brand |
| `company` | Funding, valuation, acquisitions, shutdown rumors, leadership, strategy, layoffs, culture, employer reviews |
| `other` | Real but not product |

### Boundary cases — read carefully
- "Too expensive" → `non_product` / pricing
- "The feature I need is locked behind a tier I can't justify" → **`product`**. Packaging is shaping capability access, and product can change that
- "Support never got back to me" → `non_product` / support
- "I had to contact support because I couldn't figure out how to do it" → **`product`**. The underlying issue is discoverability
- "The onboarding call was useless" → `non_product` / onboarding_service
- "Setting it up took three days and I gave up" → **`product`**. In-product activation friction
- "Their marketing said it does X and it doesn't" → **two records**: marketing for the claim, product for the gap
- "It's fine, just not worth what they charge" → `non_product` / pricing, plus any specific complaint inside it as a separate product record
- "I switched to [competitor] because of [capability]" → **`product`**, type `evaluation_reasoning`
- "I switched to [competitor] because it's cheaper" → `non_product` / pricing

**When an item contains both kinds of feedback, create separate records.** A single review complaining about price, a bug, and a rude rep produces three records.

**When it does not sort cleanly, tag `unclear` and move on.** Never discard.

## 3. What to capture per item
One record per distinct piece of feedback. **Never merge, collapse, or deduplicate.** Three people saying the same thing is three records. The same person saying it twice is two records. Repetition is data.

```json
{
  "verbatim": "exact text as posted — mandatory",
  "normalized": "one-line plain statement of the feedback",
  "category": "product | non_product | unclear",
  "subcategory": "required when category is non_product",
  "type": "bug | feature_request | usability | performance | reliability | integration | discoverability | documentation | workaround | support_seeking | evaluation_reasoning | category_need | competitor_comparison | other",
  "source": {
    "platform": "any platform, named freely",
    "url": "if available",
    "posted_date": "when written, not when found",
    "product_version": "if stated or inferable",
    "staleness_flag": false
  },
  "subject": {
    "confidence": "confirmed | probable | undetermined",
    "note": "anything odd about attribution",
    "competitor_mentioned": "name, or null"
  },
  "author": {
    "type": "apparent_user | apparent_prospect | apparent_former_user | competitor_or_vendor | employee_or_affiliated | commentator | undetermined",
    "usage_demonstrated": "specific | general | none | explicit_non_user",
    "authenticity": "organic | solicited | incentivized | suspected_coordinated | undetermined",
    "segment_hints": "role, company size, industry — stated only, else null"
  },
  "intensity": {
    "sentiment": "neutral | frustrated | angry | resigned | enthusiastic",
    "user_impact": "blocking | major_friction | minor_friction | cosmetic | unstated",
    "cadence": "constant | daily | weekly | occasional | one_off | unstated",
    "workaround_present": false,
    "workaround_description": "what they built or do instead",
    "switching_stated": "stated | none",
    "switching_evidence": "verbatim, where stated"
  },
  "reach": { "engagement": "upvotes, replies, views — raw numbers only" },
  "owner": "product | design | engineering | sales | marketing | customer_success | support | pricing | leadership"
}
```

**Mandatory on every record:** `verbatim`, `category`, `owner`.

- `usage_demonstrated` — `specific` means they named a screen, error message, menu path, limit, or version behaviour. Only a real user knows those. `explicit_non_user` means they evaluated and did not adopt, or use a competitor — often the most valuable record you will find, never a reason to discard.
- `switching_stated` — only on explicit statements of moving to, from, or rejecting the product. **Never infer switching from frustration. Angry is not leaving.**
- `reach` — raw numbers only, as context. **Never weight by engagement.** Engagement measures how interesting a post was, not how many people have the problem.

**Capture positive feedback too.** Public discussion skews negative, but what people praise, and the words they use when recommending the product, tell you which capabilities actually matter.

## 4. Interpreting different sources
Record the source; do not filter by it. Read each knowing its shape:

- **App store reviews** — dated and version-tagged, the most reliable recency signal. Thin on detail. Watch for rating manipulation and review-gating.
- **Reddit and niche forums** — the most detailed and honest content available. Usually undated in practice, anonymous, heavily selected for extremes.
- **Developer Q&A and code repositories** — precise reproduction steps, often versioned. Skews technical.
- **Review sites** — structured and role-labelled, but frequently vendor-solicited, and the reviewer is often the buyer rather than the daily user.
- **Community chat** — live problems and shared workarounds. Hard to date, easy to lose context.
- **Hacker News** — strong technical detail mixed with business and funding meta-discussion. Sort carefully.
- **X, TikTok, short-form** — fast at surfacing that something broke, thin on why. Performative framing is common.
- **YouTube** — the existence of a tutorial is itself a signal that something needed explaining. Comments drift.
- **Blogs, newsletters, podcasts, news, LinkedIn** — often sponsored, promotional, SEO-driven, or analyst framing rather than lived use. Capture; label the author `commentator`.

## 5. Recency
Public complaints have unlimited shelf life and get quoted years later as if current. **This is the most common way external feedback misleads.**

Record `posted_date` and `product_version` wherever available. Set `staleness_flag` when the content predates a known relevant release or is simply old for its type. Where the described behaviour may already have changed, say so in the record.

## 6. What not to capture
Only three things:
- Content with no connection to the company or its product at all
- Obvious spam and promotional bots
- Legally restricted material — personal data, privileged content, live security incident detail

Record a brief reason for anything skipped. **Nothing else is excluded.** Not vague complaints, not unverifiable claims, not poorly written posts, not off-topic-within-the-company content, not posts from competitors or employees, not low-quality sources. Capture and tag.

## 7. Rules you must not break
- **Never merge, collapse, or deduplicate records** — including suspicious repetition. Identical phrasing across accounts is itself a finding.
- **Never treat a public count as prevalence.** You do not know the denominator and the sample is self-selected. No extrapolation to the user base, ever.
- **Never assert the author is a customer.** Record what they demonstrated.
- **Never infer switching, churn, or company size from tone.**
- **Never present undated or stale content as a current problem.**
- **Never discard a record for being unclear.** Tag it `unclear`.
- **Never decline a source for not being on a list.**
- **Always tag `category`.** A record without it is unusable downstream.

## 8. Output
A flat list of records in source order, plus:
- Sources read
- Anything skipped, with its reason

No summary. No counts. No ranking. No recommendations.

Product and non-product records must remain separately queryable by `category` and `subcategory`. **If anything downstream produces a blended view, it must report the true proportion of each — if non-product feedback is the majority of what people are saying, that is itself a finding and must never be hidden by a display limit.**

---

# ══ reference/query-guide.md ══

# Query guide — answering questions about captured feedback

After the report is delivered, users ask follow-ups. This governs how to answer them. Read the question, work out the intent, answer from the captured data, and be explicit when the data cannot support an answer.

## Where answers come from

Two artefacts, in this order:

1. **`<company>-feedback-records.json`** — the individual records. Use for anything needing actual quotes, dates, or filtering.
2. **The report's `report-metadata` block** — pre-computed rollups: `by_source`, `by_month`, `themes`, `totals`, `sentiment`, `switching`, `resolved`, `limits`. Use for anything countable.

Never answer from general knowledge about the company. If it was not captured, say so.

## Intent map

| Question shape | Intent | Answer from | Must also say |
|---|---|---|---|
| "What did [platform] say?" · "feedback from Reddit" | **source filter** | `by_source[platform]` + records filtered by platform | Post count, sentiment split, earliest→latest date, and any `caution` on that source |
| "How long has [problem] been around?" · "is this new?" | **duration** | `themes[x].first_seen`, `months_raised`, `status` | Whether it is still live, and whether the records are old enough to need checking |
| "Show me [month]" · "feedback from Q1" · "since April" | **date filter** | `by_month` + records filtered by date | If the range has no coverage, say the range is empty *for us*, not that nothing happened |
| "What's new?" | **status filter** | themes where `status = new` | First-seen date for each |
| "What have people stopped complaining about?" | **resolved** | `resolved[]` | Roughly when it went quiet, and what changed |
| "Why are people leaving?" · "are we losing users?" | **switching** | `switching` | The exact count, that it is only people who said so outright, and that it is not a churn rate |
| "What do people like?" | **praise** | the praise theme + records | Quote their words, not ours |
| "How many people complained about X?" | **count** | count of matching records | **Always**: this is how many posts we found, not how many users have the problem |
| "Is X getting worse?" | **trend** | `by_month` filtered to that theme | Whether the movement is big enough to mean anything given the coverage gaps |
| "Who owns this?" | **routing** | record `owner` / `themes[x].owner` | Whether it is product-actionable or belongs to another team |
| "What should we build first?" | **recommendation** | the report's recommendations | Do not re-rank on the fly without saying why |

## Rules

**Counts are posts, never people.** Every count must be framed as feedback found. "Seventeen posts on Trustpilot" is right. "Seventeen percent of users" is never right, whatever the arithmetic looks like.

**Empty is not the same as quiet.** If a source or a month has no records, say we did not find any — not that people were happy. Collection reach is the more likely explanation and the metadata's `limits.coverage_gap` says so.

**Stale records get flagged in the answer, not just in the file.** If someone asks about a theme whose records predate the window, lead with how old it is. "Six posts, all from November 2024 — worth checking what the product does today."

**Do not upgrade a filtered count into a finding.** "Trustpilot is 88% negative" is a fact about Trustpilot, which is where people go when something has gone wrong. Say that alongside it.

**Marketing content stays labelled.** Records tagged as rival marketing are answerable but always identified as such, and never counted towards switching or user sentiment.

**Answer the question that was asked.** If someone asks what the App Store said, do not deliver the whole report again. Give the filtered answer, then offer the next useful cut.

## Worked examples

**"What's the feedback from Trustpilot?"**
> Seventeen posts, the largest single source in the set — 15 negative, 2 positive, spanning August 2025 to July 2026. Ten are things the product team can fix, mostly wrongly flagged activities and account lockouts; seven are support and billing complaints. Worth remembering Trustpilot is where people go when something has already gone wrong, so it reads harsher than the app stores by design, not by accident.

**"How long have people been complaining about the AI write-ups?"**
> Since October 2024 — about 21 months, and still live as of March 2026. Seven posts. The ask has been consistent the whole time: not removal, just a way to turn it off. One person this year asked to have it turned back *on* after it was disabled, which is worth knowing before anyone proposes deleting the feature.

**"Show me everything from November 2025."**
> Nothing captured that month. Ten of the last 24 months are empty for us, and that is far more likely to mean our collection did not reach them than that nobody posted. October 2025 has one post and January 2026 has ten if either is useful.

**"How many people are leaving?"**
> Two said so outright, both naming Garmin, both giving the same reason — the change to how other apps can use their data. That is two people who posted it publicly, not a churn figure. We deliberately did not count anyone who just sounded frustrated.

---
