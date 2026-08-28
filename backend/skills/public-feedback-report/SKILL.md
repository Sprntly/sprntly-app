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
- **Sweep every public channel, every run.** Do not settle for the sources that answer first. The standing sweep is: **Reddit · X · Apple App Store · Google Play · Trustpilot · G2 · Capterra · TrustRadius · Gartner Peer Insights · YouTube · TikTok · Hacker News · Facebook Groups · Discord and community forums · GitHub · Quora · Product Hunt · marketplace review pages (Shopify, Salesforce, Atlassian) · Q&A and help sites · news and analyst coverage · blogs and newsletters.** If the product ships inside an app, the app-store listings are **mandatory**, including the parent or companion app when the feature has no listing of its own — that is where dated, version-tagged, boost-level complaints live. Name every channel that returned nothing, so a gap reads as a gap rather than as quiet.
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
0. **Scope.** Company, products, handles, app IDs. Platforms. **The reporting period is one calendar month — the first day to the last day of the most recently completed month.** Do not run a part-month; a partial period makes the volume look like a decline. Older records are still collected and still used, but only as context for whether a problem raised this month is new or long-running. Prior window is the month before. The metrics the business is protecting. Any prior run. **Competitors:** user-named, or the skill picks the top 2 — most-named destination in leaving posts, closest substitutes, names appearing in "switched to ___". State which 2 and why. Exclude any competitor the company owns.
1. **Capture.** Run pass 1. Produce the record file.
2. **Compute the mix.** Product vs non-product split, and sentiment split within each, over collected records. **If non-product is the majority, that is itself a finding and must be stated, never hidden.**
3. **Group product records into problems**, each with: how often it came up (rank band, never a percentage), how people felt, direction, first seen → last seen, and 1–2 real quotes.
4. **Time split, in this order:** **New** (did not exist before this period) · **Still unresolved** (raised for a long time, still live) · **Looks fixed** (people have stopped raising it). The "looks fixed" column is proof of progress — never drop it.
5. **Volume and sentiment by month across 24 months.** Green positive, amber neutral, red negative. Show months with no coverage as gaps and say so.
6. **Platform cut.** What surfaces where, and why the audiences differ.
7. **Strengths.** What people praise, in their words.
8. **Competitors.** What users love about each rival · where we win and lose, on product dimensions · who said they are leaving and why. Rival data is equally partial — same honesty bar.
9. **Recommend** ~5 product-actionable items, each led by its user-facing problem line, ordered by how many people it affects × how badly × what the business is protecting. On a monthly run the month alone will rarely justify five, so **carry forward still-live items from prior periods and label them as carried forward**, with the date they were first raised. Never invent a fifth item to reach the number, and never present a carried-forward item as if it were raised this month.
10. **Non-product section** — the true proportion, plus the top 3 with their owning teams.
11. **Coverage note**, on demand rather than on the page.

## Output spec
**A. Header** — "generated by sprntly ai" mark top right, lowercase · period in the title · prepared date · what this covers / where we looked / what we were trying to answer, in plain sentences.
**B. Count strip** — feedback collected · product-actionable · owned elsewhere · sources checked · how many said they are leaving. Show the real number however large; 52 and 1,400 are both fine and the reader needs to know which.
**C. TL;DR — five points.** Three biggest problems, then **#4 what people are actually leaving over** and **#5 what is brand new this period**. Each in the user's voice with a short plain explanation and a real quote. Close with the single thing to do first.
**D. Volume & sentiment across 24 months** — green/amber/red, event-annotated, coverage gaps marked.
**E. The problems people are running into** — user-voice problem · how often · mood · direction · a real quote. Plain gloss naming the fix and the owner.
**F. What's new · what's stuck · what's fixed** — in that order — beside **the feedback mix panel** (product vs non-product, sentiment within each, computed over collected records).
**G. By platform.**
**H. How we compare** — external ratings with any conflicts shown · what users love, us vs each rival · where we win and lose · who said they are leaving.
**I. Recommendations** — product-actionable only. No per-item buttons.
**J. Also worth a look — not for the product team** — the true proportion, top 3, owning teams.
**K. Next steps** — one shared block with a **single action: Draft PRD**, for the whole set of recommendations. Never per item, and no second action. Do not name the vendor in the copy.
**L. Coverage note** — collapsed behind a single line, plus a machine-readable metadata block (see below). **Not shown by default, and never rendered as a visible section on the page.** Its content still has to exist in full — how the numbers should be read, who wrote what, what was unreachable, what is old — it just lives behind the toggle rather than on the page.

**The deliverable is a PDF of no more than ten pages, and eight to ten in a normal month.**

*Running long* — hold the ceiling by cutting columns and prose, never by cutting problems, the non-product section, or the honesty lines. Collapse table columns (rank and who-raised-it belong in one cell), drop the competitor comparison table when the month produced no competitor feedback, and tighten recommendation prose. Never drop a problem to save a page; if the list genuinely will not fit, that is the finding and it goes in the report.

*Running short* — **do not pad.** A quiet month, or a month where collection reached few channels, produces a shorter report, and that is the correct output. Never invent a recommendation, restate a prior period's competitor table as though it were current, widen the period to gather more posts, or pull the coverage note back onto the page to fill space. **State the page count's cause instead:** a report that says "nine posts this month, four channels returned anything" is doing its job at six pages. Padding a thin month is the same failure as fabricating a count.

**The deliverable is a PDF.** Build the report as a single self-contained HTML file, then render it to PDF and ship the PDF as the output. The HTML is an intermediate, not the deliverable.

Render as a clean white report — no coloured surround, no card shadow, reads like a document. **No animation, transition or motion of any kind** — it is print output; anything that moves is either invisible or a rendering artefact.

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
- [ ] Every public channel swept, app stores included where the product ships in an app; channels that returned nothing are named.
- [ ] Competitor block: named + why, what users love about each, where we win and lose, who said they are leaving.
- [ ] Reporting period is one complete calendar month; no part-months.
- [ ] Recommendations product-only, each led by its user problem; carried-forward items labelled as such with their original date.
- [ ] Finished PDF is ten pages or fewer; if under eight, the report says what made the month thin rather than padding to fill.
- [ ] Non-product section with the top 3 and owning teams.
- [ ] One shared next-steps block with a single Draft PRD action; no per-item buttons, no second action.
- [ ] "generated by sprntly ai" mark present, lowercase.
- [ ] Deliverable is a PDF; no animation or transition anywhere in it.
- [ ] Coverage note lives behind the toggle only — no visible how-to-read-the-numbers section on the page.
- [ ] Metadata block rich enough to answer source, date-range and duration questions.
- [ ] No internal vocabulary in anything the reader sees.

## Known limits
- Public data is self-selected and skews to the unhappy and the highly engaged. It shows direction and themes, never true prevalence.
- Sentiment assigned by hand is consistent but subjective; sarcasm and mixed posts are the weak spot.
- Collection reach varies by platform. Gaps in collection become gaps in the report — mark them rather than smoothing over them.
- Review manipulation and rival marketing content both exist. Capture, tag, and keep them out of switching evidence.
