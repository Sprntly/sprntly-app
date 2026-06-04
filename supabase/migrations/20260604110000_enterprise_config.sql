-- Per-enterprise config overrides (S5 layer 3). Most enterprises have no row.
create table if not exists enterprise_config (
    enterprise_id uuid primary key references companies (id) on delete cascade,
    overrides     jsonb not null default '{}'::jsonb,
    updated_at    timestamptz not null default now()
);
