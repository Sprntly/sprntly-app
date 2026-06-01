-- Design Agent Plan/Discuss mode storage (P3-07, AD10).
--
-- Plan mode runs a restricted (no-write) tool registry and ends by emitting a
-- SHORT textual plan instead of mutating the bundle. That plan is persisted on
-- the pending-iteration row so the Plan->Execute transition (confirm-plan) can
-- carry it back as a system addendum for the follow-up execute run.
--
-- Two senses share this one column (both are "the plan tied to this iteration"):
--   * a mode='plan'  row  -> the plan the agent EMITTED (output, written post-run)
--   * a mode='execute' confirm row -> the APPROVED plan to prepend (input, at enqueue)
--
-- Additive + idempotent (`add column if not exists`) so re-applying is a no-op
-- and dropping it is safe (Rollback in the ticket). Suffix 000150 sorts AFTER
-- P3-06's 000100 (pending_iterations table) and BEFORE P3-08's 000180.

alter table prototype_pending_iterations
    add column if not exists plan text;
