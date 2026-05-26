-- Design Agent prototypes + inline comments.
--
-- Spec source: Design_Agent_Spec.docx §4 (lifecycle), §5 (commenting).
--
-- `prototypes` is one row per prototype generation; status walks the
-- PrototypeStatus FSM (generating → iterating → complete → exported,
-- with failed as a terminal). `inputs` + `output_payload` are jsonb so
-- the route handler can return them without re-encoding.
--
-- `prototype_comments` is the inline-comment list. Comments are
-- Google-Docs-style — anchored to a section_id (page id / component id
-- / route name — generator-defined). `classification` is the delta
-- class set by the comment_classifier stub today and the Claude-driven
-- classifier later (P2).

create table if not exists prototypes (
    id              text primary key,
    workspace_id    text not null,
    artifact_id     text not null,
    status          text not null default 'generating',
    inputs          jsonb not null default '{}'::jsonb,
    output_payload  jsonb not null default '{}'::jsonb,
    output_url      text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    completed_at    timestamptz,
    exported_at     timestamptz
);

create index if not exists prototypes_workspace_idx on prototypes (workspace_id);
create index if not exists prototypes_artifact_idx on prototypes (artifact_id);

alter table prototypes enable row level security;


create table if not exists prototype_comments (
    id              text primary key,
    prototype_id    text not null references prototypes(id) on delete cascade,
    author_user_id  text not null,
    section_id      text not null,
    text            text not null,
    classification  text,
    resolved        boolean not null default false,
    created_at      timestamptz not null default now()
);

create index if not exists prototype_comments_prototype_idx
    on prototype_comments (prototype_id);

alter table prototype_comments enable row level security;
