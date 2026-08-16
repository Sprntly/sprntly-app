-- Ask jobs — DB-enforced idempotency for the group-chat retry claim.
--
-- `ask_jobs` is main chat's LIVE primitive and staging + prod share ONE
-- Supabase project (see `backend/app/db/asks.py`), so this index lands on
-- the same table backing main chat's ask/answer polling loop right now.
-- It adds NOTHING behavioral to existing rows: a PARTIAL unique index over
-- `source_turn_id` restricted to rows that are BOTH `status = 'generating'`
-- AND carry a `source_turn_id` — i.e. at most one LIVE attempt may exist per
-- triggering group turn at a time. A concurrent second retry claim for the
-- same turn violates this index and is refused, so retry atomicity is
-- DB-enforced (see `db/asks.py::claim_retry_attempt`) rather than racing in
-- application code.
--
-- Safety (identical posture to 20260816120000_ask_jobs_execution_identity):
--   * `if not exists` — idempotent re-run, matches the repo's ask_jobs
--     ALTER/index pattern (20260718120000, 20260802120000, 20260816120000).
--   * PARTIAL — the predicate excludes every legacy / main / private row
--     (all `source_turn_id IS NULL`) and every terminal group row, so those
--     rows are untouched and never collide; the index covers only the small
--     set of currently-generating group runs.
--   * NO table rewrite, NO status/CHECK change, NO new column. Status
--     vocabulary stays `generating/ready/error/cancelled`.
--   * A plain CREATE UNIQUE INDEX takes a brief ACCESS EXCLUSIVE lock;
--     `ask_jobs` is a small per-request status table so the build is
--     expected sub-second (the builder pastes the live row count + build
--     wall-clock as the prod-shared lock proof). If the measured build were
--     multi-second, the CONCURRENTLY variant (separate non-transactional
--     migration) would be used instead — not needed at the observed size.
--
-- Reversible: additive index, no data written — `drop index if exists
-- ask_jobs_active_attempt_uidx;`.

create unique index if not exists ask_jobs_active_attempt_uidx
    on public.ask_jobs (source_turn_id)
    where status = 'generating' and source_turn_id is not null;
