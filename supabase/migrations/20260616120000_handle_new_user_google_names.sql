-- Improve public.handle_new_user() name mapping so Google (OAuth) sign-ups land
-- with a populated profile name.
--
-- Supabase stores email/password sign-up metadata under first_name/last_name
-- (set by signUpWithPassword), but Google's OIDC profile uses given_name /
-- family_name / name instead — so the old trigger left Google users with empty
-- first/last/full names. This redefinition coalesces both shapes. Everything
-- else the original trigger did (the single profiles insert with avatar_url) is
-- preserved unchanged; only the name coalescing is improved.
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
begin
    insert into public.profiles (id, email, first_name, last_name, full_name, avatar_url)
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
        )
    );
    return new;
end;
$$;
