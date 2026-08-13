-- Nullable prototype anchor on prd_patches (F11).
--
-- The Design-Agent PRD-patch flow always anchors a patch to the prototype it
-- was proposed against (prototype_id NOT NULL, from 20260601000200). The
-- project-chat PRD-edit flow proposes a patch straight against a project's PRD
-- with no prototype in the loop, so the anchor must be optional. Drop NOT NULL
-- while keeping the FK + on-delete-cascade to prototypes(id) intact — a
-- Design-Agent patch still cascades away with its prototype; a project patch
-- (prototype_id NULL) simply has nothing to cascade from.
--
-- Idempotent: `drop not null` is a no-op when the column is already nullable,
-- so applying this twice succeeds and leaves the column nullable. The FK is not
-- touched, so its cascade behaviour is unchanged.
--
-- DOWN-NOTE: `alter table prd_patches alter column prototype_id set not null`
-- is safe ONLY while no project-anchored (prototype_id IS NULL) rows exist —
-- once a project chat has proposed a patch, re-adding NOT NULL would fail on
-- those rows. Clear/backfill them before any such rollback.

alter table prd_patches alter column prototype_id drop not null;
