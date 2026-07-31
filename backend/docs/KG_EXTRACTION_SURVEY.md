# KG data-source survey — extraction needs per connector

What each of the 11 live-for-KG connectors actually pulls, how that raw data
is shaped before it reaches the extractor, and what a purpose-built
extraction skill for it would need to know that the single generic prompt
(`app.graph.extractor.extract_document`, `PROMPT_VERSION = "extract-doc-v1"`)
does not. Written to answer the KG owner's ask directly: *"look at the
different data sources we are ingesting — how does that data look like? —
and start with preliminary skills of how we're ingesting it."*

Three connectors (HubSpot, Jira, ClickUp) now have a real, named extraction
skill — see `backend/skills/hubspot-extraction/`,
`backend/skills/jira-extraction/`, `backend/skills/clickup-extraction/`, and
the routing table `PROVIDER_SKILLS` in `app/kg_ingest/runner.py`. The
remaining eight stay on the generic path for now; their entries below are
the groundwork for whichever gets a skill next.

## How to read this

For every connector: **shape** (what the puller actually emits, as a
`RawRecord`), **what's distinctive**, and **what a dedicated skill would
need to know**. "Live for KG" here means the connector's content reaches
`extract_document` today, one way or another — either through the
`PULLERS` registry (`app/kg_ingest/runner.py`, one token-based pull per
provider) or through the corpus path (`app/synthesis_brief._seed_from_corpus`,
for connectors whose sync writes markdown files rather than yielding
`RawRecord`s directly).

---

## 1. HubSpot — *has a skill* (`hubspot-extraction`)

- **Path:** `PULLERS` (`app/kg_ingest/pullers/hubspot.py`).
- **Shape:** five sub-resources, each its own `RawRecord.kind`: `deal`,
  `ticket`, `note`, `email`, `owner`, plus `line_item`. Deals carry
  `amount_usd`/`stage`/`pipeline`; tickets carry `priority`/`category`;
  notes/emails carry raw customer text; owners are pure attribution.
- **Distinctive:** the single richest connector — six structurally
  different record shapes in one sync, spanning revenue, support pain, and
  first-party customer voice. The generic prompt sees six kinds of text
  with no signal about which HubSpot object produced each one, so it can't
  reliably tell a stalled deal apart from a support complaint without
  re-deriving that from prose every time.
- **What a skill needs to know:** the native `kind` bracket header
  (`deal`/`ticket`/`note`/`email`/`line_item`/`owner`) decides
  `source_type` and steers `kind` far more reliably than free-text
  classification — and it needs to know `owner` records carry no
  evidentiary content at all (skip them outright, a case the generic prompt
  has no way to know without being told).

## 2. Jira — *has a skill* (`jira-extraction`)

- **Path:** `PULLERS` (`app/kg_ingest/pullers/jira.py`).
- **Shape:** one record kind, `issue`, but each carries a **native type**
  (Bug/Story/Task/Epic) plus `status`/`priority`/`project`/`labels`/
  `assignee` straight from Jira's own fields.
- **Distinctive:** Jira is the one project-management connector that
  already tells you the record's type — the puller's own docstring notes
  "the extractor still classifies downstream, but the native type is a
  useful signal" that today goes unused.
- **What a skill needs to know:** trust the native `type` field as the
  primary classifier (Bug → `kind: bug`) instead of re-guessing from the
  summary; treat Epics as roadmap groupings (`finding`), not discrete asks;
  and — the connector-specific judgment call — never let a Bug's
  description upgrade its `source_type` to `customer_voice` even when it
  quotes a customer, because Jira is the team's internal execution record
  of the issue, not the first-party channel that captured it.

## 3. ClickUp — *has a skill* (`clickup-extraction`)

