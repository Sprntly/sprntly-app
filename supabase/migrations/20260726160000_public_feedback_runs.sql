-- Public-feedback runs — the captured record set + rendered report per run.
--
-- The public-feedback-report skill runs in two passes: CAPTURE (one record per
-- piece of public feedback found on the web) then ANALYSE (an HTML report over
-- the product records). The record set is a deliverable, not scratch: the
-- skill's query mode answers follow-ups ("what did the App Store say", "how
-- long has X been raised") from the captured records + the report's metadata
-- block, without re-running the multi-minute web sweep. Ask answers are
-- ephemeral (ask_jobs.response), so runs get their own rows here.
--
-- Additive only: no existing table, index, or policy is touched. Prod code
-- never references this table until the feature ships in a prod cutover.
--
--   records  — the flat capture list (one JSON object per piece of feedback,
--              shape governed by the skill's references/capture-spec.md)
--   metadata — the report's machine-readable rollup (by_source / by_month /
--              themes / switching / totals / limits), embedded in the report
--              as the #report-metadata block and queried by follow-ups
--   html     — the rendered report, kept with the run it belongs to (audit +
--              future re-serve/artifact surfaces; chat serves the copy in the
--              ask payload today)

create table if not exists public_feedback_runs (
    id            bigint generated always as identity primary key,
    company_id    uuid not null references companies (id) on delete cascade,
    question      text not null,
    window_label  text not null default '',
    records       jsonb not null default '[]'::jsonb,
    metadata      jsonb not null default '{}'::jsonb,
    html          text not null default '',
    created_at    timestamptz not null default now()
);

create index if not exists public_feedback_runs_company_idx
    on public_feedback_runs (company_id, id desc);

alter table public_feedback_runs enable row level security;
