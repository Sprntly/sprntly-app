-- Evidence pages: same shape as prds but for the Evidence Page generator.
-- Kept as a separate table because the two have different lifecycles
-- (evidence regenerates more often) and different templates.

create table if not exists evidences (
    id               bigint generated always as identity primary key,
    brief_id         bigint not null references briefs(id) on delete cascade,
    insight_index    int not null,
    generated_at     timestamptz not null default now(),
    title            text not null,
    payload_md       text not null default '',
    status           text not null default 'generating',
    error            text,
    template_version int,
    variant          text not null default 'v1'
);

create index if not exists evidences_brief_insight_idx
    on evidences (brief_id, insight_index);

alter table evidences enable row level security;
