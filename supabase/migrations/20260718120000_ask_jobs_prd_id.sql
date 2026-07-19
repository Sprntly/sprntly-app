-- PRD-tab chat grounding: which PRD (if any) an ask was grounded on.
-- Mirrors ask_jobs.conversation_id — audit/debug linkage only, no FK (asks
-- must outlive a pruned PRD) and no read path filters on it yet.
alter table public.ask_jobs add column if not exists prd_id bigint;
