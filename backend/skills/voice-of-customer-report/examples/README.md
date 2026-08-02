# Reference reports

Three worked examples. Together they define the standard for **structure, tone, and honesty** — check any new report against the closest one before shipping it.

| File | The question the user asked | What it exists to demonstrate |
|---|---|---|
| `kindling-voc-report.html` | *"Give me the voice of customer report for the last two quarters."* | The **default run**. All sources, no filters, goal-fit ranking. Shows a silent killer elevated (quiet theme gating $340K expansion → rec #3) and a vocal minority held back (13 accounts, frustration 2/5 → deliberately not recommended). |
| `cassette-voc-report.html` | *"What were the five most frustrating issues in the last six months?"* | A **user-specified ordering**. Themes are ordered by frustration as asked, while recommendations stay goal-fit — and the report says out loud where the two disagree (alert-rules-as-code is 4th on heat, 2nd on goal fit). Also shows 117 of 634 records excluded by origin tier, disclosed on the page. |
| `marlowe-voc-report.html` | *"Most prevalent issues from our enterprise accounts' support tickets since March 1."* | A **filtered run**. One source, one segment, prevalence ordering. Percentages recomputed inside the filter; excluded sources named; an explicit note on what a tickets-only slice cannot see; Tier-1 basis caveat under the recommendations. |

## What every one of them does

- Titled **"Voice of Customer Report"** with an explicit date range beneath.
- An **"Asked:"** line quoting the request and stating how it was honored.
- **Problem-framed TL;DR** — `#1`/`#2`/`#3` each naming who is stuck, with what, and why they can't fix it themselves.
- **Problems at a glance** with accounts, frustration (1–5), a plain-language tone read, and the metric impacted.
- **Volume vs frustration radar**, followed by prose naming the divergences.
- **Theme cards**, 2-up, each with size, description, impact chips, and 2–3 real sourced quotes.
- **~4–5 recommendations selected by goal fit**, plus a required **deliberately not recommended** block.
- At least one **honest gap** — a quote gap, a missing signal, or a column that can't be filled — stated rather than papered over.

## What none of them does

No recommendation-basis badge. No sources-and-integrity footer. No "next step: run prd-author" directive. No invented quote, anywhere — where a theme lacked a strong verbatim, the gap is stated.

## Design

White canvas throughout (`--bg:#fff`), Newsreader for prose, Inter for structure, `#1f6f52` accent. Print stylesheet included — the ⤓ PDF button produces a clean document with no page chrome. Shared stylesheet: `../_shared.css` (inlined into each report so every file stands alone).
