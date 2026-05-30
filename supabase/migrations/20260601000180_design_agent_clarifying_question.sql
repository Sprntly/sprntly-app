-- Design Agent clarifying-question pause (P3-08, F12).
--
-- The clarifying_question exit-sentinel (sentinel #1 of AD17's ≤4) pauses the
-- agent loop when the request is genuinely ambiguous. The pause is persisted as
-- a SIDECAR on the prototype row: `pending_question` jsonb holds
-- {question, choices?, context?}. There is deliberately NO new `status` enum
-- value — `pending_question IS NOT NULL` is the "awaiting answer" signal, so a
-- paused prototype stays 'ready' and the share/preview surfaces are unaffected.
-- The answer arrives as a NEW iterate (P3-16), which clears the column.
--
-- Additive + idempotent (`add column if not exists`) so re-applying is a no-op
-- and dropping it is safe (Rollback in the ticket). Suffix 000180 sorts AFTER
-- P3-07's 000150 (iteration_plan) and BEFORE P3-09's 000200 (prd_patches).

alter table prototypes
    add column if not exists pending_question jsonb;
