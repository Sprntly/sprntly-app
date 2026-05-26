-- PRDs: one row per generation attempt for a (brief, insight_index) pair.
-- `status` walks generating → ready (or failed). `variant` distinguishes
-- the original (v1) and current (v2) sample-build templates; v1 rows in
-- prod remain readable through this column.

create table if not exists prds (
    id               bigint generated always as identity primary key,
    brief_id         bigint not null references briefs(id) on delete cascade,
    insight_index    int not null,
    generated_at     timestamptz not null default now(),
    title            text not null,
    payload_md       text not null default '',
    status           text not null default 'ready',
    error            text,
    template_version int,
    variant          text not null default 'v1'
);

create index if not exists prds_brief_insight_idx
    on prds (brief_id, insight_index);

alter table prds enable row level security;
