-- Call index — cheap, queryable metadata for every call in a connected
-- transcript source (app/call_index.py).
--
-- WHY. Answering "summarize the customer calls from last week" today fetches
-- EVERY call in the window from Fireflies *with full sentences*, assembles a
-- corpus, and hands it to the model. Measured on Northwind (22 calls):
--
--     Fireflies metadata only     1.13 s      7.7 KB   ~2k tokens
--     Fireflies with sentences    3.19 s    1461.7 KB  ~374k tokens
--     LLM synthesis             168.40 s               ~38k in / 8k out, $0.23
--
-- So ~98% of the latency is the model reading transcripts, not the API call —
-- and the corpus only fit at all because a quote-trim ladder cut 374k tokens
-- down to 38k. Worse, because that path is expensive it sits behind a keyword
-- gate, so anything the regex misses ("give me the 5 latest transcripts") falls
-- through to the KG and answers from distilled summaries instead.
--
-- This table stores the 7.7 KB half and never the transcripts. With it:
--   * "which calls last week?"      -> a DB read. No fetch, no model, no cost.
--   * "summarize the Vandelay call" -> resolve one id, fetch ONE transcript.
--   * "summarize last week"         -> unchanged; it genuinely needs them all.
--
-- Transcripts are deliberately NOT stored. They are the customer's raw
-- conversation content, they are large, and the source is the system of record
-- — we keep a pointer (external_id) and re-fetch on demand.
--
-- Scoped to companies(id) ON DELETE CASCADE like company_research_runs and
-- public_feedback_runs, so an account wipe leaves no orphans. RLS enabled with
-- no policies — unreachable with an anon/authenticated key; the backend reaches
-- it with the service key.

create table if not exists call_index (
    id             bigint generated always as identity primary key,
    company_id     uuid not null references companies(id) on delete cascade,
    -- Which connector this came from. Fireflies today; the shape is provider
    -- agnostic so Gong/Otter/Zoom can populate the same index later.
    provider       text not null default 'fireflies',
    -- The source's own id. The pointer we re-fetch a transcript by, and the
    -- natural key for idempotent re-sync.
    external_id    text not null,
    title          text,
    -- Call start. The column every window query filters on, hence the index.
    call_date      timestamptz,
    duration_min   numeric,
    -- Raw participant list from the source (emails and/or names).
    participants   jsonb not null default '[]'::jsonb,
    -- Best-effort external account/company for the call, derived from
    -- participant email domains minus our own. Nullable: internal calls
    -- (interviews, standups) legitimately have none, and a wrong guess is
    -- worse than a null when the model reads this as evidence.
    account        text,
    -- The source's OWN summary/overview where it provides one. This is what
    -- makes listing and light questions answerable with no transcript fetch
    -- and no synthesis pass.
    summary        text,
    -- Set when the row was last refreshed from the source, so a sync can skip
    -- calls it already holds and a reader can judge staleness.
    synced_at      timestamptz not null default now(),
    created_at     timestamptz not null default now()
);

-- Idempotent re-sync: one row per (company, provider, call).
create unique index if not exists call_index_company_provider_external_key
    on call_index (company_id, provider, external_id);

-- Every read is "this company's calls, newest first, optionally in a window".
create index if not exists call_index_company_date_idx
    on call_index (company_id, call_date desc);

alter table call_index enable row level security;


-- ── Sync state ──────────────────────────────────────────────────────────────
--
-- WHY A SECOND TABLE. `call_index` alone cannot distinguish the three states a
-- reader has to tell apart, and conflating them is how the index answers
-- CONFIDENTLY WRONG:
--
--   (a) "this company has no calls"        -> zero rows
--   (b) "we have never synced this company"-> zero rows      <- same!
--   (c) "we synced 6h ago and a call has   -> rows, but the newest one is
--        happened since"                       missing       <- worst
--
-- (a) and (b) are indistinguishable from row count alone, so an unsynced
-- company silently degrades to the pre-index behaviour with nothing anywhere
-- reporting a problem. (c) is worse than slow: `answer_listing` states a COUNT,
-- so a stale index produces a wrong answer to a question the user will believe.
--
-- This table is the freshness precondition `call_index.ensure_fresh()` reads
-- before any answer path is allowed to speak. One row per (company, provider).
create table if not exists call_index_sync (
    company_id     uuid not null references companies(id) on delete cascade,
    provider       text not null default 'fireflies',
    -- Last time a sync COMPLETED (successfully or not). NULL/absent row means
    -- never synced — which is what separates state (b) from state (a).
    last_sync_at   timestamptz,
    -- Last time a sync completed WITHOUT error. A reader compares against this,
    -- not last_sync_at: a failing sync that keeps stamping last_sync_at would
    -- otherwise look perpetually fresh while the data rots.
    last_success_at timestamptz,
    -- Human-readable failure, surfaced rather than swallowed. Cleared on the
    -- next success.
    last_error     text,
    -- Rows written by the last successful sync. Lets a reader say "0 calls" as
    -- a FACT ("we synced and found none") rather than as an inference from an
    -- empty table.
    call_count     integer not null default 0,
    updated_at     timestamptz not null default now(),
    primary key (company_id, provider)
);

alter table call_index_sync enable row level security;
