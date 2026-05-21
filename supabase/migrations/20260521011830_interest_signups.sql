-- Interest signups captured from the sprntly.ai marketing site.
-- Written by the static site directly using the publishable (anon) key,
-- so RLS only allows INSERTs from the anon role; reads stay restricted.

create table interest_signups (
  id          bigserial primary key,
  created_at  timestamptz not null default now(),
  first_name  text,
  last_name   text,
  email       text not null,
  company     text,
  source      text
);

create index interest_signups_created_at_idx on interest_signups (created_at desc);

alter table interest_signups enable row level security;

create policy "anon insert"
  on interest_signups
  for insert
  to anon
  with check (true);
