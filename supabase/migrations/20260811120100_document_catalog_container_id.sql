-- Record the CONTAINER a catalogued document lives in.
--
-- Deregistration needs to answer "is this row still covered by the user's
-- selection?", and for Confluence the two sides of that question do not join.
-- The catalog's `external_id` is a PAGE id; the selection
-- (`connections.config.sync_space_ids`) is a list of SPACE ids. Nothing
-- stored connects them: `source_name` holds the space DISPLAY NAME, which
-- does not join to ids, and the only other route is parsing `/spaces/<KEY>/`
-- out of `url` — which is a KEY, not an id, and changes when a space or site
-- is renamed. A deletion keyed on that would silently start matching nothing
-- (delete never fires) or the wrong thing (delete fires wrongly) the first
-- time a customer renames a space, with no error either way.
--
-- So the container id is written at registration from the value the puller
-- already holds (`space["id"]` in kg_ingest/pullers/confluence.py), and the
-- deletion compares stored id against stored id.
--
-- Named for the general case, not `space_id`: `document_catalog` is
-- provider-generic (uploads / google_drive / confluence / slack /
-- chat_attachment) and a Drive FOLDER id or a Slack channel id occupies the
-- same slot. One column serves them all, and renaming a column on a shared
-- production database later is exactly the destructive DDL this table should
-- never need.
--
-- NULL MEANS UNKNOWN, NOT UNCOVERED, and the deregistration rule must ignore
-- NULL rows rather than treat them as unmatched. That is what makes shipping
-- without a backfill safe: every row written before this column existed sits
-- at NULL, and the pre-repair window is a no-op rather than a mis-delete.
-- `register_document` repairs the value on its unchanged-content
-- short-circuit, and the 6-hourly Confluence pull walks and re-catalogues
-- every page in the selected spaces (not only recent ones), so existing rows
-- fill in within one cycle without a data migration.
--
-- Additive and nullable: the running binary neither reads nor writes this
-- column, so the old prod process keeps working unchanged against the new
-- schema until cutover.
alter table document_catalog
    add column if not exists container_id text;

-- The deletion's exact access path: "every row for this company and provider
-- whose container is one of these". Cheap to carry — the table is small — and
-- it is the one query shape this column exists to serve.
create index if not exists document_catalog_container_idx
    on document_catalog (company_id, provider, container_id);

comment on column document_catalog.container_id is
    'Provider-side container this document lives in (Confluence space id; a '
    'Drive folder id or Slack channel id would fit the same slot). NULL means '
    'UNKNOWN, never uncovered — deregistration must ignore NULL rows, not '
    'treat them as unmatched by the current selection.';
