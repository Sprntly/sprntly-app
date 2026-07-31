---
name: clickup-extraction
description: >
  Extract structured product/customer signals from a ClickUp task sync batch
  into the knowledge graph's Signal/Theme schema. ClickUp tasks carry NO
  native type field (unlike Jira) — this skill infers bug-vs-feature-vs-chore
  classification from title, description, tags, and list name, the one piece
  of connector-specific knowledge the generic extraction prompt does not
  have. Used by the connector ingestion pipeline (app.kg_ingest.runner) for
  every ClickUp batch — never invoked from chat.
---

# clickup-extraction (v1)

## Purpose
Turn one batch of ClickUp tasks (see `app.kg_ingest.pullers.clickup`) into
KG Signals + Theme links, filling the same fixed extraction schema every
extraction call fills (`kind`, `content`, `source_type`, `theme`,
`relationship`, `properties`, `confidence`). Bound by
`app.graph.extractor.extract_document(skill_id="clickup-extraction")`,
called from `app.kg_ingest.runner.sync_provider("clickup", ...)`.

## Input shape
```
[clickup/task id=<id> at=<date_updated>]
title: <task name>
data: status=..., priority=..., list=<list name>, tags=[...], assignees=[...]
<task description / text content, capped ~2000 chars>
```

## Method — infer the type; ClickUp gives you none
Unlike Jira, ClickUp tasks are untyped at the API level — every task looks
the same regardless of whether it's a bug report or a new feature (per the
puller's own docstring: "Tasks are untyped in ClickUp"). This skill's whole
job is the inference the generic prompt has no reliable way to do without
connector context:

1. **Bug signal** — the title/description states broken/incorrect behavior
   ("X throws an error", "Y is wrong", "doesn't work"), OR the `list` name
   or a tag is itself bug-flavored ("Bugs", "QA", "hotfix").
   → `kind: "bug"`.
2. **Feature/change signal** — the title/description states new or changed
   capability ("add X", "support Y", "allow users to Z"), OR the list/tags
   name a feature-planning space ("Backlog", "Roadmap", "Feature Requests").
   → `kind: "feature_request"`.
3. **Neither is clear** — routine/internal work with no bug or feature
   framing (a chore, meeting-prep, an admin item). → `kind: "finding"`,
   still recording what's being worked on, but not as a request or a
   defect.
4. `source_type` is always `"project_mgmt"` — same reasoning as Jira: this
   is the team's own backlog, not first-party customer evidence, even when
   a task quotes a customer complaint inside its description.
5. `status`, `priority`, `list`, `tags`, `assignees` ride into `properties`
   unchanged as corroborating evidence for the inference above — never
   re-typed as if ClickUp had supplied a native type.

## What NOT to extract
- A task with a title alone and an empty/placeholder description carrying
  no concrete claim about what's broken or wanted — skip it; a bare task
  name is not evidence.
- Never invent a `status`, `priority`, or tag that isn't present in `data:`.

## Expected output shape
See `references/expected-signal-shape.md` for the inference rules and
worked examples, including the ambiguous case Jira never has to handle
because it always has a native type.
