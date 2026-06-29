-- On-demand Implementation Spec (machine-readable Part B) caching.
--
-- Part B is no longer generated alongside the human PRD. It is now produced
-- lazily, the first time a user clicks "Send to Claude Code", and cached in
-- prds.llm_part for reuse on re-sends. `llm_part_source_hash` stores the hash
-- of the human PRD (payload_md) the cached spec was derived from, so the cache
-- auto-invalidates the moment the human PRD changes (edit / restore): on the
-- next send the hashes differ and the spec regenerates.
--
-- Idempotent: safe to run repeatedly (add column if not exists).
alter table if exists public.prds
    add column if not exists llm_part_source_hash text;