- **Path:** `PULLERS` (`app/kg_ingest/pullers/clickup.py`).
- **Shape:** one record kind, `task`, with `status`/`priority`/`list`/
  `tags`/`assignees` — but **no native type field at all** (confirmed in
  the puller's own docstring: "Tasks are untyped in ClickUp").
- **Distinctive:** the mirror image of Jira — same domain (project-
  management work items), opposite extraction problem. Where Jira hands you
  the classification for free, ClickUp hands you nothing: bug vs. feature
  vs. chore has to be inferred from title/description wording and from
  which `list`/`tag` the task lives under ("Bugs" vs. "Feature Requests" vs.
  "Backlog").
- **What a skill needs to know:** the inference rules the generic prompt
  doesn't carry — bug-flavored wording or list/tag names bias toward `bug`,
  feature-flavored wording or planning-space lists bias toward
  `feature_request`, and a bare task with no concrete claim (a title alone,
  empty description) is not evidence and should be skipped rather than
  guessed at.

## 4. Fireflies — generic path (no skill yet)

- **Path:** `PULLERS` (`app/kg_ingest/pullers/fireflies.py`).
- **Shape:** one record kind, `meeting` — **distilled** call summaries only
  (`overview`, `action_items`, `keywords`), deliberately no raw sentences on
  the KG-ingest path (§6 no-raw-dump contract; verbatim quotes exist only on
  the separate, non-persisted call-digest path).
- **Distinctive:** every record is already a second-order summary written
  by Fireflies' own model, not a raw transcript — the extractor is
  classifying a summary-of-a-summary, which caps how granular any signal
  from this connector can be.
- **What a dedicated skill would need to know:** treat `action_items` as
  the highest-confidence extraction target (concrete, already-distilled
  commitments) and `overview` as lower-confidence color; a meeting with an
  empty `overview`/`action_items` pair (a call Fireflies couldn't summarize
  well) should be skipped rather than mined for filler. Lower priority than
  HubSpot/Jira/ClickUp for a brand-new trial tenant specifically — call
  volume in week one is near zero.

## 5. GitHub — generic path (no skill yet)

- **Path:** `PULLERS` (`app/kg_ingest/pullers/github.py`).
- **Shape:** two record kinds, `pull_request` (title/body/state/author) and
  `commit` (first line as title, rest of message as body) — metadata and
  human-authored prose only, never file contents or diffs (§6
  data-minimization; a separate on-demand deep-read path handles the
  heavier "read the repo" case).
- **Distinctive:** this is *ship activity*, not customer or business
  evidence — the puller's own hint says as much ("engineering activity...
  distilled ship signals — classify feature/fix/refactor, surface what's
  being built"). It answers "what is the team building," which is a
  different evidentiary class from the CRM/support connectors above.
- **What a dedicated skill would need to know:** commit messages are noisy
  and terse by nature (many carry no product-facing claim at all — "fix
  typo", "wip"); a skill would need a much higher extraction bar on commits
  than on PR bodies, and should read `state` (merged vs. open vs. closed) as
  a confidence signal — a merged PR describes work that actually shipped, an
  open one describes work in progress.

## 6. Sprinklr — generic path (no skill yet)

- **Path:** `PULLERS` (`app/kg_ingest/pullers/sprinklr.py`).
- **Shape:** two record kinds, `case` (CX support cases: `status`,
  `priority`, `case_type`, `sentiment`) and `message` (inbound social
  mentions: `channel`, `sentiment`, `permalink`).
- **Distinctive:** the one connector that already carries a `sentiment`
  field from the source system itself, and the only "public voice" source
  (social mentions, not just first-party support) — cases are inside-in
  voice-of-customer, messages are outside-in market sentiment, and today's
  generic prompt treats both the same way despite that difference in
  evidentiary weight.
- **What a dedicated skill would need to know:** carry the source-provided
  `sentiment` straight through in `properties` rather than re-deriving it
  from prose, and weight `case` records (identified, first-party) higher
  confidence than anonymous-adjacent `message` records (public, unverified
  authorship) when both describe the same theme.

## 7. Superset — generic path (no skill yet)

- **Path:** `PULLERS` (`app/kg_ingest/pullers/superset.py`).
- **Shape:** four record kinds — `dashboard`, `chart`, `dataset`,
  `saved_query` — **BI metadata only** (titles, descriptions, SQL text);
  deliberately never actual chart data (`/api/v1/chart/{id}/data` is a later
  phase per the puller's own docstring).
- **Distinctive:** the one connector whose content is not evidence of a
  customer/business fact at all — it's a map of what the company *measures*
  (its metrics vocabulary), which the generic extraction schema (built to
  pull feature requests, bugs, deal blockers) is not really shaped for. Most
  batches likely yield few or zero real signals under the current schema.
  Because generic extraction runs anyway (Superset content flows through
  `extract_document` unfiltered) and can't productively answer "what's the
  content," a purpose-built skill's real job for this connector may be
  narrowing WHAT counts as extraction-worthy, not reclassifying what's
  already extracted.
- **What a dedicated skill would need to know:** a `saved_query`'s SQL body
  or a chart/dashboard's title/description occasionally names a real metric
  decision worth recording as a `finding` (e.g. a dashboard explicitly
  measuring churn); most rows carry no such claim and should be skipped —
  the opposite failure mode from the connectors above, where the risk is
  under-extraction, not over-extraction.

## 8. Uploads — generic path (no skill yet, by design)

- **Path:** `PULLERS` (`app/kg_ingest/pullers/uploads.py`).
- **Shape:** one record kind, `document` — chunks of the user's own uploaded
  files, each carrying the user-supplied `source_name`/`source_description`
  in `properties`.
- **Distinctive:** the "connector" here has no third-party API at all — the
  credential is just the company id, and the puller reads
  `document_source`/`document_source_file` rows the user already populated.
  The user-supplied name/description is deliberately treated as
  authoritative context by the *generic* extractor already (see
  `extract_document`'s `source_hint` usage in
  `synthesis_brief._seed_from_corpus`'s connector-category branch).
- **What a dedicated skill would need to know:** nothing connector-specific
  — the content is arbitrary (whatever the user chose to upload), so there
  is no fixed record shape a skill could specialize on the way HubSpot's
  six object kinds allow. The user-description mechanism the generic path
  already has is the right level of specialization for this one; it is not
  a good "skill" candidate.

## 9. Google Drive — generic path (no skill yet)

- **Path:** its own module (`app/kg_ingest/drive_extract.py`), bypassing
  `PULLERS` entirely — Drive has no token-based puller; its records come
  from the connection's picked-file config (Google Picker selections), not
  a bare API pull.
- **Shape:** whole files (PRDs, specs, research notes, meeting docs)
  converted to markdown and chunked by a fixed char budget; each chunk is
  its own `extract_document` call with a Drive-specific `source_hint`
  ("documents (Google Drive product docs — PRDs, specs, research notes...")
  already distinct from the fully-generic hint every other unskilled
  connector gets.
- **Distinctive:** structurally closer to Uploads than to a CRM/PM
  connector — arbitrary long-form documents, not discrete typed records.
  Already gets a source-specific hint string (not a full skill) precisely
  because "Drive doc" isn't one shape; it's whatever the picked files are.
- **What a dedicated skill would need to know:** the same non-answer as
  Uploads — the content is document-shaped and heterogeneous by nature.
  Where Drive *could* eventually specialize is file-type-aware handling
  (a PRD read differently from a spreadsheet-as-markdown), which the current
  chunker doesn't distinguish at all — a real gap, but a different kind of
  skill (parsing-aware) than the record-classification skills built here.

## 10. Slack — generic path (no skill yet)

- **Path:** corpus path — `app/connectors/slack_sync.py` writes per-channel
  markdown (resolved user names, channel topic, message history, threads) to
  the dataset corpus; `synthesis_brief._seed_from_corpus` then extracts it
  exactly like any other corpus document, with **no Slack-specific
  `source_hint`** today (it falls into the "uncategorized" branch —
  `origin="upload"`, no category-derived `source_type_default`).
- **Distinctive:** the only connector that is genuinely conversational
  (multi-person, multi-turn, informal) rather than record-shaped — a whole
  channel's history renders as one long document, which is a very different
  extraction problem from a single deal or ticket.
- **What a dedicated skill would need to know:** how to separate signal from
  channel noise (routine standup chatter, bot messages, reaction-only
  threads) and how to attribute a claim to the right speaker across a long
  multi-person thread — neither of which the generic per-document prompt is
  tuned for. Slack currently gets **less** connector-specific treatment than
  Drive (no `source_hint` at all), which is worth closing even before a full
  skill: the cheapest next step here is giving it the same kind of
  descriptive hint Drive already has.

## 11. Figma — generic path (thin content today)

- **Path:** corpus path, same mechanism as Slack (`_seed_corpus_after_sync`
  in `app/routes/connectors.py` notes "Drive/Slack/Figma write docs to the
  corpus but have no kg_ingest puller"). No Figma-specific `source_hint`
  either.
- **Shape:** whatever Figma's corpus sync currently converts to markdown —
  the connector's primary, actively-used surface in this codebase is the
  **Design Agent's** file-structure/design-token API (`GET
  /v1/connectors/figma/files/{key}`, `/styles`), which is a separate,
  out-of-scope integration; `app/connectors/catalog.py` itself classifies
  Figma under `DESIGN`, explicitly "not customer signal" — distinguishing it
  from the evidence-bearing categories.
- **Distinctive:** the thinnest KG-relevant connector of the eleven —
  design file names/comments, not the structured design data the Design
  Agent actually reads. Its evidentiary value for the graph (feature
  requests, bugs, customer voice) is inherently indirect at best.
  Lowest-priority candidate for a dedicated extraction skill; not worth
  building one until there's a concrete use case for design-surface content
  in the graph.
- **What a dedicated skill would need to know:** not yet determined — would
  depend on what Figma's corpus sync is actually asked to capture (file
  comments? version history?), which is a product decision, not an
  extraction-method one.

---

## Summary table

| # | Connector | Ingest path | Skill built? | Skill id | Why |
|---|---|---|---|---|---|
| 1 | HubSpot | `PULLERS` | ✅ | `hubspot-extraction` | Highest structural richness (6 record kinds); revenue + support + customer-voice all in one sync |
| 2 | Jira | `PULLERS` | ✅ | `jira-extraction` | Native issue type unused by the generic prompt today |
| 3 | ClickUp | `PULLERS` | ✅ | `clickup-extraction` | Same domain as Jira, opposite problem (no native type — must infer) |
| 4 | Fireflies | `PULLERS` | — | — | Already-distilled summaries; lower priority for a brand-new trial tenant |
| 5 | GitHub | `PULLERS` | — | — | Ship-activity evidence, not customer/business evidence |
| 6 | Sprinklr | `PULLERS` | — | — | Has native sentiment; good next candidate once volume justifies it |
| 7 | Superset | `PULLERS` | — | — | BI metadata, not evidentiary content — narrowing extraction scope matters more than reclassifying |
| 8 | Uploads | `PULLERS` | — | — | Content is arbitrary by design; already gets user-supplied context via the generic path |
| 9 | Google Drive | own module | — | — | Document-shaped, heterogeneous; already gets a partial source hint |
| 10 | Slack | corpus | — | — | Conversational, not record-shaped; currently has NO source hint at all (gap worth closing before a full skill) |
| 11 | Figma | corpus | — | — | Thinnest KG-relevant content of the eleven; primary Figma integration is Design Agent, out of scope here |

## How routing works today

`app.kg_ingest.runner.sync_provider` looks up `PROVIDER_SKILLS.get(provider)`
and passes the result as `extract_document(..., skill_id=...)`. When a skill
id is present, `app.graph.extractor.extract_document` passes it straight to
`gateway.llm_call(skill=...)`, which prepends the skill's `SKILL.md` (+ its
`references/*`) to the cacheable prompt prefix ahead of the extractor's own
generic system prompt, and suffixes `prompt_version` with
`+<skill_id>@<content_hash>`. Every signal a skill-routed batch produces also
carries `provenance["skill_id"]` directly, so a Signal can be traced back to
the exact skill that produced it without parsing the prompt-version string —
the field the ticket that follows this one promotes into a first-class,
queryable column. A connector with no entry in `PROVIDER_SKILLS` — every one
of the remaining eight — keeps flowing through the fully generic path,
completely unchanged.
