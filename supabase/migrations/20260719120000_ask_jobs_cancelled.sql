-- Ask jobs — add a `cancelled` terminal status.
--
-- The chat composer now offers a Stop control while an answer is generating
-- (the user realizes it was the wrong question). Stopping POSTs
-- /v1/ask/{id}/cancel, which flips the job's status to `cancelled` so the
-- in-flight worker short-circuits at its next checkpoint (saving the expensive
-- answer LLM call when the stop lands before it starts) and a late-finishing
-- answer is discarded rather than shown. The status walk is now
-- generating → ready | error | cancelled.
--
-- The original CHECK (see 20260617120000_ask_jobs.sql) only allowed
-- generating/ready/error, so an unconditional 'cancelled' write would violate
-- it. Drop and re-add the constraint with the new value. `if exists` keeps this
-- idempotent; the constraint name is Postgres' default (`<table>_<col>_check`).

alter table ask_jobs drop constraint if exists ask_jobs_status_check;

alter table ask_jobs
    add constraint ask_jobs_status_check
    check (status in ('generating', 'ready', 'error', 'cancelled'));
