-- Conversation turns — idempotency columns for owned, per-side persistence.
--
-- `conversation_turns` is LIVE and prod-shared: it backs main chat, group
-- project chat, AND individual project chat, and staging/prod share ONE
-- Supabase project. This ALTER lands on the same table every one of those
-- surfaces reads/writes right now. Same discipline as the earlier
-- `ask_jobs` execution-identity migration: every column is nullable with NO
-- default (no volatile default on ADD COLUMN — that forces a full table
-- rewrite of a live table), so existing rows are untouched and new rows
-- populate these columns in code, in a later ticket that consumes them.
--
-- Column purposes (all NULL = "not an owned-idempotent write", i.e. every
-- existing row and every turn written by the pre-existing cross-user
-- `post_individual_turn`/`post_group_turn` helpers):
--   client_message_id — the client-issued (or server-minted) idempotency
--                       key a retry/double-submit carries. Keyed WITH
--                       `role` so the user and assistant sides of one send
--                       (same key, different role) never collide.
--   ask_job_id         — links an assistant turn to the `/v1/ask` job that
--                       produced it (the durable run link a resumed poll
--                       reuses, so a resume can't duplicate the answer row).
--
-- Both partial (`where … is not null`) so the many existing NULL rows never
-- collide with each other or with a real key, and the index only ever
-- covers the (small, growing) subset of rows that opt into this.
--
-- `if not exists` on every add/index for idempotent re-run, matching the
-- repo's existing additive-migration pattern (see the `ask_jobs`
-- execution-identity migration this mirrors).

alter table public.conversation_turns add column if not exists client_message_id text;
alter table public.conversation_turns add column if not exists ask_job_id bigint;

-- One row per (conversation, role, client_message_id) — a retry/double-
-- submit carrying the SAME key upserts onto the SAME row instead of
-- duplicating it. The user and assistant sides of one send share a
-- client_message_id but differ on role, so they never collide here.
create unique index if not exists conversation_turns_client_msg_uidx
    on public.conversation_turns (conversation_id, role, client_message_id)
    where client_message_id is not null;

-- Same idea for the `/v1/ask` answer, keyed on the durable ask_job_id link
-- instead (a resumed poll reuses the same ask_job_id, so it can't
-- duplicate the answer row).
create unique index if not exists conversation_turns_ask_job_uidx
    on public.conversation_turns (conversation_id, role, ask_job_id)
    where ask_job_id is not null;
