-- Design Agent prototype storage (P1-06).
-- AD6: prototype_checkpoints is the atomic snapshot per Generate or Apply.
-- Workspace isolation per skill-config Architecture Rules #20-#23: every new
-- table carries `workspace_id text not null` with NO default — the runtime
-- populates it from require_session().aud at insert time. A baked-in default
-- would ship prod rows under the wrong tag and leak to demo viewers.
--
-- Scenario (A/B/C/0 per spec §3) is INFERRED from the input columns at read
-- time (see infer_scenario in backend/app/db/prototypes.py), never stored —
-- so there is intentionally no `scenario` column here.

create table if not exists prototypes (
    id                     bigint generated always as identity primary key,
    prd_id                 bigint not null references prds(id) on delete cascade,
    workspace_id           text not null,                 -- NO default (Rule #20)
    status                 text not null default 'generating',
                           -- 'generating' | 'ready' | 'failed' | 'invalidated'
    variant                text not null default 'v1',
    template_version       int not null,
    instructions           text,                          -- user free-text from Generate popup
    target_platform        text not null default 'both',  -- 'desktop' | 'mobile' | 'both'
    -- ── Scenario INPUT columns (scenario itself is derived, not stored) ──────
    figma_file_key         text,                          -- non-null → contributes Scenario A
    website_url            text,                          -- non-null + no Figma → contributes B (P5-02)
    github_installation_id bigint,                        -- non-null + PRD :::design references codebase → contributes C (P4-05)
    -- (Scenario 0 = none of the above present; computed in infer_scenario.)
    bundle_url             text,                          -- populated by P1-08 on complete
    current_checkpoint_id  bigint,                        -- FK added below, after checkpoints exists
    error                  text,                          -- failure message (truncated to 500 chars at insert)
    created_at             timestamptz not null default now(),
    completed_at           timestamptz
);

create index if not exists prototypes_prd_id_idx       on prototypes (prd_id);
create index if not exists prototypes_workspace_id_idx on prototypes (workspace_id);
create index if not exists prototypes_status_idx       on prototypes (status);

alter table prototypes enable row level security;
-- No policies (matches the Sprntly pattern — the backend uses the service-role
-- key and bypasses RLS; the browser has no direct table access by default).

create table if not exists prototype_checkpoints (
    id                bigint generated always as identity primary key,
    prototype_id      bigint not null references prototypes(id) on delete cascade,
    workspace_id      text not null,                      -- NO default (Rule #20)
    bundle_url        text,                               -- per-checkpoint immutable bundle
    prd_revision_hash text,                               -- hash of PRD body at checkpoint time
    figma_frame_hash  text,                               -- hash of pulled Figma frames
    prompt_history    jsonb not null default '[]'::jsonb,
    comment_state     jsonb not null default '[]'::jsonb,
                      -- empty list in P1; P3 populates this on iterate
    created_at        timestamptz not null default now()
);

create index if not exists prototype_checkpoints_prototype_id_idx on prototype_checkpoints (prototype_id);
create index if not exists prototype_checkpoints_workspace_id_idx on prototype_checkpoints (workspace_id);

alter table prototype_checkpoints enable row level security;

-- FK from prototypes.current_checkpoint_id, added after the second table exists.
-- `drop constraint if exists` before `add constraint` keeps the migration
-- idempotent — a second apply re-creates the constraint without conflict.
alter table prototypes
    drop constraint if exists prototypes_current_checkpoint_id_fkey;
alter table prototypes
    add constraint prototypes_current_checkpoint_id_fkey
    foreign key (current_checkpoint_id)
    references prototype_checkpoints(id)
    on delete set null;
