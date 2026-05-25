-- GitHub App installations + tracked PRs.
--
-- Lands ahead of the code in PR #1 (feat/github-webhook-install-tokens)
-- so Supabase already has the right shape by the time that PR merges.
-- No tables here are read by code on main yet — pure additive DDL.
--
-- github_installations: one row per install_id. account_type is
-- 'User' or 'Organization'; repository_selection is 'all' or
-- 'selected'.
--
-- github_pull_requests: open/closed PRs we've seen via webhook.
-- Updates on pull_request opened / edited / synchronize / closed /
-- reopened events. Composite PK (repo_full_name, pr_number) since
-- PR numbers are scoped to a repo.

create table if not exists github_installations (
    installation_id      bigint primary key,
    account_id           bigint not null,
    account_login        text not null,
    account_type         text not null,
    repository_selection text not null default 'selected',
    suspended            boolean not null default false,
    permissions          jsonb not null default '{}'::jsonb,
    events               jsonb not null default '[]'::jsonb,
    created_at           timestamptz not null default now(),
    updated_at           timestamptz not null default now()
);

create table if not exists github_pull_requests (
    installation_id  bigint not null,
    repo_full_name   text not null,
    pr_number        int not null,
    title            text not null,
    state            text not null default 'open',
    is_draft         boolean not null default false,
    author_login     text,
    head_ref         text,
    base_ref         text,
    html_url         text,
    body_excerpt     text,
    pr_created_at    timestamptz,
    pr_updated_at    timestamptz,
    last_event_at    timestamptz not null default now(),
    primary key (repo_full_name, pr_number)
);

create index if not exists github_pull_requests_install_state_idx
    on github_pull_requests (installation_id, state);

alter table github_installations enable row level security;
alter table github_pull_requests enable row level security;
