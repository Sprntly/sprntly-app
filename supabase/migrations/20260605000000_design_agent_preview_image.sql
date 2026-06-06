-- Design Agent prototype preview image.
--
-- Adds a nullable URL column holding a lightweight screenshot of the generated
-- prototype bundle, captured once on generation-complete. The preview card reads
-- this column to show a real thumbnail instead of a heavy live iframe or a
-- neutral placeholder.
--
-- Nullable with NO default: capture is best-effort and honest-degrade. When the
-- screenshot cannot be produced (no browser runtime, navigation failure, timeout)
-- the prototype still completes ready and this column stays null — the card falls
-- back to its existing placeholder. No fake/placeholder image is ever stored.
--
-- The column inherits prototypes.workspace_id (no new table, no new isolation
-- surface). Additive + idempotent (`add column if not exists`) so re-applying is
-- a no-op; dropping it is safe (a derived, re-derivable value).

alter table prototypes
    add column if not exists preview_image_url text;
