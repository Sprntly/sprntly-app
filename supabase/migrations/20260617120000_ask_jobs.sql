-- Ask jobs — fire-and-forget status rows for the chat "Ask" flow.
--
-- The chat Ask endpoint used to run `qa_agent.answer(...)` inline and return
-- the answer in the POST response. A backgrounded / remounted tab would then
-- orphan the in-flight request (browsers abort fetches on long background, and
-- a remount drops the awaiting closure) even though the answer was cheap to
-- finish server-side. Mirroring how PRD / evidence already work, the POST now
-- persists a `generating` row here, kicks the same answer pipeline in a
-- background task, and returns an `ask_id`; the client polls GET /v1/ask/{id}.
--
-- Status walks generating → ready (or error). `response` holds the full JSON
-- payload the old endpoint returned (answer / key_points / citations /
-- confidence / unanswered), citation-stripped, so downstream rendering is
-- unchanged. Distinct from `cached_asks` (question-keyed, cross-tenant prewarm
-- cache for starter chips) and `ask_log` (append-only analytics history) — this
-- is a per-request, per-tenant job row.

create table if not exists ask_jobs (
    id              bigint generated always as identity primary key,
    company_id      text not null references companies (id) on delete cascade,
    dataset         text not null,
    question        text not null,
    conversation_id bigint,
    pinned_skill    text,
    status          text not null default 'generating'
                    check (status in ('generating', 'ready', 'error')),
    response        jsonb not null default '{}'::jsonb,
    error           text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

create index if not exists ask_jobs_company_idx on ask_jobs (company_id, id desc);

alter table ask_jobs enable row level security;
