-- Durable comment position storage for the mark-and-comment pin flow.
--
-- Makes pin positions durable and visible to all viewers. Without these columns
-- a pin's on-canvas location lives only in the placing user's local state and
-- vanishes on refresh; every other viewer sees the comment text but no pin.
-- These columns store the viewport-relative position (pin_x_pct / pin_y_pct,
-- both 0..100) and the resolved stable JSX anchor at the click point
-- (resolved_anchor_id) so a pin can re-attach to the same element after
-- regeneration. The resolved anchor is a data-anchor-id value from the live
-- bundle and is supplementary to the existing anchor_id column (which carries
-- the synthetic pin-<n> marker used for list keying and the orphan/re-attach
-- walk); it is stored BESIDE the existing primitive, not in place of it.
--
-- All three columns are nullable with NO DEFAULT: a comment created via the
-- right-click anchor path (not a pin) has no position; null is honest absence,
-- never a fabricated 0,0. Inherits prototype_comments.workspace_id — no new
-- table, no new isolation surface.
--
-- Additive and idempotent (add column if not exists) so re-applying is a no-op.
-- Dropping the columns is safe because the values are re-derivable by re-pinning.
--
-- HANDOFF NOTE: this is the second pending Design Agent migration in the current
-- release wave (after 20260605000000_design_agent_preview_image.sql). Neither
-- has run on production yet. Both must be applied before the feature flag flip.

alter table prototype_comments
    add column if not exists pin_x_pct          double precision;
alter table prototype_comments
    add column if not exists pin_y_pct          double precision;
alter table prototype_comments
    add column if not exists resolved_anchor_id text;
