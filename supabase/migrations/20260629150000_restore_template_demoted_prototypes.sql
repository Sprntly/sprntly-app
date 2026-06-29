-- Data-only, idempotent: restore template-demoted prototypes that still
-- have a renderable bundle back to a viewable state. A routine template-version
-- bump previously flipped every older 'ready' prototype to 'invalidated', which
-- 404s the View path and drops the PRD screen to the "Generate" CTA — even
-- though the bundle is a self-contained static build that still renders. Flip
-- those rows (and only those: an 'invalidated' row with NO bundle_url has no
-- renderable artifact and is left hidden) back to 'ready'. Re-running matches no
-- rows (no-op). No schema change.
update prototypes
   set status = 'ready'
 where status = 'invalidated'
   and bundle_url is not null;
