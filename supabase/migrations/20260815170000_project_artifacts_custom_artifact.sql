-- Widen the sibling join table so an uploaded document (a custom_artifact)
-- can be attached to a project. Sibling table only — NOT prds/briefs/evidences.
-- Idempotent: drop-if-exists then re-add, so a double-apply is a no-op.
alter table project_artifacts drop constraint if exists project_artifacts_artifact_type_check;
alter table project_artifacts add constraint project_artifacts_artifact_type_check
  check (artifact_type in ('prd', 'evidence', 'prototype', 'report', 'ticket_set', 'custom_artifact'));
