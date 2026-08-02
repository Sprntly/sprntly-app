-- LLM-context extraction jobs — fire-and-forget status rows for the onboarding
-- "Import your context" step.
--
-- POST /v1/connectors/llm-context/import parses the uploaded Markdown with the
-- deterministic heading walk (instant, exact for files our own prompt produced)
-- and returns those fields in the response body. That only covers OUR format,
-- so the same request also persists a `generating` row here and kicks an LLM
-- extraction pass over the raw file in a background task — which reads context
-- documents of ANY shape, not just ours.
--
-- The job runs while the user works through the connectors step, so by the time
-- they reach the steps the import prefills (metrics, product, workspace) the
-- fields have landed. Mirrors website_analysis_jobs exactly: a backgrounded or
-- remounted onboarding tab re-attaches by polling
-- GET /v1/connectors/llm-context/import/{job_id} instead of orphaning the work.
--
-- Status walks generating → ready (or error). `result` holds the same
-- {fields, unmapped, format_version, note} shape the POST returns, so the
-- frontend consumes one contract from both endpoints.

create table if not exists llm_context_jobs (
    id          bigint generated always as identity primary key,
    company_id  uuid not null references companies (id) on delete cascade,
    status      text not null default 'generating'
                check (status in ('generating', 'ready', 'error')),
    result      jsonb,
    error       text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create index if not exists llm_context_jobs_company_idx
    on llm_context_jobs (company_id, id desc);

alter table llm_context_jobs enable row level security;
