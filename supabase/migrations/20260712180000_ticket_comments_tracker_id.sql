-- One-way comment push (Sprntly → tracker): the tracker-side comment id a
-- Sprntly comment was pushed as (Jira issue comment / ClickUp task comment).
-- NULL = not pushed yet; the instant push at comment time sets it, and the
-- sync pass catches up any comment that failed (only comments created AFTER
-- the PRD was bound — history is never flooded into the tracker). Additive +
-- idempotent → safe under migrate-on-deploy.
alter table ticket_comments add column if not exists tracker_comment_id text;
