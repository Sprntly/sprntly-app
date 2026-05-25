-- Third-party connector storage (Google Drive / Figma / GitHub OAuth).
--
-- One row per provider. The token blob is Fernet-encrypted at
-- the application layer (TOKEN_ENCRYPTION_KEY env var) before
-- it ever reaches the database — Supabase storage is at-rest
-- encrypted on top of that.
--
-- `account_label` is the generic identifier shown in the
-- connectors UI ("alice@co.com" for Figma, "@octocat" for
-- GitHub, the user's email for Google Drive). `google_email`
-- is kept around for the existing Drive UI that reads it
-- directly; new providers should use account_label.

create table if not exists connections (
    id                      uuid primary key default gen_random_uuid(),
    provider                text not null unique,
    status                  text not null default 'active',
    google_email            text,
    account_label           text,
    scopes                  text not null default '',
    token_json_encrypted    text not null,
    config                  jsonb not null default '{}'::jsonb,
    last_sync_at            timestamptz,
    last_sync_error         text,
    created_at              timestamptz not null default now(),
    updated_at              timestamptz not null default now()
);

-- Touch updated_at on every UPDATE so the connectors list shows the
-- right "last changed" timestamp without us having to remember to set
-- it on every callsite.
create or replace function connections_touch_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists connections_set_updated_at on connections;
create trigger connections_set_updated_at
    before update on connections
    for each row execute function connections_touch_updated_at();

alter table connections enable row level security;
