-- Screenshot-as-context: store the staged upload key on the prototype row.
--
-- `screenshot_key` is a generate-time input snapshot mirroring figma_file_key /
-- website_url: the /uploads/screenshot route stages the image into storage under
-- `uploads/{workspace_id}/{uuid}.{ext}` and /generate persists the returned key
-- here, so iterate / manual-edit re-read the SAME image the prototype was
-- generated against (never a fresh upload). Nullable — existing rows and
-- screenshot-less generates read NULL. Additive + idempotent for
-- migrate-on-deploy; no index (only ever read by primary-key row fetch).
alter table prototypes add column if not exists screenshot_key text;
