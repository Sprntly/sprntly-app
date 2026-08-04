-- RECONSTRUCTED 2026-07-30: applied directly to the shared Supabase project
-- (version 20260730130000 recorded) but never committed — see
-- 20260730120000_reports.sql for the incident note. Statements verbatim from
-- schema_migrations.statements; committing is a DB no-op.
--
-- Report share links — opt-in, per-report public access by token.
--
-- Append-only columns on `reports` (20260730120000_reports.sql). Mirrors the
-- prototype share surface (share_mode / share_token / share_passcode_hash added
-- by 20260604000000_prototype_sharing.sql and driven by db/prototypes.py's
-- set_share_config), so there is ONE share vocabulary in the product rather than
-- a second, subtly different one.
--
-- DEFAULT PRIVATE, and that default is load-bearing: a report quotes customers
-- verbatim and can carry competitive intelligence, so nothing becomes reachable
-- by link until someone explicitly turns sharing on for that report. Existing
-- rows adopt 'private' via the column default, so this migration cannot expose
-- anything already captured.
--
--   share_mode          'private' → not reachable by link at all
--                       'public'  → anyone with the token can read it
--                       'passcode'→ token + passcode required
--   share_token         the access primitive. Minted on the first non-private
--                       share and then PRESERVED across public→private→public
--                       toggles, so a link handed to someone keeps working when
--                       sharing is paused and resumed (the prototype surface's
--                       static-URL invariant). Unique: it is looked up directly.
--   share_passcode_hash argon2id hash, never the plaintext. NULL unless the mode
--                       is 'passcode'; cleared when leaving that mode so a
--                       re-share never silently retains a stale gate.
--   shared_at           when sharing was last (re)configured — for audit, and to
--                       answer "when did this leave the building".

alter table reports
    add column if not exists share_mode          text not null default 'private',
    add column if not exists share_token         text,
    add column if not exists share_passcode_hash text,
    add column if not exists shared_at           timestamptz;

-- Keep the mode a closed set at the DB level: an unknown mode would be
-- ambiguous exactly where it is most dangerous (the public read path).
do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'reports_share_mode_check'
    ) then
        alter table reports
            add constraint reports_share_mode_check
            check (share_mode in ('private', 'public', 'passcode'));
    end if;
end $$;

-- The public read is a lookup BY TOKEN across every tenant, so it needs to be
-- both unique and indexed. Partial: unshared reports hold NULL and must not
-- collide with each other.
create unique index if not exists reports_share_token_uniq
    on reports (share_token) where share_token is not null;
