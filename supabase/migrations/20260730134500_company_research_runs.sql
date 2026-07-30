-- Deep company research runs (app/company_research.py).
--
-- One row per staged web-research sweep over the company's OWN public
-- footprint (products/features → positioning/ICP → pricing/packaging →
-- market/category/news). The row is the durable handle for an
-- abandonment-proof onboarding kick and for the "is a run already live?"
-- guard, and it stores the captured fact records so a follow-up question
-- inside the freshness window is answered from them instead of paying for
-- another multi-minute, multi-search sweep.
--
-- Additive only: no kg_signal / kg_source / kg_ingest_ledger changes. The
-- research signals themselves land in kg_signal via the generic extractor,
-- CLAMPED to source_type='agent_inferred' and stamped
-- provenance.origin='web_research' — so they can never satisfy the brief
-- evidence gate (app/synthesis/convergence.py, which keys on source_type and
-- additionally excludes the web_research origin).
--
-- Scoped to companies(id) with ON DELETE CASCADE, like website_analysis_jobs
-- and public_feedback_runs: an account wipe must not leave orphan rows behind
-- (the lesson from the tables previous cleanups missed). RLS enabled like every
-- sibling — no policies, so PostgREST cannot read it with an anon/authenticated
-- key; the backend reaches it with the service key.

create table if not exists company_research_runs (
    id           bigint generated always as identity primary key,
    company_id   uuid not null references companies (id) on delete cascade,
    url          text,
    trigger      text not null
                 check (trigger in ('onboarding', 'chat')),
    status       text not null default 'running'
                 check (status in ('running', 'completed',
                                   'completed_partial', 'failed')),
    stages       jsonb not null default '{}'::jsonb,  -- per-stage counts + errors
    records      jsonb,                               -- captured fact records
    summary      text,
    error        text,
    created_at   timestamptz not null default now(),
    completed_at timestamptz
);

create index if not exists company_research_runs_company_idx
    on company_research_runs (company_id, created_at desc);

-- At most ONE live run per company, enforced by the DATABASE rather than by a
-- check-then-insert in application code. A sweep costs real money and minutes,
-- and two independent triggers (the onboarding kick and a chat ask) can fire
-- concurrently — a double POST, or an onboarding+chat overlap, would otherwise
-- race past an advisory guard and pay twice. The insert conflict IS the guard;
-- app code reads it as "already running" (db/company_research_runs.py).
create unique index if not exists company_research_runs_one_live_idx
    on company_research_runs (company_id) where status = 'running';

alter table company_research_runs enable row level security;
