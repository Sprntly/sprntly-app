-- Persisted call transcripts — the store the VoC digest reads instead of
-- re-fetching every call live from Fireflies/Zoom at question time.
--
-- This REVERSES a deliberate earlier decision ("transcripts are deliberately
-- not stored" — app/call_index.py), by owner call 2026-08-12: with the
-- connector refresh at a 10-minute cadence and live connector reads stood
-- down on the answer path, the ~28s the digest spent fetching transcripts per
-- question was the last big live leg — and the price of removing it is
-- storing the customer's call content here. One row per call: the rendered
-- CallTranscript record (distilled summary + verbatim quotes) as jsonb,
-- keyed by the same (provider, external_id) the call index uses, dated so a
-- digest window is one indexed range read.
--
-- Rows are written write-through at digest time (first ask over a window
-- warms it) and by the sync-side writer (follow-up slice). `on delete
-- cascade` from companies: deleting a tenant deletes its call content.

create table if not exists call_transcripts (
    id            bigint generated always as identity primary key,
    company_id    uuid not null references companies (id) on delete cascade,
    provider      text not null,
    external_id   text not null,
    call_date     timestamptz,
    payload       jsonb not null,
    fetched_at    timestamptz not null default now(),
    unique (company_id, provider, external_id)
);

create index if not exists call_transcripts_window_idx
    on call_transcripts (company_id, call_date);

alter table call_transcripts enable row level security;
drop policy if exists "srv_call_transcripts" on call_transcripts;
create policy "srv_call_transcripts" on call_transcripts for all using (true) with check (true);
