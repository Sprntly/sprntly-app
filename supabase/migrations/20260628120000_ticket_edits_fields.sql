-- Per-ticket editable metadata: title override + Priority/Status/Sprint pickers
-- + assignee (Person responsible / Reassign). These complement the existing
-- description / acceptance_criteria overrides on ticket_edits. Idempotent so it
-- is safe under migrate-on-deploy.
alter table ticket_edits add column if not exists title    text;
alter table ticket_edits add column if not exists priority text;
alter table ticket_edits add column if not exists status   text;
alter table ticket_edits add column if not exists sprint   text;
-- assignee is the picked team member: {user_id, display_name, email, role, avatar_url}
alter table ticket_edits add column if not exists assignee jsonb;
