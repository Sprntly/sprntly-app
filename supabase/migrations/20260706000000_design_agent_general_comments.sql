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
