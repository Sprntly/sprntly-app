---
name: roadmap-extraction
description: >
  Extract structured findings from the workspace's own uploaded roadmap
  (bets, timelines, commitments) into the knowledge graph's Signal/Theme
  schema — ONE signal per initiative, never per sentence, with `kind`
  always `"finding"` (a roadmap is the company's own stated plan, not an
  inbound request) and a normalized `initiative_status` + `target_period`
  on every signal. Used by the roadmap ingest pipeline
  (app.kg_ingest.roadmap) for every roadmap version — never invoked from
  chat.
---

# roadmap-extraction (v1)

## Purpose
Turn one chunk of the workspace's uploaded roadmap (see
`app.kg_ingest.roadmap`) into KG Signals + Theme links, filling the same
fixed extraction schema every extraction call fills (`kind`, `content`,
`source_type`, `theme`, `relationship`, `properties`, `confidence`). Bound
by `app.graph.extractor.extract_document(skill_id="roadmap-extraction")`,
called from `app.kg_ingest.roadmap._ingest_roadmap_locked` — once per
~4000-char chunk of the roadmap's own extracted text, never from chat.

Generic extraction over a roadmap over-fragments: it produces 2-3 signals
per initiative (one for the goal, one for the timeline, one for a status
aside) instead of one, invents a different property key for "when" almost
every time (`target_quarter`, `ship_target`, `earliest_possible`, …), and
pulls in standalone metrics and stale/shipped sections as if they were
still live bets. This skill's whole job is those three fixes: consolidate,
normalize, exclude.

## Input shape
Unlike the connector skills, a roadmap chunk carries NO per-record bracket
header — it is free-form prose/markdown/table text straight from whatever
the PM uploaded (doc, deck, spreadsheet, csv — see `app.roadmap_doc`),
chunked at a fixed character budget (`app.kg_ingest.roadmap._CHUNK_CHARS`)
with no guarantee an initiative's mentions all land in the same chunk.
Treat each chunk as everything you have for that call — do not assume a
heading, a table, or any fixed structure.

## Method

1. **One signal per initiative, not per sentence.** A single bet/initiative
   is usually described across several sentences or table cells (what it
   is, when, how committed) — merge everything the chunk says about the
   SAME initiative into ONE signal's `content`, don't split by sentence or
   by field. Only emit a second signal for the same initiative when the
   chunk genuinely restates it with materially different information that
   cannot be reconciled into one statement.

2. **`kind` is ALWAYS `"finding"`.** RESOLVED design decision — do not
   preserve `deal_blocker` even when a roadmap item explicitly names a
   commercial/revenue blocker ("blocked until Legal signs off, holding up
   the Acme renewal", "sales flagged this as a must-ship for the Q3
   pipeline"). Two reasons, matching the precedent `jira-extraction`
   already set for the identical trade-off:
   - `deal_blocker` is `hubspot-extraction`'s vocabulary, earned from
     reading a REAL CRM deal's own `stage` field — first-party evidence a
     sale is actually stalled. A roadmap sentence mentioning revenue stakes
     is still just prose inference, the same gap `jira-extraction`
     explicitly declined to cross for Jira issues that quote sales/CSM
     language ("do NOT emit a second, separately-framed signal in
     HubSpot's revenue vocabulary… classify with the native rules exactly
     as you would any other issue").
   - A roadmap is the company's OWN plan, never an inbound ask — `finding`
     is the only `kind` that honestly describes "we stated this," which is
     true of every roadmap item regardless of what commercial stake rides
     on it.
   Capture the commercial stakes as EVIDENCE inside `properties` instead of
   a different `kind` — see `properties.commercial_risk` below.

3. **`properties.initiative_status`** — exactly one of `committed` /
   `planned` / `exploring`, ALWAYS present, one consistent key name (never
   `status`, `stage`, or any other synonym):
   - `committed` — actively being built, nearly done, or stated as a firm
     "will ship" with no hedge. Includes pure hedge language that still
     reads as underway ("basically done, just finishing up QA" is
     `committed` even with zero explicit status field anywhere in the
     source).
   - `planned` — on the roadmap for a stated (or clearly implied near-term)
     period, not yet started, with no meaningful hedge about whether it
     happens ("Q3: ship X").
   - `exploring` — openly uncertain/aspirational language ("considering",
     "evaluating", "might", "exploring options for", "under discussion").
   When the text gives no signal either way, default to `planned` — the
   weakest non-committal reading that is not a fabricated "committed."

4. **`properties.target_period`** — the document's OWN stated timing,
   transcribed as it appears (e.g. `"Q3"`, `"H2 2026"`, `"next sprint"`,
   `"by end of year"`), never normalized into a fabricated date and never
   invented when the text states no timing at all. Omit the key entirely
   rather than guess — a missing `target_period` is honest; a guessed one
   is exactly the false-precision failure mode generic extraction showed
   (`target_quarter`, `ship_target`, `earliest_possible` as different,
   mutually inconsistent keys within one document).

5. **`properties.commercial_risk`** (optional, boolean) — set `true` only
   when the initiative's own text explicitly names a commercial/revenue
   stake (a named blocker to a deal, a renewal, or a pipeline commitment).
   Omit entirely when the text says nothing about revenue — never infer it
   from a metric mentioned nearby.

6. **`source_type`** — always emit `"pm_manual"`. The caller
   (`kg_ingest.roadmap`) force-pins every signal to `pm_manual` regardless
   of what this skill emits (a roadmap's own quoted metrics must never
   count as connected brief evidence — see that module's docstring), so
   this is a formality, not a live decision. Emit it anyway since the
   shared extraction schema requires the field.

7. **Theming** — same convention as every other extraction skill: a short
   feature-area/problem label the initiative is ABOUT ("AI authoring",
   "SSO"), never the roadmap document itself or a generic label like
   "Roadmap".

8. **`relationship`** — `"AFFECTS"`: a roadmap item states a planned change
   to that theme's product area, the closest fit in the 5-value extractor
   allow-list (`SUPPORTS` / `REQUESTS` / `AFFECTS` / `PRESSURES` /
   `BLOCKED_BY`).

## What NOT to extract
- **Standalone context metrics with no initiative attached** — a bare "ARR
  is $2.0M, up 14%" or "churn is 9%" sentence not tied to a specific
  planned initiative. A metric cited AS THE REASON for a bet ("cutting
  churn from 9% is why we're prioritizing onboarding in Q3") stays INSIDE
  that bet's `content` as supporting context, never as its own signal.
- **Any section the document itself marks as already shipped / not
  forward-looking** ("Shipped in Q2 — not part of this roadmap going
  forward", a "Done" column/section, a changelog appendix). A roadmap is a
  forward plan; content the document itself disowns as past is not that.
- Never invent a `target_period`, `initiative_status`, or
  `commercial_risk` value the text doesn't support — see rules 3-5.

## Expected output shape
See `references/expected-signal-shape.md` for worked examples, including
the hedge-language `committed` inference and the deal-blocker collapse
decision.
