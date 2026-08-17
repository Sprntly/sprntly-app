-- A durable marker for a ticket format switch that is still running.
--
-- POST /v1/stories/change-template used to do the whole re-lay inside the
-- request: the confirm dialog sat on "Switching…" for as long as the fill call
-- took, and a user who navigated away lost the switch entirely — the request
-- was the only thing holding the work. The switch is now scheduled as a
-- background task and the response returns immediately, which leaves a window
-- where the row is unchanged on disk but a re-lay IS in flight. This column is
-- how a client that comes back mid-flight learns that.
--
-- Shape (null = no switch running, which is every row at rest):
--
--   {"status": "running", "template_id": "tpl-…" | null, "started_at": "<iso>"}
--
-- `template_id` is the TARGET, and null is a real value there (a stamped set
-- switching back to Sprntly's built-in layout) — which is exactly why this is
-- one jsonb object and not a bare nullable id column: "switching to the
-- built-in" and "not switching" have to be distinguishable.
--
-- `started_at` exists for the strand case. The task lives in the API process,
-- so a restart mid-switch leaves the marker set with nothing left to clear it;
-- readers treat a marker older than `RELAYOUT_STALE_AFTER_S`
-- (app/stories/relayout.py) as not running rather than spinning a client
-- forever. The tickets themselves are untouched in that case — the re-lay
-- writes the stories and clears the marker in the same update — so a stranded
-- marker costs a stale label, never a ticket.
--
-- Deliberately NOT the existing `status` column. Moving a ticket row to
-- `generating` for a re-lay would make GET /for-prd report `fresh: false`, and
-- the Tickets tab answers that by kicking off /generate — a full regeneration,
-- which mints new ticket ids and orphans every issue already synced to the
-- customer's tracker. A re-lay preserving ticket identity is the whole point of
-- the operation (app/stories/relayout.py's header), so its in-flight state must
-- live somewhere the generation path does not read.
--
-- Additive only: one nullable column per table, no default, no rows rewritten.

alter table prd_tickets add column if not exists relayout jsonb;

alter table ticket_sets add column if not exists relayout jsonb;
