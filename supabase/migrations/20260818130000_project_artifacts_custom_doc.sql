-- project_artifacts.artifact_type — admit the team-document kind.
--
-- `project_artifacts` (20260813130000_projects.sql) is the join table binding a
-- project to the artifacts it contains, discriminated by `artifact_type`. Its
-- original CHECK enumerated five kinds — `prd`, `evidence`, `prototype`,
-- `report`, `ticket_set` — and deliberately EXCLUDED custom documents (team
-- documents; the `custom_artifacts` table, "draft a leadership update"). That
-- exclusion was the ONE thing standing between a document generated in a
-- project chat and its project: the read fan-out already returns custom docs
-- (`list_artifacts_for_company`, reused verbatim by `list_artifacts_for_project`)
-- and every project-scoped UI can render the `custom_artifact` kind — the ref
-- simply could never be written, because this CHECK rejected it.
--
-- This adds `custom_artifact` to the allowed set (the existing five unchanged),
-- so a custom doc can be pinned to a project — server-side at generation when
-- the conversation is project-bound, and manually via `POST .../artifacts`.
--
-- DROP-THEN-ADD by the constraint's auto-generated name. The original was an
-- inline column check, which Postgres names `<table>_<column>_check`. `drop …
-- if exists` then `add` is the idempotent shape for a CHECK change (re-running
-- drops the current one and re-creates it identically); a bare `add` would fail
-- the second time, and there is no `alter constraint` for a CHECK body.
--
-- NO `if exists` on the table itself, matching this directory's convention
-- (see 20260816120000_custom_artifacts_error_code.sql): `project_artifacts` is
-- created three days earlier in the same ordered sequence, and this repo blocks
-- every backend deploy on a failed migration — if the table is missing this
-- SHOULD fail loudly rather than silently no-op.
alter table public.project_artifacts
  drop constraint if exists project_artifacts_artifact_type_check;

alter table public.project_artifacts
  add constraint project_artifacts_artifact_type_check
  check (artifact_type in ('prd', 'evidence', 'prototype', 'report', 'ticket_set', 'custom_artifact'));
