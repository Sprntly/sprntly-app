-- Make Slack connections PER-USER.
--
-- Background / bug
-- ----------------
-- The `connections` table is company-scoped: one row per
-- (company_id, provider), shared by every member of the company. That is
-- correct for every connector EXCEPT Slack.
--
-- Slack is a personal notification target: each user installs the bot
-- into THEIR own Slack and picks THEIR own channel for DMs/briefs. With a
-- single company-shared Slack row:
--   - member B reads member A's Slack bot token + channel list,
--   - member B posts as member A's bot,
--   - member B disconnecting kills member A's Slack,
--   - personal notifications cannot be delivered to each user.
--
-- This migration scopes Slack rows by `user_id` while leaving every other
-- provider company-scoped and member-shared.
--
-- Approach
-- --------
--   1. Add a nullable `user_id` column (matches how user ids are referenced
--      elsewhere: company_members.user_id is a bare uuid pointing at the
--      Supabase auth user / profiles.id).
--   2. Replace the broad `unique (company_id, provider)` constraint with
--      TWO partial unique indexes:
--        - non-Slack: unique (company_id, provider) where provider <> 'slack'
--          (unchanged behaviour — one row per provider per company),
--        - Slack:     unique (company_id, user_id, provider) where provider = 'slack'
--          (each user gets their own Slack row; two users in one company can
--          both connect).
--   3. Existing Slack rows keep user_id = NULL. NULL-user Slack rows are
--      excluded from every per-user read (lookups filter on user_id), so
--      those installs are effectively orphaned and the affected users simply
--      reconnect. No fuzzy backfill — we cannot know which member a shared
--      row "belonged" to.
--
-- Apply note: applied to the live Supabase project post-merge. Idempotent
-- via if-exists / if-not-exists guards.

alter table connections
    add column if not exists user_id uuid;

create index if not exists connections_user_id_idx
    on connections (user_id);

-- Drop the broad company-scoped uniqueness. It is replaced below by a
-- partial index that excludes Slack, so non-Slack providers keep exactly
-- one row per (company_id, provider) while Slack becomes per-user.
alter table connections
    drop constraint if exists connections_company_provider_key;

-- Non-Slack providers: one connection per provider per company (unchanged).
create unique index if not exists connections_company_provider_non_slack_key
    on connections (company_id, provider)
    where provider <> 'slack';

-- Slack: one connection per user per company. Two members of the same
-- company can each connect their own Slack.
create unique index if not exists connections_company_user_slack_key
    on connections (company_id, user_id, provider)
    where provider = 'slack';
