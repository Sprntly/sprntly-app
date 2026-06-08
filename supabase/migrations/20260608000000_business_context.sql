-- Business Context — the company's structured, provenance-tracked "lens"
-- (skill: business-context). One versioned JSONB document per company; the
-- doc embeds its own `version` + per-leaf `meta` provenance, so a single
-- column is the source of truth (mirrors the kpi_tree column shape).
--
-- The KG projection (segments → entities, constraints/good_outcome → signals,
-- alternatives → competitor entities) is derived FROM this column and is not
-- itself the source of truth.

alter table companies
    add column if not exists business_context jsonb not null default '{}'::jsonb;
