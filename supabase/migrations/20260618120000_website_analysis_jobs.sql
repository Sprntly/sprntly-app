-- Website-analysis jobs — fire-and-forget status rows for the onboarding
-- "Gathering information about your business" interstitial.
--
-- POST /v1/onboarding/analyze-website used to run analyze_website(...) inline
-- and return the full analysis in the response. A backgrounded / remounted
-- onboarding tab would then orphan the in-flight request (browsers abort
-- fetches on long background, and a remount drops the awaiting closure) even
-- though the analysis was cheap to finish server-side. Mirroring how the chat
-- Ask flow already works (ask_jobs), the POST now persists a `generating` row
-- here, kicks the same analyze_website pipeline in a background task, and
-- returns a `job_id`; the client polls GET /v1/onboarding/analyze-website/{id}.
--
-- Status walks generating → ready (or error). `result` holds the FULL analysis
-- dict the old endpoint returned (ok / reason / industry / business_type /
-- business_context / suggested_metrics / ...), so the onboarding form's
-- setWebsiteAnalysis(result) consumes an unchanged shape. Per-request,
-- per-tenant — scoped to companies(id).

create table if not exists website_analysis_jobs (
    id          bigint generated always as identity primary key,
    company_id  text not null references companies (id) on delete cascade,
    url         text not null,
    status      text not null default 'generating'
                check (status in ('generating', 'ready', 'error')),
    result      jsonb,
    error       text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create index if not exists website_analysis_jobs_company_idx
    on website_analysis_jobs (company_id, id desc);

alter table website_analysis_jobs enable row level security;
