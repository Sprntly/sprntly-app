-- Roadmap doc storage — the company's uploaded roadmap (priorities anchor).
-- ONE row per company (UNIQUE company_id): the latest upload replaces the prior
-- one via upsert on_conflict=company_id. Holds the original file (base64) plus
-- the extracted text the weekly brief reads + the roadmapdoc artifact renders.
--
-- Renumbered 20260623120000 → 20260623130000: the original version COLLIDED with
-- 20260623120000_connection_health.sql (two sessions stamped the same
-- timestamp). `db push` keys migrations by version, so once connection_health
-- recorded 20260623120000 first, this one failed its history-record INSERT on
-- every deploy (duplicate key), breaking the whole pipeline. The table DDL is
-- already idempotent; the policy is made idempotent below so this re-applies
-- cleanly on environments where the colliding migration already created it.
create table if not exists roadmap_doc (
  id              bigint generated always as identity primary key,
  company_id      uuid not null references companies (id) on delete cascade,
  filename        text not null,
  content_type    text,
  extracted_text  text not null default '',
  raw_b64         text,
  version         integer not null default 1,
  uploaded_at     timestamptz not null default now(),
  unique (company_id)
);
alter table roadmap_doc enable row level security;
-- Idempotent: drop-then-create so a re-apply over the colliding migration's
-- already-created policy doesn't error (Postgres has no CREATE POLICY IF NOT EXISTS).
drop policy if exists "srv_roadmap_doc" on roadmap_doc;
create policy "srv_roadmap_doc" on roadmap_doc for all using (true) with check (true);
