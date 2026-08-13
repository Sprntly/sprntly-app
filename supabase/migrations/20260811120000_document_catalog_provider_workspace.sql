-- Record WHICH provider-side workspace a catalogued document came from.
--
-- `document_catalog` rows are scoped to a company, but a company's connection
-- to a provider is not permanent: a Slack install can be disconnected and
-- replaced by an install into a DIFFERENT workspace, and the rows the old
-- install wrote stay behind. They are then indexed as documents the company
-- has, are rankable, and can be asserted as the subject of a question — after
-- which the body fetch fails and the user is told the contents could not be
-- loaded, which reads as a transient fault when the truth is that the
-- document is not connected any more.
--
-- Measured on the shared database 2026-08-11: one such row across the whole
-- fleet (66 catalog rows), a Slack channel whose permalink workspace matches
-- no active connection its company holds, written 18 minutes BEFORE that
-- company's current Slack connection row was created.
--
-- #1119 could not address this. It removes rows on channel DESELECTION, which
-- is a user action with an explicit id list; the disconnect path has no such
-- list, and the question it needs answered first — "does any connection still
-- serve this company?" — was not answerable from stored state, because
-- nothing recorded which workspace a row came from. A Slack row can be a
-- personal install later promoted to serve the company, so a rule that purged
-- on disconnect could delete a catalog that is still live. This column is
-- what makes that question answerable WITHOUT guessing.
--
-- For Slack this holds the workspace/team id (`T…`), read from the stored
-- connection config — NOT from the permalink subdomain, which is a display
-- name a workspace admin can change, and NOT from a fresh API call, which can
-- fail. Taking it from the same stored value the future rule will compare
-- against is what makes that comparison exact by construction rather than by
-- convention. The column is named for the general case (Confluence's cloud
-- id, a Drive account) so the next provider needs no second column and no
-- rename — renaming a column on a shared prod database is exactly the kind of
-- destructive DDL this table should never need.
--
-- NULL MEANS UNKNOWN, NOT ORPHANED, and every future consumer must treat it
-- that way. There is deliberately no backfill here: registration is
-- content-hash keyed, so a row only rewrites when its document CHANGES, and
-- the population of this column therefore converges over time rather than
-- immediately. `register_document` fills a missing value in place when it
-- next sees the row (no model call, no summary churn), but a document nobody
-- touches again keeps NULL indefinitely. A cleanup rule that read NULL as
-- "matches no connection" would delete exactly the quietest tenants' rows.
--
-- Additive and nullable on purpose: the running binary neither writes nor
-- reads this column, so the old prod process keeps working unchanged against
-- the new schema until cutover.
--
-- No index. The whole table is 66 rows fleet-wide and the per-company reads
-- already ride `document_catalog_company_idx`; an index here would be write
-- cost bought for a query shape that does not exist yet.
alter table document_catalog
    add column if not exists provider_workspace_id text;

comment on column document_catalog.provider_workspace_id is
    'Provider-side workspace this document came from (Slack team id; '
    'Confluence cloud id when populated). NULL means UNKNOWN, never orphaned '
    '— never delete a row on the strength of a NULL here.';
