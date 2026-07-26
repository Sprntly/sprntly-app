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
