-- Rebase the onboarding resume marker again — the invite step removed.
--
-- `companies.onboarding_step` is a 1-based INDEX into ONBOARDING_STEP_SLUGS
-- (web/app/lib/onboarding/types.ts). Migration 20260903160000 rebased it from
-- the ten-step flow onto the five-step one that shipped moments earlier the
-- same day; this rebases it again now that the invite step (bulk teammate
-- invite: paste + CSV) has also been removed, folded into Settings → Team &
-- roles, and the flow is four steps.
--
-- Nothing at runtime can tell an index meant under the five-step flow from one
-- meant under the four-step flow — the same integer names a different screen —
-- so this is fixed once, here, exactly as the prior rebase was.
--
-- NOT DESTRUCTIVE. It moves a resume pointer for signups still in flight; no
-- column is dropped, and every field the invite step collected (an outgoing
-- invite) was already sent when Continue was pressed — nothing about a
-- teammate invite is undone by this.
--
-- Five-step index -> four-step index, each removed step mapping FORWARD to the
-- next step that still exists:
--
--   1 company     -> 1 company
--   2 connectors  -> 2 connectors
--   3 invite      -> 3 review       (step removed)
--   4 review      -> 3 review
--   5 personalize -> 4 personalize
--
-- SCOPED TO UNFINISHED ONBOARDING, same guard as the prior rebase — a company
-- that already completed never reads this column again.
--
-- Runs once. Deliberately NOT re-runnable: applied twice it would walk
-- in-flight companies backwards.
update companies
   set onboarding_step = case
           when onboarding_step <= 2 then onboarding_step
           when onboarding_step <= 4 then 3
           else 4
       end
 where onboarding_completed_at is null
   and onboarding_step is not null
   and onboarding_step > 2;
