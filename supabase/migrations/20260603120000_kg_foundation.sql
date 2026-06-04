-- Knowledge Graph foundation (Phase 0)
--
-- Adds the universal meta-model tables (entity / signal / relationship / source) +
-- pgvector extension + agent_decision_log, all tenant-scoped to companies(id).
-- "enterprise_id" in the design doc / shared-contracts doc is companies.id here.
--
-- Refs:
--   ~/sprntly-shared-contracts.md S3 (types) + S2 (decision log) + S5 (config)
--   ~/sprntly-agent-design.md §2 (KG model) + §4d (Agent Decision Log)
--   #1 contract staleness windows per source_type live in app code (graph/types.py).

create extension if not exists vector;

-- ---------- Source ----------
-- A connected source (connector instance or agent) registered for an enterprise.
-- The source_type taxonomy is intentionally broad: it covers both *connector* tags
-- (clickup/hubspot/...) and the *signal* source_type vocabulary the spec uses
-- (analytics/communication/customer_voice/...). The signal table re-validates
-- using the narrower signal-side vocabulary (#1 staleness map).
create table if not exists kg_source (
    id            uuid primary key default gen_random_uuid(),
    enterprise_id uuid not null references companies (id) on delete cascade,
    source_type   text not null,
    label         text,
    config        jsonb not null default '{}'::jsonb,
    status        text not null default 'active'
                    check (status in ('active', 'disabled', 'error')),
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);
create index if not exists kg_source_enterprise_idx on kg_source (enterprise_id);

-- ---------- Entity ----------
-- The universal node. type is an emergent label ('theme' | 'account' |
-- 'product_area' | 'goal' | 'kpi' | 'competitor' | 'system' | 'meeting' | ...)
-- plus the reserved ledger types ('hypothesis' | 'decision' | 'outcome' |
-- 'artifact'). The reserved types carry extra required props in `properties`
-- (validated at the application layer).
create table if not exists kg_entity (
    id              uuid primary key default gen_random_uuid(),
    enterprise_id   uuid not null references companies (id) on delete cascade,
    type            text not null,
    canonical_label text not null,
    aliases         text[] not null default array[]::text[],
    properties      jsonb not null default '{}'::jsonb,
    embedding       vector(1536),
    valid_at        timestamptz not null default now(),
    transaction_at  timestamptz not null default now(),
    provenance      jsonb not null default '{}'::jsonb,
    confidence      real not null default 1.0,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);
create index if not exists kg_entity_enterprise_type_idx
    on kg_entity (enterprise_id, type);
-- ivfflat keeps writes cheap; lists=100 fits the small per-enterprise volumes
-- the spec assumes (load_session_context ≤500ms for ≤100 Signals, §20 NFR).
create index if not exists kg_entity_embed_ivfflat
    on kg_entity using ivfflat (embedding vector_cosine_ops) with (lists = 100);

-- ---------- Signal ----------
-- Atomic evidence. source_type drives the per-source-type staleness window
-- computed by the app (graph/types.py SOURCE_STALE_WINDOW_DAYS).
-- outcome_measured signals have stale_after = NULL (never expire).
create table if not exists kg_signal (
    id              uuid primary key default gen_random_uuid(),
    enterprise_id   uuid not null references companies (id) on delete cascade,
    source_id       uuid references kg_source (id) on delete set null,
    source_type     text not null check (source_type in (
        'analytics', 'project_mgmt', 'communication', 'customer_voice', 'revenue',
        'verbal_claim', 'pm_manual', 'agent_inferred', 'outcome_measured'
    )),
    kind            text not null,
    content         text not null,
    properties      jsonb not null default '{}'::jsonb,
    embedding       vector(1536),
    valid_at        timestamptz not null default now(),
    transaction_at  timestamptz not null default now(),
    stale_after     timestamptz,
    confidence      real not null default 1.0,
    weight          real not null default 1.0,
    provenance      jsonb not null default '{}'::jsonb,
    created_at      timestamptz not null default now()
);
create index if not exists kg_signal_enterprise_idx on kg_signal (enterprise_id);
create index if not exists kg_signal_source_type_idx
    on kg_signal (enterprise_id, source_type);
create index if not exists kg_signal_stale_idx
    on kg_signal (enterprise_id, stale_after);
create index if not exists kg_signal_embed_ivfflat
    on kg_signal using ivfflat (embedding vector_cosine_ops) with (lists = 100);

-- ---------- Relationship ----------
-- Typed edges; the type list is the CLOSED vocabulary from S3. A novel
-- relationship the extractor wants must map into RELATES_TO (and be flagged
-- for human review).
create table if not exists kg_relationship (
    id             bigserial primary key,
    enterprise_id  uuid not null references companies (id) on delete cascade,
    type           text not null check (type in (
        'SUPPORTS', 'CONTRADICTS', 'ADDRESSES', 'BLOCKED_BY', 'AFFECTS',
        'REQUESTS', 'PRESSURES', 'SERVES', 'IMPACTS', 'ON', 'PART_OF',
        'PROMOTED_TO', 'EXPRESSED_AS', 'VISUALIZES', 'RESULTED_IN',
        'VALIDATES', 'UPDATES_WEIGHT', 'IMPLEMENTS', 'REALIZES',
        'SCOPED_TO', 'INFORMS', 'RELATES_TO'
    )),
    source_kind    text not null check (source_kind in ('entity', 'signal')),
    source_id      uuid not null,
    target_kind    text not null check (target_kind in ('entity', 'signal')),
    target_id      uuid not null,
    properties     jsonb not null default '{}'::jsonb,
    confidence     real not null default 1.0,
    valid_at       timestamptz not null default now(),
    transaction_at timestamptz not null default now(),
    provenance     jsonb not null default '{}'::jsonb,
    created_at     timestamptz not null default now()
);
create index if not exists kg_rel_enterprise_idx on kg_relationship (enterprise_id);
create index if not exists kg_rel_from_idx
    on kg_relationship (enterprise_id, source_id, type);
create index if not exists kg_rel_to_idx
    on kg_relationship (enterprise_id, target_id, type);

-- ---------- Agent decision log ----------
-- Append-only, tenant-scoped. Captures factors + reasoning + output + model
-- + prompt_version + confidence + KG refs for every agent / LLM decision.
-- Triple-serves: explainability (visible to PM), audit, learning trace
-- (Tier-2 fine-tuning dataset). §4d.
create table if not exists agent_decision_log (
    id             bigserial primary key,
    enterprise_id  uuid not null references companies (id) on delete cascade,
    agent          text not null,
    decision_type  text not null,
    factors        jsonb not null default '{}'::jsonb,
    reasoning      text,
    output         jsonb not null default '{}'::jsonb,
    model          text,
    prompt_version text,
    confidence     real,
    kg_refs        jsonb not null default '[]'::jsonb,
    timestamp      timestamptz not null default now()
);
create index if not exists agent_dec_log_enterprise_idx
    on agent_decision_log (enterprise_id, timestamp desc);
create index if not exists agent_dec_log_agent_idx
    on agent_decision_log (enterprise_id, agent, timestamp desc);

-- ---------- pgvector kNN function ----------
-- Postgres function for entity find-or-create resolution (#2): returns the
-- top-k existing entities of the requested type by cosine similarity, scoped
-- to the enterprise. Application code applies τ_high / τ_low / gray-zone
-- adjudication policy on top of these candidates (S3 / §2).
create or replace function kg_find_candidates (
    p_enterprise_id uuid,
    p_type          text,
    p_embedding     vector(1536),
    p_k             int default 10
) returns table (
    id              uuid,
    canonical_label text,
    type            text,
    score           real
) language sql stable as $$
    select e.id,
           e.canonical_label,
           e.type,
           (1 - (e.embedding <=> p_embedding))::real as score
      from kg_entity e
     where e.enterprise_id = p_enterprise_id
       and e.type = p_type
       and e.embedding is not null
     order by e.embedding <=> p_embedding
     limit p_k;
$$;
