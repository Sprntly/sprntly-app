-- Ticket sets — tickets that exist WITHOUT a PRD behind them.
--
-- Until now the only way to get a ticket was to generate one from a PRD:
-- prd_tickets is `prd_id bigint not null` with `unique (prd_id)`, so a chat
-- that says "generate a ticket for #1" with no PRD open had nowhere to put the
-- result. It fell through to the markdown answer branch in ChatScreen and the
-- tickets were printed into the chat bubble — unpersisted, un-pushable, and
-- gone with the thread.
--
-- Additive only: prd_tickets is untouched and the PRD -> tickets path is
-- byte-for-byte unchanged. This is a SECOND home for the same `stories` jsonb
-- payload shape (app/stories/generate.py Story.to_dict), so every downstream
-- reader -- ticket_edits / ticket_comments / ticket_attachments, all keyed
-- (company_id, ticket_key) with no FK to prds -- works on these unmodified.
--
-- KEY NAMESPACE. Tickets in a set are keyed `set-{id}-{story_id}`, deliberately
-- disjoint from the `prd-{prd_id}-{story_id}` format the web composes
-- (ticketKeyFor, web/app/components/shared/TicketDetail.tsx). Disjoint means
-- stale code fails CLOSED rather than resolving a set key onto some other
-- tenant's PRD.
--
-- Scoping mirrors `reports` (20260730120000_reports.sql): reads filter by
-- company_id so every workspace shares one artifact library; workspace_id
-- records which workspace generated it and is NULLABLE because generation runs
-- in a background job that may carry no workspace context.
--
-- conversation_id is the chat the set was born in. It is a LINK, not ownership
-- (`on delete set null`): deleting the chat leaves the set in the library,
-- opening standalone -- exactly the reports posture.

create table if not exists ticket_sets (
    id              bigint generated always as identity primary key,
    company_id      uuid        not null references companies (id) on delete cascade,
    workspace_id    uuid        references workspaces (id) on delete set null,
    conversation_id bigint      references conversations (id) on delete set null,
    title           text        not null default '',
    source_text     text        not null default '',
    stories         jsonb       not null default '[]'::jsonb,
    status          text        not null default 'generating',
    error           text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

-- The artifacts listing: newest-first per company.
create index if not exists ticket_sets_company_idx on ticket_sets (company_id, id desc);

-- "which sets hang off this chat" — the thread-resume read.
create index if not exists ticket_sets_conversation_idx on ticket_sets (conversation_id);

alter table ticket_sets enable row level security;
drop policy if exists "srv_ticket_sets" on ticket_sets;
create policy "srv_ticket_sets" on ticket_sets for all using (true) with check (true);


-- ── Tracker sync for ticket sets ────────────────────────────────────────────
--
-- Standalone sets sync two-way with Jira / ClickUp / Asana exactly as PRD
-- tickets do. The Sprntly-ticket -> tracker-issue mapping needed nothing:
-- jira_issue_map / clickup_task_map / asana_task_map are keyed
-- (company_id, destination, ticket_id) where ticket_id is the story's
-- content-derived stable_id — no prd_id anywhere. The only PRD-shaped thing in
-- the whole engine is the per-artifact DESTINATION row, which is this table.
--
-- Rather than fork a parallel `ticket_set_sync` table -- which would double the
-- surface of the code that deletes issues out of customers' live trackers, and
-- leave two copies that must stay in step forever -- prd_ticket_sync gains a
-- second, mutually-exclusive owner column. One engine, one scheduler work list,
-- one set of semantics.
--
-- NON-DESTRUCTIVE, and deliberately so:
--   * `drop not null` on prd_id RELAXES a constraint. No row is read, rewritten,
--     moved or lost; every existing row keeps its prd_id exactly as it stands.
--   * the new column is nullable with no default, so existing rows are not
--     rewritten to add it.
--   * the existing `unique (company_id, prd_id)` is UNTOUCHED and still governs
--     PRD rows. Set rows carry prd_id NULL, and both Postgres and SQLite treat
--     NULLs as distinct in a unique constraint, so they never collide with it.
--
-- The set uniqueness index is deliberately NOT partial. A partial unique index
-- cannot be named as an ON CONFLICT inference target through PostgREST, so
-- upsert_sync_config's `on_conflict=company_id,ticket_set_id` would fail at
-- runtime with "no unique or exclusion constraint matching" and quietly insert
-- a duplicate destination row on every re-push. Non-partial + NULL-distinct
-- gives the same guarantee and stays inferrable.

alter table prd_ticket_sync alter column prd_id drop not null;

alter table prd_ticket_sync add column if not exists ticket_set_id bigint;

create unique index if not exists prd_ticket_sync_set_uniq
    on prd_ticket_sync (company_id, ticket_set_id);

-- Exactly one owner. Guaranteed to validate against existing rows: every one of
-- them predates this migration and so has prd_id NOT NULL and ticket_set_id
-- NULL. Guards the cross-wiring bug where a row claims both a PRD and a set and
-- the engine syncs one artifact's edits into the other's tracker project.
alter table prd_ticket_sync drop constraint if exists prd_ticket_sync_one_owner;
alter table prd_ticket_sync
    add constraint prd_ticket_sync_one_owner
    check ((prd_id is null) <> (ticket_set_id is null));
