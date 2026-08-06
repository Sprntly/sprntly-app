-- Per-(company, provider) cooldown for cross-connector sweep persistence.
--
-- Why a new table rather than reusing kg_ingest_ledger: that table's
-- created_at only advances when a hash is actually WRITTEN (new content).
-- The cooldown has to gate a source BEFORE any enrichment fetch or ledger
-- read happens at all — including the steady-state case where everything a
-- sweep reads is already ledger-deduped and nothing gets written, and the
-- Slack case, which structurally never collides with the ledger (see
-- connector_lookup/slack.py). Neither case would ever move
-- kg_ingest_ledger's timestamp, so gating on it would silently stop gating
-- once a source ran dry — exactly the steady state this cooldown exists to
-- bound. This table instead records that a (company, provider) pair was
-- PROCESSED, independent of whether anything was written.
create table if not exists sweep_persist_cooldown (
    enterprise_id uuid not null references companies (id) on delete cascade,
    provider text not null,
    last_run_at timestamptz not null default now(),
    primary key (enterprise_id, provider)
);

-- Service-role only, like kg_ingest_ledger and the other agent-written
-- tables: no policies are created, so RLS denies every anon/authenticated
-- read by default.
alter table sweep_persist_cooldown enable row level security;
