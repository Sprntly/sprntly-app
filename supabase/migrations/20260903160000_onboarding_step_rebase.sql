-- Rebase the onboarding resume marker onto the five-step flow.
--
-- `companies.onboarding_step` is a 1-based INDEX into ONBOARDING_STEP_SLUGS
-- (web/app/lib/onboarding/types.ts), not a screen name. That array went from
-- ten entries to five on 2026-09-03, so the same integer now names a different
-- screen: 3 meant `connectors` and now means `invite`.
--
-- Nothing at runtime can tell the two apart — a legacy 3 and a current 3 are
-- the same value — so this is fixed once, here, rather than translated on every
-- read. A read-time translation would have to fire for new rows too and would
-- permanently resume everyone a step or two behind where they left off.
--
-- NOT DESTRUCTIVE. It moves a resume pointer for signups still in flight; no
-- column is dropped, no answer is discarded, and every field those removed
-- steps collected stays exactly where it was and is now edited in Settings.
--
-- Each removed step maps FORWARD to the next step that still exists, so nobody
-- is sent back to redo work and nobody skips a step they never saw:
--
--    1 company        -> 1 company
--    2 import-context -> 2 connectors   (step removed)
--    3 connectors     -> 2 connectors
--    4 api-key        -> 2 connectors   (step removed)
--    5 product        -> 3 invite       (step removed)
--    6 workspace      -> 3 invite       (step removed)
--    7 metrics        -> 3 invite       (step removed)
--    8 invite         -> 3 invite
--    9 review         -> 4 review
--   10 personalize    -> 5 personalize
--
-- SCOPED TO UNFINISHED ONBOARDING. A company that already completed never reads
-- this column again, so rewriting its marker would churn rows to no effect —
-- and leaving them alone keeps the historical value intact.
--
-- Runs once (the migration history enforces that). It is deliberately NOT
-- re-runnable: applied twice it would walk in-flight companies backwards, which
-- is why the guard below is the completion check and not a value range.
update companies
   set onboarding_step = case
           when onboarding_step <= 1 then 1
           when onboarding_step <= 4 then 2
           when onboarding_step <= 8 then 3
           when onboarding_step = 9 then 4
           else 5
       end
 where onboarding_completed_at is null
   and onboarding_step is not null
   and onboarding_step > 1;
