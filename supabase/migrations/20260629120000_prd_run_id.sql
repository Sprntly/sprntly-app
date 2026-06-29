-- Associate a PRD row with the multi-agent run that produced it.
--
-- The multi-agent "Generate PRD" endpoint creates the PRD row synchronously
-- and stamps it with the run_id so repeat clicks for the same (brief, insight)
-- can resolve the in-flight / completed run instead of restarting it.
--
-- Nullable: single-PRD generations (routes/prd.py) leave this NULL.
alter table prds add column if not exists run_id text;
