---
name: hubspot-extraction
description: >
  Extract structured product/customer signals from a HubSpot CRM sync batch
  (deals, support tickets, notes, emails, line items, owners) into the
  knowledge graph's Signal/Theme schema. Used by the connector ingestion
  pipeline (app.kg_ingest.runner) for every HubSpot batch — never invoked
  from chat. Classifies each CRM record by its native HubSpot object kind
  rather than guessing from prose alone, so a stalled deal reads as a
  revenue blocker, a support ticket reads as customer-voice pain, and a line
  item reads as revenue detail — never as an ambiguous generic "finding."
---

# hubspot-extraction (v1)

## Purpose
Turn one batch of HubSpot CRM records (deals, tickets, notes, emails, line
items, owners — see `app.kg_ingest.pullers.hubspot`) into KG Signals + Theme
links. This skill supplies the **classification method only** — it fills the
same fixed extraction schema every extraction call fills (`kind`, `content`,
`source_type`, `theme`, `relationship`, `properties`, `confidence`), never a
different output shape. Bound by
`app.graph.extractor.extract_document(skill_id="hubspot-extraction")`, itself
called from `app.kg_ingest.runner.sync_provider("hubspot", ...)`.

## Input shape
Each record in the batch renders as (`RawRecord.render()`):
```
[hubspot/<kind> id=<hubspot-id> at=<hs_lastmodifieddate>]
title: <dealname | subject | "CRM note" | email subject>
data: key=value, key=value, ...
<free text body, capped at ~2000 chars>
```
`<kind>` is one of `deal`, `ticket`, `note`, `email`, `owner`, `line_item` —
read it off the bracket header FIRST. It is ground truth about which HubSpot
object produced the record, not something to infer from the prose below it.

## Method — classify by native object kind, never by prose alone
1. **`deal`** — a sales opportunity. `data:` carries `amount_usd`, `stage`,
   `pipeline`, `close_date`. Extract a signal ONLY when the description text
   or a stalled/lost `stage` states a concrete blocker, gap, or reason (a
   bare "Acme — $40k — proposal" with no stated reason is not evidence —
   skip it). `source_type: "revenue"`. `kind: "deal_blocker"` when the deal
   names something blocking or losing the sale; `kind: "finding"` for a
   plain revenue fact worth recording (e.g. a stated expansion driver).
   Always carry `amount_usd` into `properties` when present — a revenue
   signal with no number is weak evidence.
2. **`ticket`** — a support case. `data:` carries `priority`, `category`,
   `source`. This is the highest-value HubSpot record: real customer pain,
   first-party. `source_type: "customer_voice"`. `kind: "bug"` for a
   defect/broken-behavior report, `"feature_request"` for a stated
   capability gap, `"incident"` for an outage/data-loss report, else
   `"finding"`.
3. **`note` / `email`** (engagements — what customers actually said).
   `source_type: "customer_voice"`. `kind: "sentiment"` for a stated
   satisfaction/frustration read, `"feature_request"` when the customer
   explicitly asks for something, `"finding"` for a concrete fact worth
   keeping. A short/templated note or email with no first-person customer
   statement in it is not evidence — skip it rather than paraphrase silence
   into a signal.
4. **`line_item`** — a SKU/quantity/price row. `source_type: "revenue"`.
   `kind: "finding"`. Extract only when the properties (`sku`, `quantity`,
   `price`, `amount_usd`) tell a story worth a theme link (e.g. a product
   line disproportionately represented in the batch) — a single generic
   line item on its own rarely clears the bar; when in doubt, skip rather
   than manufacture a signal from a bare row of numbers.
5. **`owner`** — attribution only (name/email, who owns a relationship).
   Never a signal source on its own. Do not extract from `owner` records.

## Theming
Theme labels come from what the CONTENT is about (a feature area, a product
gap, a pricing objection) — never from the HubSpot object type itself:
"deal" and "ticket" are not themes. Two records of different `kind`s about
the same underlying gap (a ticket complaining about a slow export, a deal
blocked on the same missing export) must resolve to the SAME theme label so
the graph shows convergence across record types — this cross-kind theming is
the reason a dedicated skill exists here instead of one generic pass per
record.

## What NOT to extract
- `owner` records, ever.
- A `deal` / `line_item` with numbers but no stated reason, gap, or blocker
  in the text.
- A `note` / `email` that is templated boilerplate (auto-reply footers,
  scheduling confirmations) with no customer statement in it.
- Never invent a dollar amount, stage, or SKU that isn't present in `data:`.

## Expected output shape
See `references/expected-signal-shape.md` for the exact `kind` ×
`source_type` combinations this skill emits per HubSpot record kind, with a
worked example per case.
