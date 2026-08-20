-- Goal Analysis (engine: Crucible) — the run's report, as an editable document.
--
-- Plan: backend/docs/GOAL_ANALYSIS.md §4.4. Spec: backend/docs/crucible/.
-- Two additive nullable columns on `crucible_runs` and one index. No table is
-- created and no existing column changes, so an old binary keeps running
-- unchanged against this schema.
--
-- WHAT THIS ADDS. A finished run can be rendered into a `custom_artifacts` row
-- — the same shared, rich-text, team-editable document every other artifact
-- lands in — via POST /v1/crucible/{id}/document. Reusing that table rather
-- than growing a report body onto `crucible_runs` is what gives the report
-- autosave, optimistic concurrency, PDF export, share links and the library
-- listing for free, and it keeps the run row the size of job state.
--
-- WHY `on delete set null` AND NOT CASCADE. The run is the immutable record of
-- what was analysed; the document is a document ABOUT it. Deleting a document
-- from the library is an everyday editorial act and must never destroy the
-- claims, findings, ledger and predictions that justified it — under cascade,
-- one delete in the Others section would silently take a multi-minute run and
-- its calibration history with it. The pointer goes NULL, the run stays, and
-- asking for the document again re-renders it.
--
-- WHY A HASH AND NOT A BOOLEAN `detached` FLAG. A report is "detached" — hand
-- edited, and therefore no longer safe to overwrite from the run — iff the
-- artifact's current `body_html` hashes to something other than what we stored
-- at render time. Derived, not declared, because the edit does not come through
-- crucible: a member edits the document through the generic
-- PATCH /v1/custom-artifacts/{id}, which knows nothing about runs and must not
-- have to. A flag would need every writer of that endpoint to remember to set
-- it, and the failure mode of forgetting is regeneration silently discarding
-- someone's edits. The hash cannot be forgotten, only recomputed.
--
-- A RENAME DOES NOT DETACH, and that falls out of hashing the body alone:
-- `custom_artifacts.title` is not covered, so retitling a report from the
-- listing leaves it regenerable. That is the intended reading of "edited".
--
-- LIMITS, stated rather than assumed:
--   * The hash is over `body_html` EXACTLY as stored — byte-for-byte, after
--     whatever normalisation the write path applied. It is not semantic: a
--     no-op whitespace edit that round-trips through the editor counts as a
--     detach. Failing that way is the safe direction (we decline to overwrite
--     a document that may hold work), and the reverse is not.
--   * NULL means "never rendered, or rendered before this column existed".
--     A NULL hash on a run that HAS an `artifact_id` must be read as unknown,
--     not as clean — do not regenerate over it.
--
-- The reverse lookup — given a document, which run does it belong to? — is
-- what the chat edit tool does to resolve its target, so `artifact_id` is
-- indexed. Partial, because the overwhelming majority of runs never have a
-- document (one is only created when someone asks for one) and NULL rows have
-- nothing to find; a lookup is always for a concrete id.
--
-- NO RLS CHANGES ARE NEEDED. `crucible_runs` already has its service-role-only
-- policy from 20260819100000_crucible_core.sql, and `custom_artifacts` has its
-- own; adding columns does not alter either. Noted here so a reviewer checking
-- for the missing `TO service_role` clause of 20260812170000 can stop looking.

alter table public.crucible_runs
  add column if not exists artifact_id bigint
    references custom_artifacts (id) on delete set null;

alter table public.crucible_runs
  add column if not exists report_body_hash text;

comment on column public.crucible_runs.artifact_id is
  'The custom_artifacts document this run''s report was rendered into, or NULL '
  'if no report has been asked for. ON DELETE SET NULL: deleting the document '
  'must never delete the run, which is the immutable record the document '
  'merely describes.';

comment on column public.crucible_runs.report_body_hash is
  'sha256 hex of custom_artifacts.body_html exactly as stored at render time. '
  'The report is detached — hand edited, and no longer regenerated from the '
  'run — iff the artifact''s current body hashes to anything else. Derived '
  'rather than a flag so an edit through the generic custom-artifacts PATCH '
  'detaches it without that endpoint knowing crucible exists. A rename does '
  'not detach: the title is not covered. NULL means unknown, not clean.';

create index if not exists crucible_runs_artifact_idx
    on crucible_runs (artifact_id)
    where artifact_id is not null;
