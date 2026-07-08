-- NOTE: version bumped from 20260706000000 → 20260708110000 to resolve a
-- version collision with 20260706000000_clickup_task_map.sql. Two migration
-- files shared version 20260706000000, so `supabase db push` recorded the
-- clickup one and then failed re-inserting the same schema_migrations version
-- for this file (SQLSTATE 23505), which never applied this DDL in prod (leaving
-- prototype_comments.anchor_id still NOT NULL) and blocked every later
-- migration + the gated deploy. The statement is idempotent, so re-applying
-- under the new version is safe.
--
-- General (unpinned) public comments have no element anchor, so anchor_id must
-- accept null. Additive relaxation of the NOT NULL constraint from
-- 20260601000000_design_agent_comments.sql. The btree index on anchor_id stays
-- (btree permits null keys, and existing anchored rows are unaffected).
--
-- A general comment is a plain prototype_comments row with anchor_id = null
-- AND pin_x_pct/pin_y_pct = null (already nullable since
-- 20260606000002_design_agent_comment_position.sql) -- no new table, no new
-- column. The general/pinned split is a render-time filter on pin_x_pct.

alter table prototype_comments alter column anchor_id drop not null;
