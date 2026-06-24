-- Per-user timezone for the weekly brief.
--
-- The weekly brief fires Monday 06:00 in the company owner's local time. We
-- capture each user's IANA timezone (e.g. "America/New_York") at signup from the
-- browser (Intl.DateTimeFormat().resolvedOptions().timeZone, passed through
-- supabase.auth.signUp options.data) and let them edit it in profile settings.
-- The scheduler resolves the company owner's profiles.timezone; a NULL/unknown
-- value falls back to UTC in app.brief_schedule.resolve_user_timezone.

alter table profiles
    add column if not exists timezone text;

-- Recreate handle_new_user so a profile row created on signup also persists the
-- timezone from raw_user_meta_data when present. Mirrors the first/last/full
-- name + avatar handling from 20260617110000_handle_new_user_google_names.sql.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
    v_first text := coalesce(
        nullif(new.raw_user_meta_data ->> 'first_name', ''),
        new.raw_user_meta_data ->> 'given_name',
        ''
    );
    v_last text := coalesce(
        nullif(new.raw_user_meta_data ->> 'last_name', ''),
        new.raw_user_meta_data ->> 'family_name',
        ''
    );
    v_timezone text := nullif(new.raw_user_meta_data ->> 'timezone', '');
begin
    insert into public.profiles (id, email, first_name, last_name, full_name, avatar_url, timezone)
    values (
        new.id,
        new.email,
        v_first,
        v_last,
        coalesce(
            nullif(new.raw_user_meta_data ->> 'full_name', ''),
            new.raw_user_meta_data ->> 'name',
            nullif(trim(both from concat_ws(' ', nullif(v_first, ''), nullif(v_last, ''))), ''),
            ''
        ),
        coalesce(
            new.raw_user_meta_data ->> 'avatar_url',
            new.raw_user_meta_data ->> 'picture',
            ''
        ),
        v_timezone
    );
    return new;
end;
$$;
