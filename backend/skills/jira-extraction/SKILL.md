---
name: jira-extraction
description: >
  Extract structured product/customer signals from a Jira issue sync batch
  into the knowledge graph's Signal/Theme schema, using Jira's NATIVE issue
  type (Bug/Story/Task/Epic) as the primary classification signal rather
  than re-guessing intent from free text. Used by the connector ingestion
  pipeline (app.kg_ingest.runner) for every Jira batch — never invoked from
  chat.
---

# jira-extraction (v1)

## Purpose
Turn one batch of Jira issues (see `app.kg_ingest.pullers.jira`) into KG
Signals + Theme links, filling the same fixed extraction schema every
extraction call fills (`kind`, `content`, `source_type`, `theme`,
`relationship`, `properties`, `confidence`). Bound by
`app.graph.extractor.extract_document(skill_id="jira-extraction")`, called
from `app.kg_ingest.runner.sync_provider("jira", ...)`.

## Input shape
```
[jira/issue id=<PROJECT-123> at=<updated>]
title: <summary>
data: status=..., priority=..., type=Bug|Story|Task|Epic, project=..., labels=[...], assignee=...
<description, flattened from Atlassian Document Format, capped ~2000 chars>
```

## Method — the native `type` field decides `kind`, not the prose
Jira issues carry a real type; use it as the primary classifier and let the
summary/description refine within it, rather than re-guessing bug-vs-feature
from words alone (the generic prompt's job — one the native field makes
unnecessary and error-prone here):

1. **`type: Bug`** → `kind: "bug"`. `source_type: "project_mgmt"`.
2. **`type: Story` or `Task`** — look at what the issue actually describes: a
   stated new capability or user-facing change → `kind: "feature_request"`;
   internal/technical work with no user-facing framing (refactor, chore,
   spike) → `kind: "finding"` (still worth recording — it says what the
   team is building — but not a "request" from anyone).
   `source_type: "project_mgmt"`.
3. **`type: Epic`** → `kind: "finding"` — an epic is a roadmap grouping, not
   a discrete ask; record it as a finding naming the initiative, and let its
   child issues (separate records in the same or a later batch) carry the
   granular requests.
4. Any other/custom issue type → treat like `Task` (rule 2): infer from
   content, since the type name itself carries no fixed meaning outside the
   four standard types.
5. **`status` / `priority`** ride into `properties` unchanged — they are
   Jira's own read of urgency/progress and should never be re-derived or
   guessed from prose.
6. **`labels`** are a strong theme hint when present — prefer a label that
   already names the feature area over inventing a new theme label from the
   summary.

## What NOT to extract
- An issue whose summary + description together carry no concrete claim (a
  bare "Investigate X" placeholder with an empty description) — skip rather
  than manufacture a signal from a title alone.
- Never invent a `status`, `priority`, or `type` not present in `data:`.
- `source_type` is always `"project_mgmt"` here — Jira issues are internal
  execution records, not first-party customer evidence. Do not let a Bug's
  description (which may quote a customer) upgrade it to `customer_voice` —
  that read belongs to the connector that captured the customer's own words
  directly (HubSpot ticket, Fireflies call), not to the team's internal
  tracking record of it.

## Expected output shape
See `references/expected-signal-shape.md` for the `type` → `kind` mapping
and worked examples.
