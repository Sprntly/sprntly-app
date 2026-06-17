-- Phase 2 of the weekly-brief lifecycle: turn brief_finding_state (added in
-- 20260616130000) into the theme-keyed USER-ACTION store, on top of its existing
-- role as the de-dup fingerprint memory.
--
-- `action` records what the user did with a theme that was surfaced as a brief
-- finding:
--   surfaced     — appeared in a brief, no action taken yet (default)
--   prd_created  — a PRD was generated for this brief insight
--   dismissed    — the user dismissed the finding
--   done         — the work was marked complete
--
-- "Completed" (the Backlog screen's Completed tab) = themes whose action is
-- 'prd_created' OR 'done'. "Backlog" = everything else non-brief (unchanged).

alter table brief_finding_state
    add column if not exists action text not null default 'surfaced';

alter table brief_finding_state
    drop constraint if exists brief_finding_state_action_check;
alter table brief_finding_state
    add constraint brief_finding_state_action_check
    check (action in ('surfaced', 'prd_created', 'dismissed', 'done'));
