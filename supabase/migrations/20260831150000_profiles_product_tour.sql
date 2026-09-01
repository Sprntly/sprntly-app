-- Per-USER "has seen the product tour" marker.
--
-- It has to live on profiles rather than on the workspace, because the two
-- people who need the tour arrive by different doors and only one of them is
-- ever attached to a fresh workspace:
--
--   * the owner who just finished onboarding — their workspace's
--     onboarding_completed_at was set moments ago, and
--   * an invited member on their first sign-in — who joins a workspace that
--     was onboarded long before they existed, so every workspace-level
--     marker already reads "done" for them.
--
-- A workspace-scoped flag would therefore show the tour to the owner and
-- silently skip it for everyone they invite, which is the opposite of what
-- was asked for.
--
-- NULL means "has not finished it". Set to now() when the tour is completed
-- OR skipped — a skip is a decision, and re-showing something someone
-- dismissed is how a welcome mat becomes a nuisance.
--
-- Additive and idempotent. No backfill: every existing user reads NULL and
-- would see the tour once on their next visit, which is the intended
-- behaviour for people who have never been shown it.
alter table profiles
    add column if not exists product_tour_completed_at timestamptz;

comment on column profiles.product_tour_completed_at is
    'When this user finished or skipped the first-run product tour. NULL = not yet shown.';
