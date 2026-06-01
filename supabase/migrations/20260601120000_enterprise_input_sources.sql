-- Enterprise-configurable input sources per dataset/company.
-- Each row records a data source type (csv_upload, google_drive, etc.)
-- and whether it is enabled for that dataset. The config JSONB holds
-- source-specific settings (folder_id, project_id, etc.).

create table if not exists enterprise_input_sources (
    id          uuid primary key default gen_random_uuid(),
    dataset     text not null,
    source_type text not null,
    enabled     boolean not null default true,
    config      jsonb not null default '{}'::jsonb,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    unique (dataset, source_type)
);

comment on column enterprise_input_sources.source_type is
    'One of: csv_upload, google_drive, figma, github, amplitude, mixpanel, ga4, posthog';
