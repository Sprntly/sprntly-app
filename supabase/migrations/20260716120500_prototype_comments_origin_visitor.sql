-- Public/internal comment isolation for the share-link surface.
--
-- `origin` discriminates who created a comment: 'internal' (the authed team
-- surface) or 'public' (an anonymous share-link viewer). The public by-token
-- list filters to origin='public' only, so internal team discussion (and the
-- real display names resolved from profiles) can never be served to an
-- anonymous holder of a share link. The authed team view is unchanged and
-- keeps reading every row regardless of origin.
--
-- Backfill is fail-closed by design: every pre-existing row takes the column
-- DEFAULT 'internal' — including any historical comment that was actually
-- created through the public route (they are not distinguishable retroactively
-- with certainty; a `user_id IS NULL` heuristic would misclassify early 'demo'
-- rows). Consequence: previously-created public comments stop appearing on the
-- PUBLIC list (the internal view keeps them). Privacy-safe direction — no
-- internal comment can ever leak; some external comments go quiet externally.
--
-- `visitor_id` is a server-minted opaque identity for the anonymous visitor,
-- carried in an HttpOnly cookie and stored so a visitor's own comments can be
-- marked `mine` on the public list. Nullable with NO DEFAULT: internal rows
-- (and pre-existing rows) have no visitor identity; null is honest absence.
-- The value is never serialized in any response and never logged.
--
-- Inherits prototype_comments.workspace_id — no new table, no new isolation
-- surface. Additive and idempotent (add column if not exists) so re-applying
-- is a no-op.

alter table prototype_comments
    add column if not exists origin text not null default 'internal'
        check (origin in ('internal', 'public'));
alter table prototype_comments
    add column if not exists visitor_id text;
