-- Allow users to create their own profile row if the auth trigger did not run
-- (e.g. users created before the trigger existed).
create policy "profiles_insert_own"
    on profiles for insert to authenticated
    with check (id = auth.uid());
