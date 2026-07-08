-- Design Agent grounding-degrade signal.
--
-- A codebase-grounded generation request (design_source == "github") can
-- silently degrade to a shell-only or blank-canvas prototype when the
-- repository map or the selected screen could not be resolved. Today nothing
-- tells the user this happened. `grounding_note` is a nullable sidecar (NOT a
-- new status — mirrors pending_question's sidecar pattern): NULL means full
-- grounding (or a non-codebase source); a non-null string is a plain-English
-- note set once at generation time describing which grounding tier the run
-- actually landed on.
--
-- Additive + idempotent (`add column if not exists`).

alter table prototypes
    add column if not exists grounding_note text;
