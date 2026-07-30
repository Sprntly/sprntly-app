-- competitive_intel_runs — one row per completed competitive-intelligence run.
--
-- The chat/Slack competitive-intelligence pipeline (app/competitive_intel.py) is
-- a multi-minute web-research sweep. This row is what makes the next run a Scan
-- instead of a Review and what follow-up questions ("what did Google ship",
-- "which threats have no defence", "status of last quarter's recommendations")
-- are answered from — without re-running the sweep.
--
-- `state` IS the skill's `state/ci-state.json` (references/state-spec.md):
-- competitors{} / our_state / decisions[], every field carrying observed_on +
-- source + tier. run_id / previous_run map to row ids, so the diff between runs
-- is `state` on this row vs `state` on the prior row for the company.
--
-- Reads and writes are BEST-EFFORT in the application (mirroring
-- public_feedback_runs): with this table absent the pipeline still answers, it
-- just always runs in Review mode and cannot answer follow-ups from storage.
-- That is what lets the pipeline PR merge in either order relative to this one.

create table if not exists competitive_intel_runs (
    id bigint generated always as identity primary key,
    company_id uuid not null references companies (id) on delete cascade,
    mode text not null default 'review' check (mode in ('scan','review')),
    question text not null,
    window_label text not null default '',
    competitor_set jsonb not null default '[]'::jsonb,
    records jsonb not null default '[]'::jsonb,
    state jsonb not null default '{}'::jsonb,
    metadata jsonb not null default '{}'::jsonb,
    html text not null default '',
    created_at timestamptz not null default now()
);

-- Latest-run lookup: (company_id, id desc) is the only access pattern.
create index if not exists competitive_intel_runs_company_idx
    on competitive_intel_runs (company_id, id desc);

-- Service-role only, like the other agent-written tables: no policies are
-- created, so RLS denies every anon/authenticated read by default.
alter table competitive_intel_runs enable row level security;
