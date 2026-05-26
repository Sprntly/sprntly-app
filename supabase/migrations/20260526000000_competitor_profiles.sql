-- Competitor profiles + signals. Persistent state behind the weekly
-- research digest. A profile is the canonical record of a competitor
-- ("Linear", "Notion", "Figma") with the URLs/handles needed to monitor
-- them. Signals are the time-series of observed events (App Store
-- review, changelog post, etc) attributed to a profile.
--
-- The digest job (feat/research-competitive-digest) reads from these
-- tables; this migration lands first so the digest has something to
-- consume on day one.

create table if not exists competitor_profiles (
    id                       uuid primary key default gen_random_uuid(),
    workspace_id             text not null,
    name                     text not null,
    product_url              text,
    app_store_ios_url        text,
    app_store_android_url    text,
    g2_url                   text,
    capterra_url             text,
    changelog_url            text,
    careers_url              text,
    twitter_handle           text,
    monitoring_enabled       boolean not null default true,
    created_at               timestamptz not null default now(),
    updated_at               timestamptz not null default now()
);

create index if not exists competitor_profiles_workspace_idx
    on competitor_profiles (workspace_id);

create or replace function competitor_profiles_touch_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists competitor_profiles_set_updated_at on competitor_profiles;
create trigger competitor_profiles_set_updated_at
    before update on competitor_profiles
    for each row execute function competitor_profiles_touch_updated_at();

alter table competitor_profiles enable row level security;


create table if not exists competitor_signals (
    id                       uuid primary key default gen_random_uuid(),
    competitor_profile_id    uuid not null references competitor_profiles(id) on delete cascade,
    source                   text not null,
    signal_type              text not null,
    title                    text not null,
    body                     text not null default '',
    url                      text,
    sentiment                text,
    published_at             timestamptz not null,
    fetched_at               timestamptz not null default now(),
    raw_payload_json         jsonb not null default '{}'::jsonb,
    -- Source/type are constrained at the application layer (Pydantic
    -- Literal). We keep them as text in the DB so adding a new source
    -- doesn't require an enum ALTER + downtime.
    constraint competitor_signals_source_check
        check (source in (
            'app_store_ios','app_store_android','changelog','blog',
            'press','jobs','g2','social','pricing','seo'
        )),
    constraint competitor_signals_type_check
        check (signal_type in (
            'review','release','blog_post','press_release',
            'job_posting','rating_change','pricing_change','feature_launch'
        )),
    constraint competitor_signals_sentiment_check
        check (sentiment is null or sentiment in ('positive','neutral','negative'))
);

create index if not exists competitor_signals_profile_idx
    on competitor_signals (competitor_profile_id, published_at desc);
create index if not exists competitor_signals_profile_source_idx
    on competitor_signals (competitor_profile_id, source, published_at desc);

-- Dedup: when a signal has a URL, that URL is unique per profile (no two
-- "same review" rows). When there's no URL (e.g. a stub blog post), the
-- (source, title, published_at) triple is the fallback key.
create unique index if not exists competitor_signals_dedup_url_idx
    on competitor_signals (competitor_profile_id, url)
    where url is not null;
create unique index if not exists competitor_signals_dedup_meta_idx
    on competitor_signals (competitor_profile_id, source, title, published_at)
    where url is null;

alter table competitor_signals enable row level security;
