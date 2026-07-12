-- Local overrides for tracker custom-field values, keyed by the tracker's
-- field id (Jira "customfield_10031" / ClickUp field uuid). Values are the
-- provider-agnostic normalized shapes (app/connectors/tracker_meta.py):
-- select/user → {id, name}, multiselect/users → [{id, name}], labels →
-- [text], scalars as themselves. NULL = no overrides; writers MERGE into
-- this one column (it holds many fields — a single-field write must never
-- clobber siblings). Additive + idempotent → safe under migrate-on-deploy.
alter table ticket_edits add column if not exists custom_fields jsonb;
