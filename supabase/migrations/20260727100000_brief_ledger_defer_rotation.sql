-- Top Insights phase 2A: grow brief_finding_state into the skill's LEDGER
-- (skills/top-insights/SKILL.md step 5 + BUILD-BRIEF.md §5).
--
-- Three additions on the existing per-(enterprise, theme) row:
--
--   times_shown     — how many times this theme has surfaced as a brief card.
--                     Drives rotation exhaustion: 3 cards with no user action
--                     retires the theme to the backlog ("nagging is not
--                     persistence").
--   deferred_until  — the "Defer / not now" action: interested, wrong moment.
--                     Suppressed until this instant, then re-enters the pool at
--                     full rank. Distinct from dismissed (which stays out until
--                     the issue materially worsens) — see the action walk below.
--   last_state      — the freshness state the theme carried the last time it
--                     surfaced ('new' | 'updated'), audit/debug surface for
--                     "why is this card here".
--
-- `action` gains 'deferred':
--   surfaced → prd_created | dismissed | deferred | done

alter table brief_finding_state
    add column if not exists times_shown integer not null default 0;
alter table brief_finding_state
    add column if not exists deferred_until timestamptz;
alter table brief_finding_state
    add column if not exists last_state text;

alter table brief_finding_state
    drop constraint if exists brief_finding_state_action_check;
alter table brief_finding_state
    add constraint brief_finding_state_action_check
    check (action in ('surfaced', 'prd_created', 'dismissed', 'deferred', 'done'));

alter table brief_finding_state
    drop constraint if exists brief_finding_state_last_state_check;
alter table brief_finding_state
    add constraint brief_finding_state_last_state_check
    check (last_state is null or last_state in ('new', 'updated'));

-- Existing rows: themes surfaced before this migration have been shown at
-- least once; a 0 would give every historical theme a free extra rotation.
update brief_finding_state set times_shown = 1
where times_shown = 0 and last_surfaced_at is not null;
