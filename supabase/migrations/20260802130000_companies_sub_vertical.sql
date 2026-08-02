-- companies.sub_vertical was never created by a migration, despite the
-- application code (business_context_agent.py's _company_row, the
-- BusinessContext doc's identity.sub_vertical field, website_analysis.py)
-- treating it as a real, actively-used onboarding/research concept
-- throughout. Its sibling `industry` was added in
-- 20260525150000_onboarding_workspace.sql; sub_vertical never got the same
-- treatment.
--
-- Consequence: `_company_row()` selects it unconditionally as the first step
-- of run_business_context(), so every business-context generate/refresh call
-- 500s before any real work starts, for every tenant, regardless of whether
-- a doc already exists.
--
-- Purely additive, no backfill: existing rows get null, which
-- `_company_row()`/`row.get("sub_vertical")` already handles gracefully
-- (falsy-checked before use).

alter table companies add column if not exists sub_vertical text;
