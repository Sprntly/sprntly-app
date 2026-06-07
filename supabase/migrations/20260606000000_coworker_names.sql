-- Coworker names — the user-given names for their four AI coworkers
-- (Product / Design / Data Science / Admin), captured on design-v4
-- onboarding page 07. Stored as a jsonb map keyed by coworker slot so
-- new slots don't need a schema change. Empty map until named.
alter table companies
    add column if not exists coworker_names jsonb not null default '{}'::jsonb;
