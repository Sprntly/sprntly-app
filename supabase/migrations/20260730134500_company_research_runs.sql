-- Deep company research runs (app/company_research.py).
--
-- One row per staged web-research sweep over the company's OWN public
-- footprint (products/features → positioning/ICP → pricing/packaging →
-- market/category/news). The row is the durable handle for an
-- abandonment-proof onboarding kick and for the "is a run already live?"
-- guard, and it stores the captured fact records so a later read does not
-- require re-running a multi-minute, multi-search sweep.
--
-- Additive only: no kg_signal / kg_source / kg_ingest_ledger changes. The
-- research signals themselves land in kg_signal via the generic extractor
-- with provenance.origin = 'web_research' — deliberately NOT 'upload' or
-- 'connector', so scraped web facts can never satisfy the brief evidence
-- gate (app/synthesis/convergence.py).
--
-- `company_id` is text (not a FK) to match the other run/job tables written
-- by background workers, which must not fail a write because a company row
-- is being deleted concurrently.

create table if not exists company_research_runs (
  id           bigint generated always as identity primary key,
  company_id   text not null,
  url          text,
  trigger      text not null,                          -- 'onboarding' | 'chat'
  status       text not null default 'running',        -- running | completed | failed
  stages       jsonb not null default '{}'::jsonb,     -- per-stage counts + errors
  records      jsonb,                                  -- captured fact records
  summary      text,
  error        text,
  created_at   timestamptz not null default now(),
  completed_at timestamptz
);

create index if not exists company_research_runs_company_idx
  on company_research_runs (company_id, created_at desc);
