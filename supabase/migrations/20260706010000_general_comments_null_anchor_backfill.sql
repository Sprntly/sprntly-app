-- One-time data cleanup: general (unpinned) comments now unify on
-- anchor_id = null everywhere (public AND authed). Before this, the
-- authed-only freeform composer posted the truthy sentinel string 'general'
-- instead of a real null, which the null-based general/pinned split and the
-- null-based auto-grounding exclusion both missed. The composer itself is
-- fixed to stop creating new sentinel rows (CommentsPanel.tsx); this migration
-- rewrites any that already exist so every general comment, old or new,
-- resolves to the single null representation.
--
-- Additive + reversible-by-drop (no column/shape change, just a value
-- rewrite); safe to run more than once (idempotent -- the WHERE clause matches
-- nothing on a second run).

update prototype_comments set anchor_id = null where anchor_id = 'general';
