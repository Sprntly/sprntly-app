-- Brief finding-state — the per-theme memory that powers "don't resurface a
-- finding in the weekly brief unless the underlying issue actually changed".
--
-- When a theme is surfaced as one of the brief's top-N findings, we record a
-- FINGERPRINT of its convergence state at that moment (signal count, effective
-- weight, revenue at stake, breadth, newest signal timestamp). On the next
-- synthesis run we compare each previously-surfaced theme's current convergence
-- against this fingerprint: if nothing materially changed (no new evidence, no
-- ≥20% metric move, no breadth change), the theme is suppressed from the brief
-- so the same finding doesn't reappear week after week. A changed issue
-- (worsened / fresh evidence) becomes eligible to resurface.
--
-- One row per (enterprise_id, theme_id); a re-surface upserts the fingerprint in
-- place. "enterprise_id" == companies.id (same convention as backlog_items and
-- the kg_* tables).

create table if not exists brief_finding_state (
    id                  uuid primary key default gen_random_uuid(),
    enterprise_id       uuid not null references companies (id) on delete cascade,
    theme_id            text not null,
    -- The brief that last surfaced this theme (nullable; brief rows can be
    -- pruned independently, so don't hard-FK to keep the memory durable).
    last_brief_id       bigint,
    last_surfaced_at    timestamptz not null default now(),
    -- Convergence fingerprint captured at last surface (see synthesis/dedup.py).
    fp_signal_count     int not null default 0,
    fp_effective_weight double precision not null default 0,
    fp_revenue_at_stake double precision not null default 0,
    fp_breadth          int not null default 0,
    fp_latest_signal_at timestamptz,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    unique (enterprise_id, theme_id)
);

create index if not exists brief_finding_state_enterprise_idx
    on brief_finding_state (enterprise_id);
