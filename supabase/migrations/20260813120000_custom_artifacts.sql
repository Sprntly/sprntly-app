-- Custom artifacts — a document of ANY kind, written by the team, shared by
-- the team.
--
-- Until now every artifact in the library was a by-product of a pipeline: a
-- PRD came from a brief insight or a chat task, evidence came from a finding,
-- tickets came from a PRD, a report came from a report skill. There was no way
-- to keep a leadership update, a launch plan, a postmortem or a stakeholder
-- memo in Sprntly, even though those documents are made of exactly the same
-- material (the brief, the tickets, the customer voice) and are the ones a PM
-- actually sends upward. They lived in Google Docs, disconnected from the
-- evidence that justified them.
--
-- This table is that home. `kind` is FREE TEXT on purpose: the product promise
-- is "an artifact for anything", so an enum would need a migration every time
-- someone invents a document type, and the first thing a closed list does is
-- refuse the one the user wanted. It is a label, never a dispatch key — no
-- code branches on its value (that is what `status` is for), so an unexpected
-- string can only ever render as itself.
--
-- NAMING, because three neighbouring things are all called "document":
--   * `documents`        = POST /v1/documents/pdf, a stateless HTML->PDF render
--   * `document_catalog` = pointers to docs living in Confluence / Drive / Slack
--   * `custom_artifacts` = THIS: documents Sprntly itself stores and edits
-- The listing type is `custom_artifact` and the web section is "Others", so the
-- name is the same word at every layer and none of them collide.
--
-- SCOPING mirrors `reports` (20260730120000_reports.sql) and `ticket_sets`
-- (20260806120000_ticket_sets.sql): reads filter by `company_id`, so every
-- workspace in a company shares ONE library and any member can open and edit
-- any document. That is the requested behaviour ("shared in the workspace
-- similar to other artifacts") and it is why there is no per-document ACL here
-- — explicit per-person sharing is a later slice, and adding a column for it
-- now would be a guess at a design nobody has made yet.
--
-- `workspace_id` records which workspace it was written in and is NULLABLE for
-- the same reason it is on ticket_sets: a background generation may carry no
-- workspace context. It is provenance, not a boundary.
--
-- `conversation_id` is the chat the document was born in. A LINK, not
-- ownership (`on delete set null`): deleting the chat leaves the document in
-- the library, opening standalone — the reports/ticket_sets posture.
--
-- BODY FORMAT is HTML, not markdown. Two reasons, and the second is the
-- binding one:
--   * the editor is a rich-text surface (bold/italic/fonts), and round-tripping
--     that through markdown loses every span-level style markdown has no syntax
--     for — the "change the font" requirement is unrepresentable in markdown;
--   * the LLM already writes HTML for evidence briefs (#1108), so generation
--     has a working, reviewed contract to reuse rather than a new one to invent.
-- The body is rendered inside the app's own sanitizer, and the PDF renderer
-- runs it with JavaScript disabled (app/report_pdf.py), the same treatment
-- report HTML already gets.
--
-- VERSION is an optimistic-concurrency counter, not a history. A shared
-- document with debounced autosave has a real lost-update window: two members
-- editing the same paragraph would otherwise silently overwrite each other,
-- last-writer-wins, with no signal to either. `version` increments on every
-- body/title write; a PATCH may carry the version it started from and is
-- refused with 409 when it no longer matches. Sending no base version is
-- allowed and means "I accept last-write-wins" — that is what a title rename
-- from the listing does, where there is nothing to lose.

create table if not exists custom_artifacts (
    id              bigint generated always as identity primary key,
    company_id      uuid        not null references companies (id) on delete cascade,
    workspace_id    uuid        references workspaces (id) on delete set null,
    conversation_id bigint      references conversations (id) on delete set null,
    -- Free-text label, e.g. 'leadership update', 'launch plan'. Never dispatched on.
    kind            text        not null default '',
    title           text        not null default '',
    body_html       text        not null default '',
    -- generating | ready | failed. The one field code DOES branch on, and the
    -- same three-state lifecycle prototypes and ticket_sets use.
    status          text        not null default 'ready',
    error           text,
    -- Optimistic-concurrency counter; see the note above.
    version         integer     not null default 1,
    created_by      uuid        references auth.users (id) on delete set null,
    updated_by      uuid        references auth.users (id) on delete set null,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- The artifacts listing: most-recently-EDITED first per company. Ordered on
-- `updated_at` rather than `id` because a library of living documents is
-- browsed by last touch, and the listing applies its 200-row cap in the query —
-- so ordering by id would drop an old-but-recently-edited document before the
-- sort could raise it.
create index if not exists custom_artifacts_company_updated_idx
    on custom_artifacts (company_id, updated_at desc);

-- "which documents hang off this chat" — the thread-resume read.
create index if not exists custom_artifacts_conversation_idx
    on custom_artifacts (conversation_id);

-- Service-role only, spelled with the TO clause that 20260812170000 exists to
-- add everywhere else. Omitting it defaults the policy to PUBLIC, which is the
-- Class B defect that migration was written to close — a new table must not
-- reintroduce it.
alter table custom_artifacts enable row level security;
drop policy if exists "srv_custom_artifacts" on custom_artifacts;
create policy "srv_custom_artifacts" on custom_artifacts
    for all to service_role using (true) with check (true);
