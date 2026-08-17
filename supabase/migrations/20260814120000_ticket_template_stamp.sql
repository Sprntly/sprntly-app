-- Which uploaded ticket format a generated set was rendered with.
--
-- Tickets already carry their description layout PER STORY (Story.description_
-- layout, stamped at generation so a pushed ticket keeps round-tripping under
-- the labels it was written with) — but nothing records WHICH format produced
-- the set. That id is what the Tickets panel's "Format: {name}" label and the
-- in-place format switch (POST /v1/stories/change-template) both need: the
-- label has to name the current format without reverse-engineering it from
-- layout JSON, and the switch has to no-op when asked for the format the set
-- is already in.
--
-- Mirrors prds.artifact_template_id exactly: nullable text, NO foreign key —
-- a format's deletion must not cascade into generated artifacts, and the stamp
-- surviving deletion is by design (the PRD stamp's 20260806160000 header).
-- NULL = Sprntly's built-in layout, which is what every pre-existing row and
-- every company without an uploaded ticket format uses.
--
-- Additive only: two nullable columns, no defaults, no rows rewritten.

alter table prd_tickets add column if not exists artifact_template_id text;

alter table ticket_sets add column if not exists artifact_template_id text;
