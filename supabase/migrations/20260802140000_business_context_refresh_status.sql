-- Async business-context refresh state — a singleton per tenant.
--
-- POST /v1/company/business-context/refresh used to block the whole HTTP
-- request until run_business_context() (a real, billed research pass)
-- finished — the kind of long synchronous POST that trips browser/proxy
-- timeouts. The refresh now fires in the background (asyncio.create_task,
-- mirroring app/ask_job_runner.py's pattern), and these columns are its
-- durable status handle so a client can poll "is it still running / did it
-- finish / did it error".
--
-- Columns on `companies` rather than a dedicated `_jobs` table (unlike
-- company_research_runs, which DOES warrant its own table because it keeps
-- per-run history — captured fact records a later read reuses without
-- re-paying for a sweep): a business-context refresh has no per-run history
-- worth keeping. Its result IS the stored `business_context` doc; only
-- "what is happening RIGHT NOW" needs to be visible, and at most one refresh
-- is ever meaningful per company at a time. A table would need the same
-- one-row-per-company bookkeeping these columns give for free.
--
-- business_context_refresh_heartbeat_at is bumped periodically by the live
-- worker (see app/business_context_refresh_runner.py) — the orphan sweep
-- keys off ITS age, not business_context_refresh_started_at's, so a
-- genuinely long-but-healthy refresh is never confused with one whose owning
-- process died. This is the same distinction ask_jobs' own heartbeat makes
-- (app/db/asks.py's ORPHAN_ASK_JOB_HEARTBEAT_SECONDS / touch_ask_job) — an
-- age-only check without a heartbeat cost a different feature real answers
-- on staging when a long-but-live run was reaped out from under it.
--
-- Default 'idle' (never NULL): the atomic start-guard compares
-- business_context_refresh_status != 'generating', and SQL's NULL != x is
-- NULL (excluded by WHERE, not true) — a nullable column would silently let
-- a company with no companies row backfill ever pass the same guard as one
-- that already ran. 'idle' sidesteps that entirely.

alter table companies
    add column if not exists business_context_refresh_status text
        not null default 'idle'
        check (business_context_refresh_status in
               ('idle', 'generating', 'done', 'error')),
    add column if not exists business_context_refresh_error text,
    add column if not exists business_context_refresh_started_at timestamptz,
    add column if not exists business_context_refresh_heartbeat_at timestamptz;
