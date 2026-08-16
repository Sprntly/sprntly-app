-- Ask jobs — execution-identity columns for the chat-parity foundation.
--
-- `ask_jobs` is main chat's LIVE primitive; staging and prod share ONE
-- Supabase project (see `backend/app/db/asks.py`), so this ALTER lands on
-- the same table backing main chat's ask/answer polling loop right now.
-- This migration adds NOTHING behavioral: every column is nullable with NO
-- default (in particular `run_id uuid` has no `default gen_random_uuid()` —
-- a volatile default on ADD COLUMN forces a full table rewrite of a live,
-- prod-shared table, which is not acceptable here). Existing rows stay
-- exactly as they are; new rows populate these columns in code, in a later
-- ticket that consumes them.
--
-- Column purposes (all NULL = "not a chat-parity run", i.e. every existing
-- row and every current main/private ask):
--   kind             — surface tag ('main' | 'project_private' |
--                       'project_group'), set at creation time.
--   project_id       — the project a run belongs to (main/private asks have
--                       none).
--   source_turn_id   — links a run to the triggering `conversation_turns`
--                       row (group chat only).
--   run_id           — a durable execution identity so retries/resume can't
--                       duplicate a run. No default: legacy rows stay NULL,
--                       new rows set it in code at insert time.
--   client_message_id — the idempotency key a retry/resume carries; enforced
--                       unique only when set (see the partial index below).
--   error_class      — a typed error category (e.g. billing/timeout/
--                       local_gate/app), separate from the user-facing
--                       generic `status = 'error'`.
--   attempt          — retry attempt ordinal; NULL for legacy/non-retried
--                       rows.
--
-- Status vocabulary is UNTOUCHED: this migration does not alter `status`,
-- its default, or its CHECK constraint (still generating/ready/error/
-- cancelled — see 20260617120000_ask_jobs.sql + 20260719120000_ask_jobs_
-- cancelled.sql). The frontend's `AgentRunStatus` (queued/running/done/
-- failed/declined) is a display-layer vocabulary mapped at the DTO edge in
-- a later ticket — it is never written to this column.
--
-- `if not exists` on every add/index for idempotent re-run, matching the
-- repo's existing ask_jobs ALTER pattern (20260718120000, 20260802120000).

alter table public.ask_jobs add column if not exists kind text;
alter table public.ask_jobs add column if not exists project_id bigint;
alter table public.ask_jobs add column if not exists source_turn_id bigint;
alter table public.ask_jobs add column if not exists run_id uuid;
alter table public.ask_jobs add column if not exists client_message_id text;
alter table public.ask_jobs add column if not exists error_class text;
alter table public.ask_jobs add column if not exists attempt int;

-- Idempotency key: a retry/resume that carries the SAME client_message_id
-- must not create a second row. Partial so the many existing (and future
-- non-parity) NULL rows never collide with each other or with a real key.
create unique index if not exists ask_jobs_client_message_id_uidx
    on public.ask_jobs (client_message_id)
    where client_message_id is not null;

-- Read-path index for a later ticket's "runs triggered by this group turn"
-- lookup. Partial for the same reason: most rows never carry a
-- source_turn_id, so a plain index would be pure dead weight on those.
create index if not exists ask_jobs_source_turn_idx
    on public.ask_jobs (project_id, source_turn_id)
    where source_turn_id is not null;
