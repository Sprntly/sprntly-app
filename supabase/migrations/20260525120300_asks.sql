-- Ask logs and cached asks.
--
-- ask_log: append-only history of every /v1/ask call. Used for
-- product analytics and to feed the cached_asks pre-warmer.
--
-- cached_asks: pre-computed answers keyed by (dataset, question).
-- Status walks generating → ready (or failed). `response` holds the
-- full JSON the API returns.

create table if not exists ask_log (
    id             bigint generated always as identity primary key,
    asked_at       timestamptz not null default now(),
    question       text not null,
    answer         text not null,
    citations      jsonb not null
);

create table if not exists cached_asks (
    id             bigint generated always as identity primary key,
    dataset        text not null,
    question       text not null,
    response       jsonb not null default '{}'::jsonb,
    status         text not null default 'generating',
    error          text,
    cache_version  int,
    generated_at   timestamptz not null default now()
);

create index if not exists cached_asks_dataset_question_idx
    on cached_asks (dataset, question, status);

alter table ask_log enable row level security;
alter table cached_asks enable row level security;
