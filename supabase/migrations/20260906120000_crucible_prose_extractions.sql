-- Extraction results for prose attached to a Goal Analysis message, keyed by
-- the bytes that produced them.
--
-- WHY IT EXISTS. A file attached in chat is read for ONE run and is never
-- written to the knowledge graph — that is what makes attaching a file safe on
-- a tenant carrying real connector data, and it is unchanged by this table.
-- But turning its prose into claims needs a model call, and a model call is a
-- draw rather than a lookup: without a cache that outlives the worker process,
-- the identical document attached to two runs yields two different claim sets
-- and therefore two different rankings, and the product would be asserting a
-- reproducibility it does not have.
--
-- WHAT IT IS NOT. Not an ingest, and not a second evidence corpus. A row is
-- reachable only by the sha256 of text the reader has just handed over again;
-- nothing queries it by company, theme or content, and no analysis reads it
-- except in place of a call it was about to make. The attachment's own bytes
-- already persist under the workspace prefix in attachment storage, so this
-- retains nothing the workspace was not already holding.
--
-- NO TTL AND NO SWEEP, DELIBERATELY. The key is the exact text plus the prompt
-- version, so there is nothing for time to invalidate — the same bytes under
-- the same prompt have the same right answer next month, and a prompt change
-- is simply a new key. Rows are cascaded away with the company.
--
-- Scoping mirrors crucible_runs / crucible_backfill_runs: tenant column on
-- every row, filtered at every read, RLS spelled out `to service_role` on the
-- policy rather than left to default to PUBLIC.
create table if not exists crucible_prose_extractions (
    id              bigint generated always as identity primary key,
    enterprise_id   uuid        not null references companies (id) on delete cascade,
    -- sha256 of the SEGMENT text sent to the model, not of the whole file: a
    -- reader who re-attaches the same pack with one more conversation in it
    -- keeps every cached answer instead of paying for all of them again.
    content_sha256  text        not null,
    -- app.graph.extractor.PROMPT_VERSION at the time of the call. In the key
    -- so a prompt change invalidates by construction rather than by a sweep.
    prompt_version  text        not null,
    -- The model's own `{"signals": [...]}` OBJECT, verbatim. The object
    -- rather than the bare array because that is exactly what the extraction
    -- call returns, so a cache hit and a live call hand the caller the same
    -- shape and there is no unwrapping that only one of the two paths does.
    -- Kept as the extractor's shape rather than as projected Signals, so a
    -- change to the projection is picked up by the next run instead of being
    -- frozen into the cache.
    output          jsonb       not null default '{}'::jsonb,
    created_at      timestamptz not null default now(),
    constraint crucible_prose_extractions_key
        unique (enterprise_id, content_sha256, prompt_version)
);

alter table crucible_prose_extractions enable row level security;
drop policy if exists "srv_crucible_prose_extractions" on crucible_prose_extractions;
create policy "srv_crucible_prose_extractions" on crucible_prose_extractions
    for all to service_role using (true) with check (true);
