-- Per-ticket tracker issue type (Jira: Task/Story/Bug/… or the workspace's
-- custom types, from tracker metadata's issue_types). An override like
-- status/priority: NULL = default ("Task" at push time). Editable on the
-- ticket detail for Jira-bound tickets; the sync pushes it best-effort (Jira
-- may refuse cross-workflow type changes) and imports tracker-side changes.
-- Additive + idempotent → safe under migrate-on-deploy.
alter table ticket_edits add column if not exists issue_type text;
