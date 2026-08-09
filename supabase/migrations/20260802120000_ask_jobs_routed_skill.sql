-- Ask jobs — record the routed skill AT ROUTING TIME, not at completion.
--
-- `qa_agent.route()` picks a skill for an ask in the first second or two
-- (keyword fast-path, else the haiku classifier), but that choice was only ever
-- written to `ask_jobs.response` by `complete_ask_job`, i.e. at the END of the
-- run. While `status = 'generating'` the payload is still `{}`, so the chat's
-- waiting surface could not name what it was waiting FOR until there was
-- nothing left to wait for. A competitive review that runs for minutes showed
-- exactly the same blank wait as a two-second answer.
--
-- Two nullable columns rather than an early partial `response` write: both
-- `complete_ask_job` and `fail_ask_job` are guarded on `status = 'generating'`
-- and treat `response` as the finished answer, so seeding that jsonb mid-run
-- would blur the "is this row finished?" question these guards rest on. A
-- dedicated column is inert to every existing read path.
--
-- NULL is meaningful and load-bearing: it means "the router selected no skill"
-- (a direct answer, an out-of-scope refusal, or one of the pre-routing
-- interceptors in qa_agent.answer that never consults the router at all). The
-- UI renders no chip in that case rather than inventing a default, so nothing
-- here is backfilled and there is no default value.
--
-- Additive and forward-only. `routed_skill` mirrors the payload's `_skill`
-- (a skill id) and `routed_skill_action` mirrors `_skill_action` (the human
-- label the same decision carries), so the label shown mid-run is the label
-- shown when the answer lands.

alter table public.ask_jobs add column if not exists routed_skill text;
alter table public.ask_jobs add column if not exists routed_skill_action text;
