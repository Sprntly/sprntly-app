"""Shared pytest fixtures.

After the Supabase cutover, the backend no longer touches SQLite at
all. Tests substitute a `FakeSupabaseClient` (in-memory SQLite under
the hood — see tests/_fake_supabase.py) for `supabase_client()` so
helpers run fast + isolated without a real network round-trip.

Each test gets:
- A fresh DATA_DIR under tmp_path (still used by corpus.py for files).
- A fresh in-memory fake Supabase with schema seeded from
  the live supabase/migrations/*.sql, translated to SQLite-compatible
  DDL for the fake's underlying store.
- A patched app.llm.call_json that returns deterministic payloads
  instead of hitting Anthropic.
- An authenticated FastAPI TestClient with a real session cookie minted
  via the login route.

Mark tests `integration` to opt out of LLM mocking.
"""
from __future__ import annotations

import importlib
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from tests._fake_supabase import FakeSupabaseClient, reset_fake_db


# ── P5-06: default a same-origin `Origin` header onto every test HTTP client ──
# The P5-06 CSRF backstop (`require_same_origin`) rejects authed mutating Design Agent
# requests whose `Origin` is missing or not in `settings.origins_list`. Real browsers
# always send `Origin`; the test clients do not by default, so without this every
# pre-existing authed-route test would 403. We wrap BOTH client classes the suite uses —
# starlette's sync `TestClient` AND `httpx.AsyncClient` (the e2e/smoke files drive the app
# over `httpx.AsyncClient` + ASGITransport, a different class a function-scoped autouse
# fixture would miss) — to default `Origin` to the app's own allow-list entry. The default
# is `setdefault`, so the csrf negative tests that pass an explicit (foreign/empty/absent)
# Origin still exercise the 403 path. The Origin is pulled from `settings.origins_list`
# (derived from ALLOWED_ORIGINS — the SAME allow-list CORS uses; no second list).
def _wrap_client_origin(cls) -> None:
    _orig = cls.__init__
    if getattr(_orig, "_origin_wrapped", False):
        return

    def __init__(self, *a, **kw):
        from app.config import settings  # read lazily so per-test config reloads apply

        headers = dict(kw.pop("headers", None) or {})
        headers.setdefault("origin", settings.origins_list[0])
        kw["headers"] = headers
        _orig(self, *a, **kw)

    __init__._origin_wrapped = True  # type: ignore[attr-defined]
    cls.__init__ = __init__


def pytest_configure(config):  # noqa: ARG001 — pytest hook signature
    import starlette.testclient as _tc

    _wrap_client_origin(_tc.TestClient)
    import httpx

    _wrap_client_origin(httpx.AsyncClient)


# Modules that import `settings` at top level and therefore need to be
# reloaded after env vars change. Order matters: config first, then its
# consumers, then anything that imports the consumers.
_RELOAD_ORDER = [
    "app.config",
    "app.db.client",
    "app.db.schema",
    "app.db.briefs",
    "app.db.prds",
    "app.db.prd_input_questions",
    "app.db.evidences",
    "app.db.asks",
    "app.db.datasets",
    "app.db.connections",
    "app.db.github",
    "app.db",
    "app.corpus",
    "app.auth",
    "app.entitlements",
    "app.llm",
    "app.ingest",
    "app.datasets",
    "app.prompts",
    "app.ask_runner",
    "app.ask_job_runner",
    "app.evidence_runner",
    "app.prd_runner",
    "app.prd_questions",
    "app.brief_runner",
    "app.routes.health",
    "app.routes.datasets",
    "app.routes.brief",
    "app.routes.ask",
    "app.routes.evidence",
    "app.routes.prd",
    "app.connectors.tokens",
    "app.connectors.google_oauth",
    "app.connectors.figma_oauth",
    "app.connectors.github_app",
    "app.routes.connectors",
    "app.routes.internal",
    "app.db.mcp_tokens",
    "app.routes.mcp_tokens",
    "app.routes.internal_mcp",
    "app.main",
]


def _reload_app_modules() -> None:
    for name in _RELOAD_ORDER:
        mod = sys.modules.get(name)
        if mod is None:
            try:
                importlib.import_module(name)
            except Exception:
                continue
        else:
            try:
                importlib.reload(mod)
            except Exception:
                raise


# Schema for the fake Supabase. SQLite-compatible DDL that mirrors the
# Postgres tables we actually use. Booleans + jsonb are translated by
# the fake's encode/decode layer.
_FAKE_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE briefs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset      TEXT NOT NULL,
    generated_at TEXT NOT NULL DEFAULT (datetime('now')),
    week_label   TEXT,
    payload      TEXT NOT NULL,
    is_current   INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX briefs_dataset_current_idx ON briefs (dataset, is_current);

CREATE TABLE prds (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id         INTEGER NOT NULL,
    insight_index    INTEGER NOT NULL,
    generated_at     TEXT NOT NULL DEFAULT (datetime('now')),
    title            TEXT NOT NULL,
    payload_md       TEXT NOT NULL DEFAULT '',
    llm_part         TEXT,
    -- Hash of the human PRD (payload_md) the cached llm_part was derived from
    -- (mirrors 20260629120000_prd_llm_part_source_hash.sql). Keys the on-demand
    -- Implementation Spec cache so it invalidates when the human PRD changes.
    llm_part_source_hash TEXT,
    status           TEXT NOT NULL DEFAULT 'ready',
    error            TEXT,
    template_version INTEGER,
    variant          TEXT NOT NULL DEFAULT 'v1',
    run_id           TEXT,
    -- Ideation-sourced PRDs (mirrors 20260702000000_prds_backlog_source.sql,
    -- values renamed by 20260715000000): source='ideation' + theme_id set for a
    -- PRD generated from an ideation item; source='brief' + theme_id NULL for a
    -- brief-insight PRD.
    source           TEXT NOT NULL DEFAULT 'brief',
    theme_id         TEXT,
    -- 20260731090000: originating-chat-question linkage (mirrors reports'
    -- question/ask_id) — NULL on every path except the chat-task command.
    question         TEXT,
    ask_id           INTEGER,
    -- Mirrors 20260802120000_prds_public_id.sql. Real Postgres backfills +
    -- defaults this via gen_random_uuid(), which sqlite has no equivalent
    -- for — nullable here; tests that exercise resolve_prd_id_by_public_id
    -- stamp a real uuid4 explicitly via an UPDATE after seeding.
    public_id        TEXT,
    -- Which uploaded FORMAT produced this PRD (mirrors
    -- 20260806160000_prds_artifact_template.sql). NULL = Sprntly's built-in
    -- format, which is every pre-existing row and every PRD from a company that
    -- never uploads one. Deliberately NOT a foreign key in either engine: a
    -- format is deletable, and an FK would either erase this PRD's provenance
    -- when the library is tidied or make the format undeletable.
    artifact_template_id TEXT
);

CREATE TABLE evidences (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id         INTEGER NOT NULL,
    insight_index    INTEGER NOT NULL,
    generated_at     TEXT NOT NULL DEFAULT (datetime('now')),
    title            TEXT NOT NULL,
    payload_md       TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'generating',
    error            TEXT,
    template_version INTEGER,
    variant          TEXT NOT NULL DEFAULT 'v1',
    -- 20260719120000: chat-task evidence keys by (brief_id, theme_id)
    -- ('chat:<hash>'); brief-insight docs keep NULL.
    theme_id         TEXT,
    -- 20260731090000: originating-chat-question linkage (mirrors prds above).
    question         TEXT,
    ask_id           INTEGER
);

-- Test-harness only (NOT a migration): the real prd_patches migration ships
-- from P3-09. Seeded in the base schema so get_prd_rendered (P3-17) can resolve
-- list_applied_patches to [] under the base harness — keeps existing PRD route
-- tests green when GET /v1/prd/{id} now folds applied patches on read. Mirrors
-- test_design_agent_prd_patches._PRD_PATCHES_DDL exactly.
CREATE TABLE prd_patches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id        INTEGER NOT NULL,
    prototype_id  INTEGER NOT NULL,
    workspace_id  TEXT NOT NULL,
    rationale     TEXT NOT NULL,
    patch_md      TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'applied', 'rejected')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at   TEXT
);

-- PRD version snapshots (mirrors 20260607100000_prd_versions.sql). Seeded so the
-- save-a-version-before-overwrite path (PUT /{id} + the input-answer edit) works
-- under the harness instead of silently no-opping.
CREATE TABLE prd_versions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id         INTEGER NOT NULL,
    version_number INTEGER NOT NULL DEFAULT 1,
    title          TEXT NOT NULL DEFAULT '',
    payload_md     TEXT NOT NULL DEFAULT '',
    saved_by       TEXT NOT NULL DEFAULT 'user',
    saved_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Structured "User input needed" questions (mirrors
-- 20260708000000_prd_input_questions.sql). Seeded in the base schema so the PRD
-- input-question routes + extraction resolve under the base harness.
CREATE TABLE prd_input_questions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id       INTEGER NOT NULL,
    ordinal      INTEGER NOT NULL DEFAULT 0,
    tag          TEXT NOT NULL DEFAULT 'need'
                 CHECK (tag IN ('escalate', 'need')),
    prompt       TEXT NOT NULL,
    owner        TEXT,
    options      TEXT NOT NULL DEFAULT '[]',
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'answered', 'dismissed')),
    answer       TEXT,
    answered_by  TEXT,
    answered_at  TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE ask_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    asked_at    TEXT NOT NULL DEFAULT (datetime('now')),
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL,
    citations   TEXT NOT NULL
);

CREATE TABLE cached_asks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset       TEXT NOT NULL,
    question      TEXT NOT NULL,
    response      TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'generating',
    error         TEXT,
    cache_version INTEGER,
    generated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Team documents of any kind — the "Others" library (mirrors
-- 20260813120000_custom_artifacts.sql). Present in the BASE schema, not only in
-- the suites that exercise it, because the startup lifespan sweeps this table
-- for orphaned generations — so every test that boots the app touches it.
CREATE TABLE custom_artifacts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      TEXT NOT NULL,
    workspace_id    TEXT,
    conversation_id INTEGER,
    kind            TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL DEFAULT '',
    body_html       TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'ready',
    error           TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    created_by      TEXT,
    updated_by      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Fire-and-forget Ask job rows (mirrors 20260617120000_ask_jobs.sql). Status
-- walks generating → ready (or error); `response` holds the citation-stripped
-- answer JSON. Per-request + per-tenant — distinct from cached_asks/ask_log.
CREATE TABLE ask_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      TEXT NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    dataset         TEXT NOT NULL,
    question        TEXT NOT NULL,
    conversation_id INTEGER,
    pinned_skill    TEXT,
    -- PRD-tab grounding (mirrors 20260718120000_ask_jobs_prd_id.sql).
    prd_id          INTEGER,
    -- The skill the router picked, written the moment it resolves rather than
    -- at completion, so the waiting surface can name the running skill (mirrors
    -- 20260802120000_ask_jobs_routed_skill.sql). NULL = no skill was routed.
    routed_skill        TEXT,
    routed_skill_action TEXT,
    status          TEXT NOT NULL DEFAULT 'generating',
    response        TEXT NOT NULL DEFAULT '{}',
    error           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX ask_jobs_company_idx ON ask_jobs (company_id, id DESC);

-- Fire-and-forget onboarding website-analysis job rows (mirrors
-- 20260618120000_website_analysis_jobs.sql). Status walks generating → ready
-- (or error); `result` holds the full analyze_website() dict. Per-request +
-- per-tenant — backs the blur/remount-safe onboarding interstitial.
CREATE TABLE website_analysis_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id  TEXT NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    url         TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'generating',
    result      TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX website_analysis_jobs_company_idx ON website_analysis_jobs (company_id, id DESC);

-- Deep company-research runs (mirrors
-- 20260730134500_company_research_runs.sql). One row per staged web-research
-- sweep over the company's OWN public footprint; status walks running →
-- completed / completed_partial (or failed). `records` holds the captured fact
-- records. No client polls this — the row IS the handle on an
-- abandonment-proof background run. The partial unique index is the ATOMIC
-- one-live-run-per-company guard (SQLite supports partial indexes, so the
-- insert-conflict path is exercised by the tests exactly as in Postgres).
CREATE TABLE company_research_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id   TEXT NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    url          TEXT,
    trigger      TEXT NOT NULL
                 CHECK (trigger IN ('onboarding', 'chat')),
    status       TEXT NOT NULL DEFAULT 'running'
                 CHECK (status IN ('running', 'completed',
                                   'completed_partial', 'failed')),
    stages       TEXT NOT NULL DEFAULT '{}',
    records      TEXT,
    summary      TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);
CREATE INDEX company_research_runs_company_idx
    ON company_research_runs (company_id, created_at DESC);
CREATE UNIQUE INDEX company_research_runs_one_live_idx
    ON company_research_runs (company_id) WHERE status = 'running';

-- Fire-and-forget LLM-context extraction jobs (mirrors
-- 20260723130000_llm_context_jobs.sql). The onboarding import step reads the
-- uploaded Markdown with an LLM pass here — the only reader since the v3
-- prompt — which handles context documents of any shape. Status walks
-- generating → ready (or error); `result` holds the same
-- {ok, fields, unmapped, format_version, note} dict the POST returns.
CREATE TABLE llm_context_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id  TEXT NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'generating',
    result      TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX llm_context_jobs_company_idx ON llm_context_jobs (company_id, id DESC);

-- Multi-agent generated docs (mirrors 20260613100000_multi_agent_docs.sql).
-- No company_id column: tenant ownership is bound via brief_id -> brief ->
-- dataset -> company (app.deps.ownership.require_owned_brief). Was previously
-- absent from the fake schema, which is why this table shipped untested.
CREATE TABLE multi_agent_docs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id      INTEGER NOT NULL,
    insight_index INTEGER NOT NULL,
    prd_id        INTEGER,
    doc_type      TEXT NOT NULL CHECK (doc_type IN (
        'qa_test_cases', 'technical_design', 'risk_analysis', 'traceability_matrix'
    )),
    title         TEXT NOT NULL DEFAULT '',
    payload_md    TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'generating' CHECK (status IN (
        'generating', 'ready', 'failed', 'invalidated'
    )),
    error         TEXT,
    run_id        TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_multi_agent_docs_run_id ON multi_agent_docs (run_id);

-- slug PRIMARY KEY mirrors the prod UNIQUE on datasets.slug
-- (20260608160000_datasets_slug_unique.sql); a duplicate INSERT raises
-- IntegrityError here, which insert_dataset treats as "already exists".
CREATE TABLE datasets (
    slug         TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    -- Workspace binding (mirrors 20260716123000_datasets_workspace_id.sql):
    -- the dataset is the workspace's corpus key. NULL = legacy demo dataset.
    workspace_id TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Companies / company_members mirror the Supabase migrations.
-- connections.company_id FKs into companies(id); require_company
-- (auth.py) reads company_members to resolve the active tenant from
-- the Supabase JWT.
CREATE TABLE companies (
    id                  TEXT PRIMARY KEY,
    slug                TEXT NOT NULL UNIQUE,
    display_name        TEXT NOT NULL,
    coworker_names      TEXT NOT NULL DEFAULT '{}',
    kpi_tree            TEXT NOT NULL DEFAULT '{}',
    business_context    TEXT NOT NULL DEFAULT '{}',
    -- Onboarding profile columns the research agents read/write (mirrors
    -- 20260525150000_onboarding_workspace.sql). competitors[] is the fixed
    -- competitor roster; the Competitor agent auto-discovers + writes it when empty.
    competitors         TEXT NOT NULL DEFAULT '[]',
    product_description TEXT,
    industry            TEXT,
    business_type       TEXT,
    -- Per-company config incl. drip-email cadence/opt-out (mirrors
    -- 20260525150000_onboarding_workspace.sql). Read by app.db.drip.
    notification_settings TEXT NOT NULL DEFAULT '{}',
    -- Fernet-encrypted per-company Claude key (mirrors
    -- 20260711120000_company_llm_api_key.sql). Read by app.llm_keys.
    llm_api_key_encrypted TEXT,
    -- The OpenAI counterpart, plus which of the two the company actually runs
    -- on (mirrors 20260807120000_company_openai_key_and_provider.sql). Both
    -- keys may be set at once; llm_provider decides which is live. Defaults to
    -- 'anthropic' so an untouched row behaves exactly as it did before OpenAI
    -- was an option.
    openai_api_key_encrypted TEXT,
    llm_provider        TEXT NOT NULL DEFAULT 'anthropic',
    -- Platform-key fallback flag + onboarding-completion marker. Read by
    -- app.llm_keys to decide whether a keyless company may use the platform key
    -- (mirrors 20260712120000_company_use_platform_key.sql +
    -- 20260525150000_onboarding_workspace.sql).
    use_platform_key    INTEGER NOT NULL DEFAULT 0,
    onboarding_completed_at TEXT,
    -- Staff-panel entitlements (mirrors
    -- 20260712150000_org_invites_admin_entitlements.sql). seat_limit NULL =
    -- unlimited. prototype_enabled defaults 1, matching the real column since
    -- 20260721130000_prototype_enabled_default_true.sql (prototype is a
    -- default-ON module; the staff toggle is an opt-out).
    feature_flags       TEXT NOT NULL DEFAULT '{}',
    seat_limit          INTEGER,
    prototype_enabled   INTEGER NOT NULL DEFAULT 1,
    -- Registration-spec v5 columns (mirrors
    -- 20260716120000_account_type_onboarding_v5.sql).
    account_type        TEXT,
    mission             TEXT,
    strategy            TEXT,
    portfolio           TEXT,
    icp                 TEXT NOT NULL DEFAULT '{}',
    tone_voice          TEXT NOT NULL DEFAULT '{}',
    planning_cycle      TEXT,
    team_scope          TEXT,
    prioritization_framework TEXT,
    sizing_methodology  TEXT,
    -- Onboarding v6 columns (mirrors 20260717120000_onboarding_v6.sql):
    -- team name + the steps-6/7 typed blocks + the accepted business-context
    -- prose + the define-metrics sub-flow definitions.
    team_name           TEXT,
    team_strategy       TEXT,
    team_roadmap        TEXT,
    decision_process    TEXT,
    additional_context  TEXT,
    business_context_summary TEXT,
    business_context_accepted_at TEXT,
    metric_definitions  TEXT NOT NULL DEFAULT '[]',
    -- Async business-context refresh state, singleton per tenant (mirrors
    -- 20260802140000_business_context_refresh_status.sql). status defaults
    -- 'idle' (never NULL) — see that migration for why.
    business_context_refresh_status TEXT NOT NULL DEFAULT 'idle',
    business_context_refresh_error TEXT,
    business_context_refresh_started_at TEXT,
    business_context_refresh_heartbeat_at TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE company_members (
    id         TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    user_id    TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'member'
                CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, user_id)
);
CREATE INDEX company_members_user_id_idx    ON company_members (user_id);
CREATE INDEX company_members_company_id_idx ON company_members (company_id);

-- In-app feedback / feature-request submissions (mirrors
-- 20260622130000_feedback.sql). Read/written by app.db.feedback via the route.
CREATE TABLE feedback (
    id          TEXT PRIMARY KEY,
    company_id  TEXT REFERENCES companies (id) ON DELETE SET NULL,
    user_id     TEXT,
    user_email  TEXT,
    type        TEXT NOT NULL DEFAULT 'other'
                  CHECK (type IN ('bug', 'feature_request', 'connector_request', 'other')),
    message     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX feedback_company_idx ON feedback (company_id, created_at DESC);

CREATE TABLE connections (
    id                   TEXT PRIMARY KEY,
    company_id           TEXT NOT NULL
                          REFERENCES companies (id) ON DELETE CASCADE,
    -- Workspace/product scoping (added 2026-06-06, see migration
    -- 20260606120000_workspaces_and_connection_scope.sql). Nullable
    -- today because the application route layer hasn't moved off
    -- company_id yet — both columns coexist until the migration to
    -- workspace-scoped routes lands.
    workspace_id         TEXT,
    product_id           TEXT,
    company_name         TEXT,
    product_name         TEXT,
    -- Slack is PER-USER (each user's own bot/channel); every other
    -- provider is company-scoped + member-shared. user_id is NULL for
    -- company-scoped rows and set for Slack rows (see migration
    -- 20260608000000_slack_per_user.sql). The two partial unique indexes
    -- below mirror that split.
    user_id              TEXT,
    provider             TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'active',
    google_email         TEXT,
    account_label        TEXT,
    scopes               TEXT NOT NULL DEFAULT '',
    token_json_encrypted TEXT NOT NULL,
    -- Service-account private key (Fernet-encrypted), separate from the OAuth
    -- user token above so both coexist on one connection. Mirrors migration
    -- 20260807130000_connections_sa_key.sql. Never in the client-facing
    -- serializer's allowlist.
    sa_key_encrypted     TEXT,
    config               TEXT NOT NULL DEFAULT '{}',
    last_sync_at         TEXT,
    last_sync_error      TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX connections_company_provider_non_slack_key
    ON connections (company_id, provider) WHERE provider <> 'slack';
CREATE UNIQUE INDEX connections_company_user_slack_key
    ON connections (company_id, user_id, provider) WHERE provider = 'slack';
CREATE INDEX connections_company_id_idx ON connections (company_id);
CREATE INDEX connections_user_id_idx ON connections (user_id);
CREATE INDEX connections_workspace_id_idx ON connections (workspace_id);
CREATE INDEX connections_product_id_idx   ON connections (product_id);

-- Onboarding's per-company product rows (mirrors
-- supabase/migrations/20260525150300_products.sql, SQLite-ized). The Design
-- Agent reads it via app.db.products.get_company_website (called from
-- app.routes.design_agent) to fall back to the company's primary-product
-- website when no Figma source is connected. Seeded here so every Design Agent
-- route/db test finds the table regardless of run order — previously only the
-- ad-hoc fake in test_market_research_agent.py knew about it, so the shared
-- fake raised `no such table: products`. Read-only in tests; FK target
-- companies(id) is defined above. uuid PK / timestamptz are TEXT under SQLite,
-- matching the other seeded tables.
CREATE TABLE products (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    website     TEXT,
    description TEXT,
    is_primary  INTEGER NOT NULL DEFAULT 0,
    -- Registration-spec v5 product fields (mirrors
    -- 20260716120000_account_type_onboarding_v5.sql; text[] → JSON TEXT).
    surfaces     TEXT NOT NULL DEFAULT '[]',
    personas     TEXT NOT NULL DEFAULT '[]',
    positioning  TEXT,
    monetization TEXT NOT NULL DEFAULT '[]',
    -- v6 "tell us about your users" prose (mirrors 20260717120000_onboarding_v6.sql).
    users_description TEXT,
    maturity     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX products_company_id_idx ON products (company_id);

-- Workspaces (1 company → N products → N workspaces; 1 product → N workspaces).
-- Mirrors supabase/migrations/20260606120000_workspaces_and_connection_scope.sql.
CREATE TABLE workspaces (
    id          TEXT PRIMARY KEY,
    -- No REFERENCES companies(id) here, unlike prod: route tests override
    -- require_company with fabricated company ids (co-X, acme, …) that have no
    -- companies row, and require_workspace's ensure_default_workspace self-heal
    -- must be able to insert for them. In prod require_company resolves the
    -- company FROM the DB, so the row always exists and the FK never bites.
    company_id  TEXT NOT NULL,
    product_id  TEXT REFERENCES products (id) ON DELETE SET NULL,
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL,
    is_default  INTEGER NOT NULL DEFAULT 0,
    -- Workspace-owned "Your workspace" fields (mirrors
    -- 20260722120000_workspace_owned_fields.sql — moved off companies).
    team_scope          TEXT,
    team_strategy       TEXT,
    team_roadmap        TEXT,
    sizing_methodology  TEXT,
    additional_context  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, slug)
);
CREATE INDEX workspaces_company_id_idx ON workspaces (company_id);
CREATE INDEX workspaces_product_id_idx ON workspaces (product_id);

-- Workspace membership (mirrors 20260716121000_workspace_members.sql).
-- Two-level roles: org owner/admin implicitly access all workspaces;
-- plain members need a row here per workspace.
CREATE TABLE workspace_members (
    id           TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    user_id      TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'member'
                  CHECK (role IN ('admin', 'member', 'viewer')),
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (workspace_id, user_id)
);
CREATE INDEX workspace_members_user_id_idx      ON workspace_members (user_id);
CREATE INDEX workspace_members_workspace_id_idx ON workspace_members (workspace_id);

-- Mirrors supabase/migrations/20260525150000_onboarding_workspace.sql
-- (+ 20260716122000_invites_multi_workspace.sql: viewer role + the
-- workspace_ids uuid[] → JSON-encoded TEXT here).
-- Used by the Settings → Team route suite (test_team_*.py).
CREATE TABLE workspace_invites (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    email         TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'member',
    -- v6 invite step's JOB role (mirrors 20260717120000_onboarding_v6.sql).
    job_role      TEXT,
    invited_by    TEXT,
    workspace_ids TEXT NOT NULL DEFAULT '[]',
    -- Project association (mirrors 20260813140200_workspace_invites_project.sql):
    -- when set, accept auto-adds the accepter to project_members (Extension B).
    -- No FK here: workspace_invites is created before the projects table below,
    -- and the fake schema mirrors columns, not constraint ordering.
    project_id    INTEGER,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, email)
);
CREATE INDEX workspace_invites_company_id_idx ON workspace_invites (company_id);

-- Staff-panel organization invites (mirrors
-- 20260712150000_org_invites_admin_entitlements.sql). Read/written by
-- app.db.org_invites via /v1/staff + the claim route.
CREATE TABLE org_invites (
    id                TEXT PRIMARY KEY,
    email             TEXT NOT NULL,
    company_name      TEXT NOT NULL,
    invited_by        TEXT,
    seat_limit        INTEGER,
    -- Default ON since 20260721130000_prototype_enabled_default_true.sql.
    prototype_enabled INTEGER NOT NULL DEFAULT 1,
    use_platform_key  INTEGER NOT NULL DEFAULT 0,
    feature_flags     TEXT NOT NULL DEFAULT '{}',
    status            TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'accepted', 'revoked')),
    company_id        TEXT REFERENCES companies (id) ON DELETE SET NULL,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    accepted_at       TEXT
);
CREATE UNIQUE INDEX org_invites_pending_email_uq
    ON org_invites (lower(email)) WHERE status = 'pending';

CREATE TABLE github_installations (
    installation_id      INTEGER PRIMARY KEY,
    company_id           TEXT,
    account_id           INTEGER NOT NULL,
    account_login        TEXT NOT NULL,
    account_type         TEXT NOT NULL,
    repository_selection TEXT NOT NULL DEFAULT 'selected',
    suspended            INTEGER NOT NULL DEFAULT 0,
    permissions          TEXT NOT NULL DEFAULT '{}',
    events               TEXT NOT NULL DEFAULT '[]',
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE github_pull_requests (
    installation_id INTEGER NOT NULL,
    company_id      TEXT,
    repo_full_name  TEXT NOT NULL,
    pr_number       INTEGER NOT NULL,
    title           TEXT NOT NULL,
    state           TEXT NOT NULL DEFAULT 'open',
    is_draft        INTEGER NOT NULL DEFAULT 0,
    author_login    TEXT,
    head_ref        TEXT,
    base_ref        TEXT,
    html_url        TEXT,
    body_excerpt    TEXT,
    pr_created_at   TEXT,
    pr_updated_at   TEXT,
    last_event_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (repo_full_name, pr_number)
);

-- Tenancy primitive (mirrors 20260525140000_companies_and_profiles.sql).
-- Used by require_company tests AND as the FK anchor for the kg_* tables.
CREATE TABLE IF NOT EXISTS companies (
    id           TEXT PRIMARY KEY,
    slug         TEXT,
    display_name TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS company_members (
    id         TEXT PRIMARY KEY,
    company_id TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'member'
);

-- User profiles (mirrors auth.users FK in prod; require_company reads this
-- to resolve user_name instead of stale JWT user_metadata).
CREATE TABLE IF NOT EXISTS profiles (
    id           TEXT PRIMARY KEY,
    email        TEXT,
    full_name    TEXT,
    first_name   TEXT,
    last_name    TEXT,
    avatar_url   TEXT,
    -- Registration-spec v5 (mirrors 20260716120000_account_type_onboarding_v5.sql).
    account_type TEXT,
    -- Free-text job designation captured at onboarding (mirrors
    -- 20260525150000_onboarding_workspace.sql's `profiles.role` column).
    role         TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---- KG foundation (Phase 0) ----
CREATE TABLE kg_source (
    id            TEXT PRIMARY KEY,
    enterprise_id TEXT NOT NULL,
    source_type   TEXT NOT NULL,
    label         TEXT,
    config        TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE kg_entity (
    id              TEXT PRIMARY KEY,
    enterprise_id   TEXT NOT NULL,
    type            TEXT NOT NULL,
    canonical_label TEXT NOT NULL,
    aliases         TEXT NOT NULL DEFAULT '[]',
    properties      TEXT NOT NULL DEFAULT '{}',
    embedding       TEXT,
    valid_at        TEXT NOT NULL,
    transaction_at  TEXT NOT NULL,
    provenance      TEXT NOT NULL DEFAULT '{}',
    confidence      REAL NOT NULL DEFAULT 1.0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE kg_signal (
    id             TEXT PRIMARY KEY,
    enterprise_id  TEXT NOT NULL,
    source_id      TEXT,
    source_type    TEXT NOT NULL,
    kind           TEXT NOT NULL,
    content        TEXT NOT NULL,
    properties     TEXT NOT NULL DEFAULT '{}',
    embedding      TEXT,
    valid_at       TEXT NOT NULL,
    transaction_at TEXT NOT NULL,
    stale_after    TEXT,
    confidence     REAL NOT NULL DEFAULT 1.0,
    weight         REAL NOT NULL DEFAULT 1.0,
    provenance     TEXT NOT NULL DEFAULT '{}',
    skill_id       TEXT,
    origin         TEXT,
    channel        TEXT,
    evidence_eligible INTEGER,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE kg_relationship (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    enterprise_id  TEXT NOT NULL,
    type           TEXT NOT NULL,
    source_kind    TEXT NOT NULL,
    source_id      TEXT NOT NULL,
    target_kind    TEXT NOT NULL,
    target_id      TEXT NOT NULL,
    properties     TEXT NOT NULL DEFAULT '{}',
    confidence     REAL NOT NULL DEFAULT 1.0,
    valid_at       TEXT NOT NULL,
    transaction_at TEXT NOT NULL,
    provenance     TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE enterprise_config (
    enterprise_id TEXT PRIMARY KEY,
    overrides     TEXT NOT NULL DEFAULT '{}',
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---- DS metrics (tiny rolling aggregates — mirrors
-- 20260607000000_metric_points.sql) ----
CREATE TABLE metric_points (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    enterprise_id TEXT NOT NULL,
    metric        TEXT NOT NULL,
    period_start  TEXT NOT NULL,
    value         REAL NOT NULL,
    source        TEXT NOT NULL,
    computed_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (enterprise_id, metric, period_start, source)
);
CREATE INDEX metric_points_series_idx
    ON metric_points (enterprise_id, metric, period_start DESC);

CREATE TABLE agent_decision_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    enterprise_id  TEXT NOT NULL,
    agent          TEXT NOT NULL,
    decision_type  TEXT NOT NULL,
    factors        TEXT NOT NULL DEFAULT '{}',
    reasoning      TEXT,
    output         TEXT NOT NULL DEFAULT '{}',
    model          TEXT,
    prompt_version TEXT,
    confidence     REAL,
    kg_refs        TEXT NOT NULL DEFAULT '[]',
    timestamp      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Prioritized ideation pool (mirrors 20260608120000_backlog_items.sql as
-- renamed + extended by 20260715000000_ideation_rename_shortlist.sql).
-- One row per non-brief theme, carrying its rank/score + rationale and the
-- weekly-prioritization `shortlisted` flag. 'backlog' stays an allowed legacy
-- status (pre-rename prod writes it through the compat view until cutover).
-- uuid PK / timestamptz are TEXT under SQLite, matching the other seeded tables.
CREATE TABLE ideation_items (
    id            TEXT PRIMARY KEY,
    enterprise_id TEXT NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    theme_id      TEXT NOT NULL,
    hypothesis_id TEXT,
    title         TEXT NOT NULL,
    tag           TEXT,
    rank          INTEGER NOT NULL,
    score         REAL NOT NULL,
    status        TEXT NOT NULL DEFAULT 'proposed'
                  CHECK (status IN ('proposed', 'backlog', 'in_progress', 'done', 'dismissed')),
    shortlisted   INTEGER NOT NULL DEFAULT 0,
    reasoning     TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (enterprise_id, theme_id)
);
CREATE INDEX ideation_items_rank_idx ON ideation_items (enterprise_id, rank);

-- Pipeline run audit rows (mirrors 20260605120000_pipeline_tables.sql).
-- Durable record of regenerate / scheduled pipeline runs; phase-2 fix uses it
-- to surface runs interrupted by a service restart.
CREATE TABLE pipeline_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset       TEXT NOT NULL,
    "trigger"     TEXT NOT NULL DEFAULT 'scheduled',
    status        TEXT NOT NULL DEFAULT 'running'
                  CHECK (status IN ('running', 'completed', 'failed')),
    stages        TEXT NOT NULL DEFAULT '{}',
    -- ISO-8601 with 'T' (not sqlite's space-separated datetime('now')) so
    -- lexical .lt() comparisons against isoformat() cutoffs behave like
    -- Postgres timestamptz comparisons do.
    started_at    TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
    completed_at  TEXT,
    error         TEXT
);

-- Per-theme brief de-dup fingerprint (mirrors 20260616130000_brief_finding_state.sql).
-- One row per theme ever surfaced in a brief; carries the convergence state at
-- last surface so the next run can tell whether the issue changed.
CREATE TABLE brief_finding_state (
    id                  TEXT PRIMARY KEY,
    enterprise_id       TEXT NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    theme_id            TEXT NOT NULL,
    last_brief_id       INTEGER,
    last_surfaced_at    TEXT NOT NULL DEFAULT (datetime('now')),
    fp_signal_count     INTEGER NOT NULL DEFAULT 0,
    fp_effective_weight REAL NOT NULL DEFAULT 0,
    fp_revenue_at_stake REAL NOT NULL DEFAULT 0,
    fp_breadth          INTEGER NOT NULL DEFAULT 0,
    fp_latest_signal_at TEXT,
    -- Phase 2 user-action (mirrors 20260616140000_brief_finding_state_action.sql
    -- + 20260727100000_brief_ledger_defer_rotation.sql).
    action              TEXT NOT NULL DEFAULT 'surfaced'
                        CHECK (action IN ('surfaced', 'prd_created', 'dismissed', 'deferred', 'done')),
    times_shown         INTEGER NOT NULL DEFAULT 0,
    deferred_until      TEXT,
    last_state          TEXT CHECK (last_state IS NULL OR last_state IN ('new', 'updated')),
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (enterprise_id, theme_id)
);
CREATE INDEX brief_finding_state_enterprise_idx ON brief_finding_state (enterprise_id);

-- Mirrors supabase/migrations/20260611100000_ticket_data.sql (SQLite-ized).
-- Ticket overrides keyed by a stable ticket_key + company_id.
CREATE TABLE ticket_edits (
    -- Workspace scope (20260716124000_workspace_scope_columns.sql).
    workspace_id TEXT,
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id          TEXT NOT NULL,
    ticket_key          TEXT NOT NULL,
    -- Nullable (per 20260628130000): a fields-only edit leaves these NULL so the
    -- UI keeps the generated body, distinct from an intentionally-saved empty.
    description         TEXT,
    acceptance_criteria TEXT,
    -- Mirrors supabase/migrations/20260628120000_ticket_edits_fields.sql
    title               TEXT,
    priority            TEXT,
    status              TEXT,
    sprint              TEXT,
    assignee            TEXT,
    -- Mirrors supabase/migrations/20260709120000_ticket_edits_subtasks.sql
    subtasks            TEXT,
    -- Mirrors supabase/migrations/20260712160000_ticket_edits_custom_fields.sql:
    -- tracker custom-field overrides keyed by field id (jsonb → TEXT here).
    custom_fields       TEXT,
    -- Mirrors supabase/migrations/20260712170000_ticket_edits_issue_type.sql
    issue_type          TEXT,
    -- Mirrors 20260731120000_ticket_edits_lifecycle.sql: 'active' | 'excluded'
    -- | 'deleted'. Non-active tickets are never pushed and are removed from
    -- the tracker if they were.
    lifecycle           TEXT NOT NULL DEFAULT 'active',
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, ticket_key)
);
CREATE TABLE ticket_attachments (
    workspace_id TEXT,
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id  TEXT NOT NULL,
    ticket_key  TEXT NOT NULL,
    label       TEXT NOT NULL,
    sub         TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_ticket_attachments_key ON ticket_attachments (company_id, ticket_key);
CREATE TABLE ticket_comments (
    workspace_id TEXT,
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id  TEXT NOT NULL,
    ticket_key  TEXT NOT NULL,
    author      TEXT NOT NULL DEFAULT 'user',
    body        TEXT NOT NULL,
    -- Mirrors 20260712180000_ticket_comments_tracker_id.sql: the tracker-side
    -- comment id once pushed (NULL = not pushed).
    tracker_comment_id TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_ticket_comments_key ON ticket_comments (company_id, ticket_key);

-- Persisted PRD-generated tickets (mirrors 20260627120000_prd_tickets.sql).
-- One row per PRD; the individual tickets are elements of the `stories` JSON
-- array (each has a stable `id` = ticket_key). Source of ticket EXISTENCE
-- (the ticket_edits/comments/attachments tables above only layer overrides on
-- top). bigint identity / jsonb / timestamptz are INTEGER / TEXT here.
CREATE TABLE prd_tickets (
    -- Workspace scope (20260716124000_workspace_scope_columns.sql).
    workspace_id TEXT,
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id    TEXT NOT NULL,
    prd_id        INTEGER NOT NULL UNIQUE,
    content_hash  TEXT NOT NULL DEFAULT '',
    stories       TEXT NOT NULL DEFAULT '[]',
    status        TEXT NOT NULL DEFAULT 'ready',
    error         TEXT,
    generated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    -- Which ticket format rendered the set (20260814120000_ticket_template_stamp.sql).
    artifact_template_id TEXT
);
CREATE INDEX idx_prd_tickets_company ON prd_tickets (company_id);

-- Standalone ticket sets (mirrors 20260806120000_ticket_sets.sql): tickets
-- generated from a chat with NO PRD behind them. Same `stories` JSON payload
-- shape as prd_tickets; its tickets are keyed `set-{id}-{story_id}` so they
-- share ticket_edits / ticket_comments / ticket_attachments with PRD tickets
-- while staying in a disjoint key namespace.
CREATE TABLE ticket_sets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      TEXT NOT NULL,
    workspace_id    TEXT,
    conversation_id INTEGER,
    title           TEXT NOT NULL DEFAULT '',
    source_text     TEXT NOT NULL DEFAULT '',
    stories         TEXT NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL DEFAULT 'generating',
    error           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    -- Which ticket format rendered the set (20260814120000_ticket_template_stamp.sql).
    artifact_template_id TEXT
);
CREATE INDEX ticket_sets_company_idx ON ticket_sets (company_id, id DESC);
CREATE INDEX ticket_sets_conversation_idx ON ticket_sets (conversation_id);

-- Per-artifact tracker sync state (mirrors 20260710120000_prd_ticket_sync.sql
-- + 20260806120000_ticket_sets.sql). One row per (company, PRD) OR
-- (company, ticket set): the ClickUp list / Jira project that artifact's
-- tickets sync with, the last sync outcome, and the pulled per-ticket tracker
-- statuses (jsonb → TEXT here).
--
-- prd_id is NULLABLE here exactly as the migration leaves it, and
-- ticket_set_id is its mutually-exclusive counterpart. Both UNIQUE constraints
-- are non-partial and rely on SQLite treating NULLs as distinct — the same
-- property Postgres has — so a set row (prd_id NULL) never collides with the
-- PRD constraint and vice versa. The fake client translates upsert
-- on_conflict= into a real SQLite ON CONFLICT target, so both indexes have to
-- exist for upsert_sync_config to resolve on either owner.
CREATE TABLE prd_ticket_sync (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id       TEXT NOT NULL,
    workspace_id     TEXT,
    prd_id           INTEGER,
    ticket_set_id    INTEGER,
    provider         TEXT NOT NULL,
    destination_id   TEXT NOT NULL,
    destination_name TEXT,
    auto_sync        INTEGER NOT NULL DEFAULT 1,
    sync_status      TEXT NOT NULL DEFAULT 'idle',
    sync_started_at  TEXT,
    last_synced_at   TEXT,
    last_error       TEXT,
    statuses         TEXT NOT NULL DEFAULT '{}',
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, prd_id),
    UNIQUE (company_id, ticket_set_id)
);

-- Idempotent Jira push mapping (mirrors 20260708120000_jira_issue_map.sql).
-- One row per (company, project, ticket) → the Jira issue a push created,
-- read by re-pushes and the ticket transitions route.
CREATE TABLE jira_issue_map (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id     TEXT NOT NULL,
    workspace_id     TEXT,
    project_key    TEXT NOT NULL,
    ticket_id      TEXT NOT NULL,
    jira_issue_key TEXT NOT NULL,
    updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, project_key, ticket_id)
);

-- Cached per-destination tracker vocabulary (mirrors
-- 20260712150000_tracker_meta.sql). One row per (company, provider,
-- destination): the normalized TrackerMeta snapshot (statuses / priorities /
-- issue types / custom fields) the ticket UI + sync engine read instead of
-- hitting the tracker live (jsonb → TEXT here).
CREATE TABLE tracker_meta (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id     TEXT NOT NULL,
    workspace_id     TEXT,
    provider       TEXT NOT NULL,
    destination_id TEXT NOT NULL,
    meta           TEXT NOT NULL DEFAULT '{}',
    fetched_at     TEXT NOT NULL DEFAULT (datetime('now')),
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, provider, destination_id)
);

-- Roadmap doc storage (mirrors 20260623120000_roadmap_doc.sql, SQLite-ized).
-- One row per company (UNIQUE company_id) so a re-upload upserts in place. Holds
-- the original file (base64) + extracted text the Top Insights brief reads + the
-- roadmapdoc artifact renders. bigint identity / timestamptz are INTEGER / TEXT
-- under SQLite, matching the other seeded tables.
CREATE TABLE roadmap_doc (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id     TEXT NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    -- Workspace scope (mirrors 20260716124000_workspace_scope_columns.sql):
    -- one roadmap per WORKSPACE now; the old unique(company_id) is gone.
    workspace_id   TEXT,
    filename       TEXT NOT NULL,
    content_type   TEXT,
    extracted_text TEXT NOT NULL DEFAULT '',
    raw_b64        TEXT,
    version        INTEGER NOT NULL DEFAULT 1,
    uploaded_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
-- Non-partial (unlike Postgres) so the fake's ON CONFLICT(workspace_id)
-- upsert matches; SQLite treats NULLs as distinct, so legacy no-workspace
-- rows still coexist.
CREATE UNIQUE INDEX roadmap_doc_workspace_id_key
    ON roadmap_doc (workspace_id);

-- Company templates storage (mirrors 20260623140000_company_template.sql,
-- SQLite-ized). MANY rows per company (unlike roadmap_doc's one-per-company):
-- each gold-standard PRD exemplar is its own row, listed + individually
-- deletable. Holds the original file (base64) + extracted text prd-author reads
-- as FORMAT/STYLE EXEMPLARS. uuid / timestamptz are TEXT here, matching the
-- other seeded tables.
CREATE TABLE company_template (
    id             TEXT PRIMARY KEY,
    company_id     TEXT NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    label          TEXT,
    type           TEXT NOT NULL DEFAULT 'prd',
    filename       TEXT NOT NULL,
    content_type   TEXT,
    extracted_text TEXT NOT NULL DEFAULT '',
    raw_b64        TEXT,
    uploaded_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX company_template_company_idx ON company_template (company_id);

-- Company documents storage (mirrors 20260626120000_company_document.sql,
-- SQLite-ized). The GENERALIZED sibling of roadmap_doc / company_template: a
-- SINGLE table with a `doc_type` discriminator instead of one table per kind.
-- MANY rows per company. Holds the original file (base64) + extracted text for a
-- future agent-context follow-up (STORED only for now). uuid / timestamptz are
-- TEXT here, matching the other seeded tables.
CREATE TABLE company_document (
    id             TEXT PRIMARY KEY,
    company_id     TEXT NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    workspace_id   TEXT,
    doc_type       TEXT NOT NULL
                     CHECK (doc_type IN (
                       'ceo_memo', 'team_priorities', 'research', 'company_strategy',
                       'team_strategy', 'team_roadmap', 'decision_process',
                       'additional_context', 'sizing_doc'
                     )),
    filename       TEXT NOT NULL,
    content_type   TEXT,
    extracted_text TEXT NOT NULL DEFAULT '',
    raw_b64        TEXT,
    uploaded_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX company_document_company_idx ON company_document (company_id);
CREATE INDEX company_document_company_type_idx
    ON company_document (company_id, doc_type);

-- Uploaded document sources (mirrors 20260723120000_document_sources.sql,
-- SQLite-ized). A NAMED bundle of user-uploaded files (+ an optional
-- description of what they are) surfaced as the `uploads` connector; the
-- uploads puller reads these rows and yields RawRecords into the KG. uuid /
-- timestamptz are TEXT here, matching the other seeded tables.
CREATE TABLE document_source (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    workspace_id TEXT,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX document_source_company_idx ON document_source (company_id);

CREATE TABLE document_source_file (
    id             TEXT PRIMARY KEY,
    source_id      TEXT NOT NULL REFERENCES document_source (id) ON DELETE CASCADE,
    company_id     TEXT NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    filename       TEXT NOT NULL,
    content_type   TEXT,
    size_bytes     INTEGER NOT NULL DEFAULT 0,
    extracted_text TEXT NOT NULL DEFAULT '',
    raw_b64        TEXT,
    uploaded_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX document_source_file_source_idx ON document_source_file (source_id);
CREATE INDEX document_source_file_company_idx ON document_source_file (company_id);

-- Document catalog (mirrors 20260803120000_document_catalog.sql, SQLite-ized).
-- One row per document-shaped item from ANY source, carrying an extractive
-- summary + topics + a summary embedding, so a document can be found by what
-- it is about. Both constraints that carry meaning are mirrored faithfully:
-- the unique triple the registration upsert conflicts on, and the check that
-- makes an ownerless session-scoped row unrepresentable.
--
-- NOT mirrored (no SQLite equivalent, and nothing under test needs them):
-- `search_tsv` (a generated tsvector maintained by Postgres) and the
-- ivfflat/GIN indexes. `embedding` and `topics` are JSON-encoded TEXT via the
-- fake's _JSONB_COLUMNS map. `document_find_candidates` is a Postgres
-- function; its tenancy filter is exercised against real Postgres, not here
-- (the fake's rpc() returns whatever a test registers).
CREATE TABLE document_catalog (
    -- Postgres fills this with gen_random_uuid(); the registration upsert
    -- deliberately never sends an `id` (sending one would rewrite the PK on
    -- every re-registration), so the mirror needs its own uuid4 default.
    id              TEXT PRIMARY KEY DEFAULT (
                        lower(hex(randomblob(4))) || '-'
                        || lower(hex(randomblob(2))) || '-4'
                        || substr(lower(hex(randomblob(2))), 2) || '-'
                        || substr('89ab', abs(random()) % 4 + 1, 1)
                        || substr(lower(hex(randomblob(2))), 2) || '-'
                        || lower(hex(randomblob(6)))
                    ),
    company_id      TEXT NOT NULL,
    workspace_id    TEXT,
    conversation_id INTEGER,
    user_id         TEXT,
    provider        TEXT NOT NULL,
    external_id     TEXT NOT NULL,
    -- The provider-side CONTAINER (Confluence space id today). Nullable, and
    -- the null-ness carries meaning that tests depend on: `IN` never matches
    -- NULL in either engine, so a row registered before this column existed
    -- is skipped by the container-keyed deregistration rather than swept up
    -- by it.
    container_id    TEXT,
    title           TEXT NOT NULL,
    source_name     TEXT NOT NULL DEFAULT '',
    url             TEXT,
    doc_date        TEXT,
    content_hash    TEXT NOT NULL,
    summary         TEXT NOT NULL DEFAULT '',
    topics          TEXT NOT NULL DEFAULT '[]',
    summary_model   TEXT,
    summary_version TEXT,
    -- Mirrors 20260811120000_document_catalog_provider_workspace.sql. NULLABLE
    -- and NULL-by-default on purpose: NULL means UNKNOWN, never "belongs to no
    -- workspace", and the tests below pin that a caller who does not know the
    -- workspace can neither clear nor invent one.
    provider_workspace_id TEXT,
    embedding       TEXT,
    body_text       TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, provider, external_id),
    CONSTRAINT document_catalog_session_needs_owner
        CHECK (conversation_id IS NULL OR user_id IS NOT NULL)
);
CREATE INDEX document_catalog_company_idx ON document_catalog (company_id);

-- Custom skills (mirrors 20260728180000_custom_skills.sql, SQLite-ized).
-- COMPANY-scoped user-uploaded skill definitions (all workspaces in a company
-- share one library; workspace_id records the uploading workspace only):
-- `method` is the parsed SKILL.md text injected at invocation time;
-- modules/refs are JSON-encoded TEXT maps. No company/workspace FKs, matching
-- the workspaces-table note: route tests fabricate tenant ids that have no
-- parent rows.
CREATE TABLE custom_skills (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL,
    workspace_id  TEXT NOT NULL,
    slug          TEXT NOT NULL,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL,
    method        TEXT NOT NULL,
    modules       TEXT NOT NULL DEFAULT '{}',
    refs          TEXT NOT NULL DEFAULT '{}',
    content_hash  TEXT NOT NULL,
    storage_key   TEXT,
    uploader_id   TEXT NOT NULL,
    uploader_name TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    -- Which synced folder produced this skill (20260807170000_skill_sources.sql).
    -- NULL for every hand-uploaded skill; non-NULL makes the skill read-only in
    -- the UI and at the PATCH route, because the repo owns its text.
    source_id     TEXT,
    UNIQUE (company_id, slug)
);
CREATE INDEX custom_skills_company_id_idx ON custom_skills (company_id);
CREATE INDEX custom_skills_source_id_idx ON custom_skills (source_id);

-- Synced skill folders (mirrors 20260807170000_skill_sources.sql, SQLite-ized).
-- One row per (company, repo, ref, path) folder a company keeps synced: the
-- 30-minute sweep re-runs GitHub discovery over it and re-imports every .md it
-- finds. `ref` empty means the repo's default branch, `path` empty the repo
-- root. `last_commit_sha` is the sweep's short-circuit — unchanged head means
-- no work. No company/workspace FKs, matching custom_skills above.
CREATE TABLE skill_sources (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    workspace_id    TEXT,
    installation_id INTEGER NOT NULL,
    repo            TEXT NOT NULL,
    ref             TEXT NOT NULL DEFAULT '',
    path            TEXT NOT NULL DEFAULT '',
    last_commit_sha TEXT NOT NULL DEFAULT '',
    last_synced_at  TEXT,
    last_error      TEXT NOT NULL DEFAULT '',
    active          INTEGER NOT NULL DEFAULT 1,
    created_by      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, repo, ref, path)
);
CREATE INDEX skill_sources_company_id_idx ON skill_sources (company_id);

-- Artifact format templates (mirrors 20260805120000_artifact_templates.sql,
-- SQLite-ized). COMPANY-scoped uploaded PRD / ticket / engineering-spec FORMS
-- (all workspaces in a company share one library and one active format per
-- type; workspace_id records the uploading workspace only and is never a query
-- filter). section_map / compile_notes are JSON-encoded TEXT, matching the real
-- column type. No company/workspace FKs, matching the workspaces-table note:
-- route tests fabricate tenant ids that have no parent rows.
--
-- `is_active INTEGER` + the PARTIAL unique index below are the load-bearing
-- part of this mirror: they are what makes activate_template's
-- deactivate-siblings-then-activate order testable, because the other order
-- trips the constraint here exactly as it does in Postgres.
CREATE TABLE artifact_templates (
    id             TEXT PRIMARY KEY,
    company_id     TEXT NOT NULL,
    workspace_id   TEXT NOT NULL,
    artifact_type  TEXT NOT NULL
                     CHECK (artifact_type IN ('prd', 'tickets', 'impl_spec')),
    name           TEXT NOT NULL,
    source_md      TEXT NOT NULL,
    source_chars   INTEGER NOT NULL DEFAULT 0,
    compiled       TEXT NOT NULL DEFAULT '',
    section_map    TEXT NOT NULL DEFAULT '{}',
    compile_status TEXT NOT NULL DEFAULT 'pending'
                     CHECK (compile_status IN
                            ('pending', 'compiling', 'ready', 'needs_review', 'failed')),
    compile_notes  TEXT NOT NULL DEFAULT '[]',
    -- Mirrors 20260812200000_artifact_templates_summary.sql: the LLM-written
    -- description a successful compile stores; '' = "no summary yet".
    summary        TEXT NOT NULL DEFAULT '',
    content_hash   TEXT NOT NULL DEFAULT '',
    is_active      INTEGER NOT NULL DEFAULT 0,
    uploader_id    TEXT NOT NULL,
    uploader_name  TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX artifact_templates_company_id_idx ON artifact_templates (company_id);
CREATE INDEX artifact_templates_company_type_idx
    ON artifact_templates (company_id, artifact_type);
CREATE UNIQUE INDEX artifact_templates_active_uniq
    ON artifact_templates (company_id, artifact_type) WHERE is_active = 1;

-- Captured HTML report documents (mirrors 20260730120000_reports.sql,
-- SQLite-ized). COMPANY-scoped (all workspaces in a company share one report
-- library; workspace_id records the generating workspace and may be NULL).
-- conversation_id / prd_id are the report's ATTACHMENT — the chat room and PRD
-- the run happened in, NULL when the ask carried neither. No FKs, matching the
-- workspaces-table note: route tests fabricate tenant ids with no parent rows.
-- share_* mirror 20260730130000_reports_share.sql: opt-in public access by
-- token, DEFAULT PRIVATE (nothing is reachable by link until explicitly shared).
CREATE TABLE reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      TEXT NOT NULL,
    workspace_id    TEXT,
    skill           TEXT NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    html            TEXT NOT NULL DEFAULT '',
    question        TEXT NOT NULL DEFAULT '',
    ask_id          INTEGER,
    conversation_id INTEGER,
    prd_id          INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    share_mode      TEXT NOT NULL DEFAULT 'private',
    share_token     TEXT,
    share_passcode_hash TEXT,
    shared_at       TEXT
);
CREATE INDEX reports_company_idx ON reports (company_id, id DESC);
CREATE UNIQUE INDEX reports_share_token_uniq ON reports (share_token)
    WHERE share_token IS NOT NULL;

-- Onboarding drip / nudge email tracking (mirrors
-- 20260614100000_drip_email_sends.sql). One row per delivered (company ×
-- member × step); UNIQUE is the de-dup guard so steps never double-send.
CREATE TABLE drip_email_sends (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL,
    step_key    TEXT NOT NULL,
    email       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'sent'
                  CHECK (status IN ('sent', 'skipped')),
    sent_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (company_id, user_id, step_key)
);
CREATE INDEX drip_email_sends_company_user_idx
    ON drip_email_sends (company_id, user_id);

-- Invite reminder drip tracking (mirrors
-- 20260720120000_invite_reminder_sends.sql). One row per delivered
-- (invite × step); UNIQUE is the de-dup guard. FK cascade from
-- workspace_invites so accept/revoke (which delete the invite) auto-clear it.
CREATE TABLE invite_reminder_sends (
    id          TEXT PRIMARY KEY,
    invite_id   TEXT NOT NULL REFERENCES workspace_invites (id) ON DELETE CASCADE,
    company_id  TEXT NOT NULL,
    email       TEXT NOT NULL,
    step_key    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'sent'
                  CHECK (status IN ('sent', 'skipped')),
    sent_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (invite_id, step_key)
);
CREATE INDEX invite_reminder_sends_invite_idx
    ON invite_reminder_sends (invite_id);

-- NOTE: the `prototypes` table is intentionally NOT in this shared base schema.
-- The ~40 Design Agent tests each create their own (richer) `prototypes` on the
-- singleton in-memory DB in their fixtures; a base-schema copy collides with
-- those ("table prototypes already exists"). The one consumer that reads it
-- through a route rather than creating it — tests/test_routes_internal_mcp.py —
-- creates the trimmed variant locally in its own fixture. See issue #697.

-- Customer-issued MCP API tokens (mirrors 20260707120000_mcp_tokens.sql +
-- 20260708120000_mcp_token_role.sql, SQLite-ized). uuid / timestamptz are
-- TEXT here, matching the other seeded tables.
CREATE TABLE mcp_tokens (
    id           TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    user_id      TEXT NOT NULL,
    name         TEXT NOT NULL DEFAULT 'MCP token',
    token_hash   TEXT NOT NULL UNIQUE,
    token_prefix TEXT NOT NULL,
    scopes       TEXT NOT NULL DEFAULT 'read',
    token_role   TEXT NOT NULL DEFAULT 'pm'
        CHECK (token_role IN ('developer', 'pm')),
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT,
    revoked_at   TEXT
);
CREATE INDEX mcp_tokens_company_idx ON mcp_tokens (company_id);

-- Chat history (mirrors 20260611110000_conversations.sql +
-- 20260611120000_conversation_turns.sql). prd_id links a conversation to the
-- PRD it's about (20260709130000_conversations_prd_id.sql) so a reopened PRD
-- tab can rehydrate its earlier turns via GET /v1/conversations/by-prd/{prd_id}.
CREATE TABLE conversations (
    -- Workspace scope (20260716124000_workspace_scope_columns.sql).
    workspace_id TEXT,
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id  TEXT NOT NULL,
    user_id     TEXT,
    title       TEXT NOT NULL DEFAULT '',
    preview     TEXT NOT NULL DEFAULT '',
    agent_type  TEXT NOT NULL DEFAULT 'ask',
    query       TEXT NOT NULL DEFAULT '',
    reply       TEXT NOT NULL DEFAULT '',
    pinned      INTEGER NOT NULL DEFAULT 0,
    prd_id      INTEGER,
    -- 20260731090000: Evidence half of the conversation<->artifact binding
    -- (mirrors prd_id above).
    evidence_id INTEGER,
    -- Additive group-chat columns (mirrors
    -- 20260813130100_conversations_project_columns.sql). Every pre-existing
    -- + future per-user chat keeps project_id NULL / kind='individual' by
    -- default, so the untouched per-user ownership path is unaffected.
    project_id  INTEGER REFERENCES projects (id) ON DELETE SET NULL,
    kind        TEXT NOT NULL DEFAULT 'individual'
                  CHECK (kind IN ('individual', 'group')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_conversations_company ON conversations (company_id, created_at);
CREATE INDEX idx_conversations_company_prd ON conversations (company_id, prd_id, updated_at);
CREATE INDEX conversations_evidence_idx ON conversations (evidence_id);
CREATE INDEX idx_conversations_project ON conversations (project_id, kind, updated_at);
CREATE UNIQUE INDEX uq_one_group_chat_per_project
    ON conversations (project_id) WHERE kind = 'group';

CREATE TABLE conversation_turns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    role            TEXT NOT NULL DEFAULT 'user',
    content         TEXT NOT NULL DEFAULT '',
    -- Extracted attachment texts [{name, content}] persisted with the turn
    -- (20260723170000_conversation_turn_attachments.sql).
    attachments     TEXT,
    -- Which human posted this turn (mirrors
    -- 20260813130100_conversations_project_columns.sql). NULL for
    -- assistant turns and every pre-existing single-owner-chat turn.
    author_user_id  TEXT,
    -- Why the group agent did/did not reply to this turn (mirrors
    -- 20260815180000_conversation_turns_trigger_kind.sql). NULL for every
    -- pre-existing turn and every non-group-decision turn.
    trigger_kind    TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_conv_turns_conv ON conversation_turns (conversation_id, created_at);

-- Unified per-call LLM usage ledger (20260725120000_llm_usage_events.sql).
-- The `llm_usage_summary` rollup is a Postgres function with no SQLite
-- equivalent; tests exercise the read path via FakeSupabaseClient.rpc_returns.
CREATE TABLE llm_usage_events (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id                  TEXT NOT NULL,
    user_id                     TEXT,
    feature                     TEXT NOT NULL,
    operation                   TEXT,
    provider                    TEXT NOT NULL DEFAULT 'anthropic',
    model                       TEXT,
    key_mode                    TEXT NOT NULL DEFAULT 'unknown',
    input_tokens                INTEGER NOT NULL DEFAULT 0,
    output_tokens               INTEGER NOT NULL DEFAULT 0,
    cache_creation_input_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_input_tokens     INTEGER NOT NULL DEFAULT 0,
    est_cost_usd                REAL,
    latency_ms                  INTEGER,
    status                      TEXT NOT NULL DEFAULT 'succeeded',
    error_class                 TEXT,
    created_at                  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_llm_usage_co_created ON llm_usage_events (company_id, created_at);

-- Artifact share-grant primitive (mirrors
-- 20260801130000_artifact_share_links.sql, SQLite-ized: bigint identity /
-- timestamptz are INTEGER / TEXT here). owner_company_id / owner_workspace_id
-- are plain TEXT with no FK, matching the workspaces-table note above — route
-- tests fabricate tenant ids that have no parent rows.
CREATE TABLE artifact_shares (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    token              TEXT NOT NULL UNIQUE,
    artifact_type      TEXT NOT NULL DEFAULT 'prd',
    artifact_id        INTEGER NOT NULL,
    owner_company_id   TEXT NOT NULL,
    owner_workspace_id TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    revoked_at         TEXT
);
CREATE INDEX artifact_shares_artifact_idx ON artifact_shares (artifact_type, artifact_id);

CREATE TABLE artifact_share_joins (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    share_id            INTEGER NOT NULL REFERENCES artifact_shares (id),
    joined_user_id      TEXT NOT NULL,
    joined_company_id   TEXT NOT NULL,
    joined_workspace_id TEXT NOT NULL,
    joined_at           TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (share_id, joined_user_id)
);

-- Projects + collaboration layer (mirrors 20260813130000_projects.sql,
-- 20260813130100_conversations_project_columns.sql [conversations/
-- conversation_turns columns are ALTERed onto those tables above],
-- 20260813130200_project_memory.sql). No FK on company_id/workspace_id
-- here — same reasoning as the `workspaces` table comment above: route
-- tests routinely fabricate tenant ids that have no `companies`/
-- `workspaces` row, and require_workspace's self-heal must be able to
-- insert for them.
CREATE TABLE projects (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id   TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    name         TEXT NOT NULL,
    origin       TEXT NOT NULL DEFAULT 'manual'
                   CHECK (origin IN ('manual', 'prd_auto', 'artifact')),
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    instructions TEXT
);
CREATE INDEX idx_projects_company_ws ON projects (company_id, workspace_id, updated_at);

CREATE TABLE project_members (
    project_id INTEGER NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    user_id    TEXT NOT NULL,
    added_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (project_id, user_id)
);

CREATE TABLE project_artifacts (
    project_id    INTEGER NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL
                    CHECK (artifact_type IN ('prd', 'evidence', 'prototype', 'report', 'ticket_set')),
    artifact_id   INTEGER NOT NULL,
    added_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (project_id, artifact_type, artifact_id)
);
CREATE INDEX idx_project_artifacts_lookup ON project_artifacts (project_id, added_at);

CREATE TABLE project_chat_members (
    conversation_id INTEGER NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    user_id         TEXT NOT NULL,
    joined_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (conversation_id, user_id)
);

CREATE TABLE project_memory_entries (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id             INTEGER NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    body                   TEXT NOT NULL,
    author_user_id         TEXT,
    promoted_by            TEXT CHECK (promoted_by IN ('agent')),
    source_conversation_id INTEGER REFERENCES conversations (id),
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at             TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK ((author_user_id IS NOT NULL) <> (promoted_by IS NOT NULL))
);
CREATE INDEX idx_pme_project ON project_memory_entries (project_id, updated_at);

CREATE TABLE project_memory_summary (
    project_id   INTEGER PRIMARY KEY REFERENCES projects (id) ON DELETE CASCADE,
    summary_md   TEXT NOT NULL,
    entry_count  INTEGER NOT NULL,
    generated_at TEXT NOT NULL DEFAULT (datetime('now')),
    stale        INTEGER NOT NULL DEFAULT 0
);

-- Mirrors 20260813130300_project_delegations.sql. No FK on
-- assigner/assignee user_id — same reasoning as project_members above,
-- these are auth.users ids the fake DB never seeds a row for.
CREATE TABLE project_delegations (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id                INTEGER NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    assigner_user_id          TEXT NOT NULL,
    assignee_user_id          TEXT NOT NULL,
    task_summary              TEXT NOT NULL,
    source_conversation_id    INTEGER REFERENCES conversations (id),
    source_turn_id            INTEGER,
    delivered_conversation_id INTEGER REFERENCES conversations (id),
    delivered_turn_id         INTEGER,
    created_at                TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_project_delegations_project  ON project_delegations (project_id, created_at);
CREATE INDEX idx_project_delegations_assignee ON project_delegations (assignee_user_id, created_at);
CREATE INDEX idx_project_delegations_assigner ON project_delegations (assigner_user_id, created_at);

-- Mirrors 20260813140100_delegation_events.sql. No FK on actor_user_id —
-- same reasoning as project_delegations above (auth.users ids the fake
-- DB never seeds a row for). The migration's own CHECK constraint and
-- `v_delegation_status` left-join-lateral view are NOT mirrored here —
-- sqlite cannot enforce/evaluate either; those are proven by the real
-- local-Supabase round-trip (test_delegation_events.py). This mirror
-- exists only so the genesis-emit fast-lane tests in
-- test_project_delegation.py can insert against FakeSupabaseClient.
CREATE TABLE delegation_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    delegation_id INTEGER NOT NULL REFERENCES project_delegations (id) ON DELETE CASCADE,
    event         TEXT NOT NULL,
    actor_user_id TEXT NOT NULL,
    note          TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_delegation_events_delegation ON delegation_events (delegation_id, id);

-- Mirrors 20260813130400_conversation_read_cursors.sql. Inputs-only read
-- cursor (AD-P3/AD-P20) — no `unread` boolean/count column anywhere;
-- unread is derived at read time by the db helper. No FK on user_id —
-- same reasoning as project_delegations above (auth.users ids the fake
-- DB never seeds a row for).
CREATE TABLE conversation_read_cursors (
    conversation_id   INTEGER NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    user_id           TEXT NOT NULL,
    last_read_turn_id INTEGER NOT NULL DEFAULT 0,
    updated_at        TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (conversation_id, user_id)
);

-- Mirrors 20260814140000_delegation_followups.sql. Inputs/facts-only
-- cadence-scheduling row (AD-P17) — no derived status column. The
-- migration's own partial `where muted = false` index is not mirrored
-- (sqlite supports partial indexes, but nothing in the fast lane needs
-- it); RLS is a real-Postgres concern proven by
-- test_delegation_followups.py, not sqlite. This mirror exists only so
-- `delegation_status_ingest.py`'s fast-lane tests can upsert/read against
-- FakeSupabaseClient.
CREATE TABLE delegation_followups (
    delegation_id       INTEGER PRIMARY KEY REFERENCES project_delegations (id) ON DELETE CASCADE,
    expected_completion TEXT,
    next_check_in       TEXT,
    last_checked_in     TEXT,
    muted               INTEGER NOT NULL DEFAULT 0,
    pending_done_since  TEXT,
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Mirrors 20260814150000_delegation_followup_sends.sql. Idempotent
-- per-company send-ledger for the autonomous task follow-up sweep; the
-- UNIQUE below is the fast-lane's proof-stand-in for the migration's own
-- constraint (the real-Postgres RLS/policy shape is proven by
-- test_delegation_followup_sends.py, not sqlite). This mirror exists only
-- so `delegation_followup.py`'s stubbed-LLM sweep tests can record/read
-- sends against FakeSupabaseClient.
CREATE TABLE delegation_followup_sends (
    id               TEXT PRIMARY KEY,
    delegation_id    INTEGER NOT NULL REFERENCES project_delegations (id) ON DELETE CASCADE,
    company_id       TEXT NOT NULL,
    assignee_user_id TEXT NOT NULL,
    check_key        TEXT NOT NULL,
    channel          TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'sent',
    sent_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (delegation_id, check_key, channel)
);
CREATE INDEX idx_delegation_followup_sends_person
    ON delegation_followup_sends (assignee_user_id, sent_at);
CREATE INDEX idx_delegation_followup_sends_deleg
    ON delegation_followup_sends (delegation_id);

-- Mirrors supabase/migrations/20260812130000_call_transcripts.sql (SQLite-ized:
-- bigint identity / jsonb / timestamptz are INTEGER / TEXT here). The persisted
-- call transcripts the VoC digest reads instead of live-fetching per question.
create table if not exists call_transcripts (
    id            integer primary key autoincrement,
    company_id    text not null,
    provider      text not null,
    external_id   text not null,
    call_date     text,
    payload       text not null,
    fetched_at    text not null default '',
    unique (company_id, provider, external_id)
);
"""


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


@pytest.fixture
def tmp_data_dir(tmp_path: Path, repo_root: Path) -> Path:
    """A clean DATA_DIR seeded with the PRD/evidence templates."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for name in (
        "sprntly_prd_template.md",
        "sprntly_evidence_template.md",
    ):
        src = repo_root / "data" / name
        if src.exists():
            shutil.copy(src, data_dir / name)
    return data_dir


@pytest.fixture
def isolated_settings(tmp_path: Path, tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("TEMPLATE_DIR", str(tmp_data_dir))
    monkeypatch.setenv("DEMO_PASSWORD", "test-pw")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:3000")
    monkeypatch.setenv("COOKIE_DOMAIN", "")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    monkeypatch.setenv("ENV", "test")
    # Provide non-empty Supabase env so require_client() doesn't 500.
    # The values are unused — supabase_client() is patched below.
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "fake-service-role-key")

    _reload_app_modules()

    # Wire the in-memory fake Supabase + reset the schema per-test.
    reset_fake_db(_FAKE_SCHEMA)
    # Re-detect the connections tenant column against the FRESH db. _owner_column()
    # caches its probe result in a module global; if that probe ever ran against a
    # closed/half-reset db (e.g. a background sync thread racing reset_fake_db) it
    # would cache the legacy "workspace_id" and every later upsert_connection would
    # insert a NULL company_id. Clearing it here forces a clean re-detect per test.
    import app.db.connections as _conn_db
    _conn_db._OWNER_COL = None
    fake_client = FakeSupabaseClient()
    import app.db.client as db_client_mod
    monkeypatch.setattr(db_client_mod, "supabase_client", lambda: fake_client)
    db_client_mod._reset_supabase_client_for_tests()

    import app.config as config_mod
    import app.corpus as corpus_mod
    import app.db as db_mod
    yield {
        "config": config_mod,
        "db": db_mod,
        "corpus": corpus_mod,
        "data_dir": tmp_data_dir,
        "supabase": fake_client,
    }


@pytest.fixture(autouse=True)
def _no_background_connector_sync(request, monkeypatch):
    """Keep connect / upload / scheduler routes from spawning real background
    sync threads during tests.

    kickoff_sync and kickoff_corpus_seed each start a daemon thread (see
    app.kg_ingest.auto_sync) that pulls from a provider's LIVE API and writes
    sync stamps back through the shared in-memory test DB. In the full suite
    that thread is doubly harmful: (a) it makes real network calls (the
    api.fireflies.ai hits seen in CI), and (b) it races the per-test
    reset_fake_db() — if its db.get_connection() → _owner_column() probe runs
    while the DB is mid-reset, the probe SELECT throws and _OWNER_COL caches the
    legacy "workspace_id", after which every upsert_connection inserts a NULL
    company_id ("NOT NULL constraint failed: connections.company_id"). That is
    the intermittent, order-dependent pytest-integration failure.

    Patch the SOURCE functions in app.kg_ingest.auto_sync to no-ops. The route
    modules (app.routes.connectors/.brief/.datasets) are reloaded per test, so a
    reloaded `from auto_sync import kickoff_sync` re-binds to whatever the source
    exposes now — auto_sync itself is never reloaded, so this patch sticks. The
    scheduler is NOT reloaded, so its already-bound reference is patched directly.

    The two modules that unit-test these helpers directly (real thread-spawn
    behavior, with internals mocked) opt out. Tests that patch a route/scheduler
    reference themselves run after this fixture and win for that test."""
    if request.module.__name__.rsplit(".", 1)[-1] in (
        "test_connector_auto_sync",
        "test_corpus_seed_kickoff",
        "test_roadmap_kg_ingest",
    ):
        yield
        return

    def _noop_sync(*_a, **_k):
        return False

    def _noop_seed(*_a, **_k):
        return None

    try:
        auto_sync = importlib.import_module("app.kg_ingest.auto_sync")
        monkeypatch.setattr(auto_sync, "kickoff_sync", _noop_sync, raising=False)
        monkeypatch.setattr(auto_sync, "kickoff_corpus_seed", _noop_seed, raising=False)
        # Same rationale for the roadmap ingest kickoff (POST
        # /v1/company/roadmap-doc): its daemon thread would run a real LLM
        # extraction against the mid-reset in-memory DB.
        monkeypatch.setattr(auto_sync, "kickoff_roadmap_ingest", _noop_seed,
                            raising=False)
        # And for Slack's corpus kickoff, now that the OAuth callback fires it
        # on connect (not just the 6-hourly scheduler): its thread runs
        # sync_slack against the LIVE slack.com API and stamps the connection
        # row, so every test that drives /v1/connectors/slack/callback would
        # otherwise inherit exactly the two hazards above.
        monkeypatch.setattr(auto_sync, "kickoff_slack_corpus_sync", _noop_sync,
                            raising=False)
        # And for the call-index refresh, fired by POST /v1/connectors/fireflies
        # /apikey and by the scheduler cycle: its thread hits api.fireflies.ai
        # for real and upserts call_index / call_index_sync through the same
        # mid-reset DB — the identical pair of hazards.
        monkeypatch.setattr(auto_sync, "kickoff_call_index_sync", _noop_sync,
                            raising=False)
    except Exception:
        pass
    try:
        # app.routes.company is NOT in _RELOAD_ORDER, so its `from auto_sync
        # import kickoff_roadmap_ingest` binding is fixed at first import and the
        # source patch above can't reach it — patch the route's own reference too.
        company_route = importlib.import_module("app.routes.company")
        monkeypatch.setattr(company_route, "kickoff_roadmap_ingest", _noop_seed,
                            raising=False)
    except Exception:
        pass
    try:
        scheduler_mod = importlib.import_module("app.scheduler")
        monkeypatch.setattr(scheduler_mod, "kickoff_sync", _noop_sync, raising=False)
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _no_background_template_compile(request, monkeypatch):
    """Keep POST/PATCH /v1/artifact-templates from starting a real format check.

    `schedule_compile` (app.artifact_templates.compile_prd) claims the row and
    runs the compile on a background thread — and that compile goes through
    `graph.gateway.llm_call`, which holds its OWN `call_json` reference bound at
    import time. The `fake_llm` fixture patches `app.llm.call_json`, which does
    NOT reach the gateway's binding, so an unguarded upload in any route test
    would fire a REAL Anthropic request from a daemon thread, against the
    mid-reset in-memory DB — the same pair of hazards
    `_no_background_connector_sync` above exists for.

    Returning False (not True) is what keeps the route's contract intact under
    the patch: `_with_compile_started` reads False as "a check is already in
    flight", leaves the row alone, and the response still describes the row the
    write produced.

    Opt out with `@pytest.mark.real_template_compile` — the compile suite does,
    and drives the gateway with its own stub."""
    if request.node.get_closest_marker("real_template_compile"):
        yield
        return
    import importlib

    def _noop_schedule(company_id, template_id):  # noqa: ARG001
        return False

    # Patched on BOTH modules: routes/artifact_templates.py does
    # `from ...compile_prd import schedule_compile`, so its binding is fixed at
    # import and patching only the source module cannot reach it.
    for mod_name in ("app.artifact_templates.compile_prd",
                     "app.routes.artifact_templates"):
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        if hasattr(mod, "schedule_compile"):
            monkeypatch.setattr(mod, "schedule_compile", _noop_schedule,
                                raising=False)

    # The summary self-heal is the same hazard through a different door: the
    # templates LIST route (and the planner's catalog read) schedules a
    # background summarize for any ready row with an empty summary — which is
    # every ready row a test seeds — and that call is a gateway `llm_call` too.
    # 0 = "nothing scheduled", which is the function's no-work return, so list
    # responses keep their shape under the patch. Same two-module patching,
    # same reason. The summarize suite opts out with the marker above and
    # drives the gateway with its own stub.
    def _noop_summaries(company_id, rows):  # noqa: ARG001
        return 0

    for mod_name in ("app.artifact_templates.summarize",
                     "app.routes.artifact_templates"):
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        if hasattr(mod, "schedule_missing_summaries"):
            monkeypatch.setattr(mod, "schedule_missing_summaries",
                                _noop_summaries, raising=False)
    yield


@pytest.fixture(autouse=True)
def _no_referent_adjudication(request, monkeypatch):
    """Keep document RESOLUTION from firing a real model call.

    `document_referent.adjudicate` runs on the ask path whenever a question
    carries a document cue and a candidate clears the content-term floor — and
    it goes through `graph.gateway.llm_call`, which holds its own `call_json`
    reference bound at import time and so is NOT reached by `fake_llm`
    (see `_no_background_template_compile` above for the same hazard).
    Several existing ask tests ask cue-bearing questions in passing; without
    this guard each of them would attempt a real Anthropic request and sit on
    a network timeout before failing open.

    Returning None is the resolver's own no-referent answer, so a guarded test
    sees exactly the grounding it saw before resolution existed — the guard
    cannot mask a resolution bug by inventing a resolution.

    Opt out with `@pytest.mark.real_referent_adjudication`;
    `test_document_referent.py`'s own suite stubs `adjudicate` with a
    controllable fake instead, which overrides this for the tests that need to
    steer the verdict."""
    if request.node.get_closest_marker("real_referent_adjudication"):
        yield
        return
    import importlib

    try:
        mod = importlib.import_module("app.document_referent")
    except Exception:
        yield
        return
    monkeypatch.setattr(
        mod, "adjudicate", lambda **kw: None, raising=False
    )
    yield


@pytest.fixture(autouse=True)
def _no_background_memory_synthesis(request, monkeypatch):
    """Keep a project-memory mutation from firing a REAL Anthropic request.

    `schedule_regen` (`app.project_memory`) runs `regenerate_summary` INLINE
    under pytest by design (the writer's own contract), and EVERY
    `add_memory`/`update_memory`/`delete_memory` route call triggers it —
    not just the tests that mean to exercise synthesis. Without this guard,
    every existing memory-CRUD test (`test_project_memory_entries.py`, and
    any other route test that adds/edits/deletes a memory entry in passing)
    would fire a real `call_md` request against Anthropic using the suite's
    fake API key — the exact hazard class `_no_background_template_compile`/
    `_no_referent_adjudication` above exist for.

    A test that means to drive synthesis (`test_project_memory.py`) patches
    `app.project_memory.call_md` itself; that patch runs AFTER this autouse
    fixture and wins for that test (same ordering the two guards above rely
    on). Opt out with `@pytest.mark.real_memory_synthesis` — the dedicated
    real-LLM live suite drives an UNSTUBBED `call_md` instead.
    """
    if request.node.get_closest_marker("real_memory_synthesis"):
        yield
        return
    import importlib

    def _fake_call_md(*, system, user, model, meta_out=None, **kwargs):  # noqa: ARG001
        if meta_out is not None:
            meta_out.update(
                {
                    "model": model,
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                }
            )
        return "Autouse placeholder synthesis summary for isolated tests."

    try:
        mod = importlib.import_module("app.project_memory")
    except Exception:
        yield
        return
    monkeypatch.setattr(mod, "call_md", _fake_call_md, raising=False)
    yield


@pytest.fixture(autouse=True)
def _no_background_origin_seed(request, monkeypatch):
    """Keep the project-origin-seed's summarizer call from firing a REAL
    Anthropic request.

    `maybe_auto_create_project_for_prd`'s new-project branch calls
    `seed_project_origin_memory`, which makes ONE bounded `call_json` call —
    not just from the tests that mean to exercise the seed itself. ANY test
    that creates a `prd_auto` project (via `/v1/prd/generate-from-task`,
    `/v1/prd/import`, or the helper called directly — e.g.
    `test_project_from_prd.py`'s existing suite) would otherwise fire a real
    Anthropic request using the suite's fake API key — the same hazard class
    `_no_background_memory_synthesis`/`_no_background_interjection_gate`
    above exist for. Confirmed empirically: without this guard, the existing
    `test_project_from_prd.py` suite still passes (the seed's own AD-P7
    fallback swallows the failed call), but it does so only because this
    sandbox has no network egress — a CI runner with egress would instead
    round-trip a real 401 against Anthropic on every such test, or worse,
    spend real credit if a valid key ever leaked into the test environment.

    Defaults to a blank brief/decisions so the seed's own deterministic
    PRD-derived fallback brief still writes (never an empty-memory surprise
    for an unrelated test that happens to assert on entry counts). A test
    that means to drive the seed itself (`test_project_origin_seed.py`)
    patches `app.project_origin_seed.call_json` directly; that patch runs
    AFTER this autouse fixture and wins for that test (same ordering the
    sibling guards rely on). Opt out with
    `@pytest.mark.real_origin_seed_synthesis` — the dedicated real-LLM live
    suite drives an UNSTUBBED `call_json` instead."""
    if request.node.get_closest_marker("real_origin_seed_synthesis"):
        yield
        return
    import importlib

    def _fake_call_json(*, system, user, model, schema=None, meta_out=None, **kwargs):  # noqa: ARG001
        if meta_out is not None:
            meta_out.update(
                {
                    "model": model,
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                }
            )
        return {"brief_summary": "", "decisions": []}

    try:
        mod = importlib.import_module("app.project_origin_seed")
    except Exception:
        yield
        return
    monkeypatch.setattr(mod, "call_json", _fake_call_json, raising=False)
    yield


@pytest.fixture(autouse=True)
def _no_background_interjection_gate(request, monkeypatch):
    """Keep a non-mention group turn from firing a REAL Anthropic request.

    `post_group_turn_route` consults `project_group_gate.should_respond` on
    EVERY non-mention group turn that clears the gate's own cheap
    pre-filter — not just the tests that mean to exercise the gate.
    Without this guard, an existing group-chat/memory-CRUD test that posts
    a non-trivial non-mention turn (e.g. `test_group_chat_turns.py`'s "a
    turn nobody should log verbatim", or `test_project_memory_promotion.py`'s
    "morning team, nothing to see here") would fire a real `call_json`
    request against Anthropic using the suite's fake API key — the same
    hazard class `_no_background_memory_synthesis` above exists for.

    Defaults to `{"respond": False}` — the gate's own conservative
    default — so the stub can never manufacture a spurious interjection in
    an unrelated test. A test that means to drive the gate itself
    (`test_project_group_gate.py`) patches `app.project_group_gate.call_json`
    directly; that patch runs AFTER this autouse fixture and wins for that
    test (same ordering `_no_background_memory_synthesis` relies on). Opt
    out with `@pytest.mark.real_interjection_gate` — the dedicated
    real-LLM live tier drives an UNSTUBBED `call_json` instead."""
    if request.node.get_closest_marker("real_interjection_gate"):
        yield
        return
    import importlib

    def _fake_call_json(*, system, user, model, schema=None, meta_out=None, **kwargs):  # noqa: ARG001
        if meta_out is not None:
            meta_out.update(
                {
                    "model": model,
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                }
            )
        return {"respond": False}

    try:
        mod = importlib.import_module("app.project_group_gate")
    except Exception:
        yield
        return
    monkeypatch.setattr(mod, "call_json", _fake_call_json, raising=False)
    yield


@pytest.fixture(autouse=True)
def _reset_iterate_limiter():
    """Per-test isolation for the Design Agent rate limiters.

    `app.design_agent.rate_limit` holds process-level `SlidingWindowLimiter`
    singletons keyed by a request attribute:

      - ITERATE_LIMITER        — keyed by `prototype_id` (P5-04).
      - PUBLIC_TOKEN_LIMITER   — keyed by the share token (P5-07).
      - PUBLIC_COMMENT_LIMITER — keyed by the client IP (P5-07).

    Tests use a fresh per-test DB whose autoincrement restarts at 1 (so iterate
    tests reuse key "1"), and the public-comment limiter is keyed by the
    TestClient's constant "testclient" host (so EVERY public-comment POST in the
    whole suite shares one key). Without this reset those windows accumulate across
    the session and unrelated tests would spuriously 429. Clearing the windows
    (rather than reloading the module) keeps the singletons' class identity stable,
    so isinstance checks against them still hold under full-suite ordering."""
    try:
        from app.design_agent.rate_limit import (
            ITERATE_LIMITER,
            PUBLIC_COMMENT_LIMITER,
            PUBLIC_TOKEN_LIMITER,
        )

        ITERATE_LIMITER._events.clear()
        PUBLIC_TOKEN_LIMITER._events.clear()
        PUBLIC_COMMENT_LIMITER._events.clear()
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _clear_auth_caches():
    """Per-test isolation for the in-process auth/tenancy TTL caches.

    `app.db.authcache` holds module-level TTLMap singletons (memberships,
    profile names, workspace rows) that survive the per-test module reloads
    (authcache is not in _RELOAD_ORDER, and nothing resets it). Tests reuse
    the same user/company/workspace ids against a FRESH fake DB each test,
    so an entry cached in one test would leak stale rows — or worse, rows
    that no longer exist — into the next. Clear on both sides of each test
    so a test's own cache writes can't outlive it either."""
    try:
        from app.db import authcache

        authcache.clear_all()
    except Exception:
        pass
    yield
    try:
        from app.db import authcache

        authcache.clear_all()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _no_real_browser_in_preview_capture(monkeypatch):
    """Keep real Chromium out of the test session.

    The generation-complete hook captures a preview screenshot of the staged
    bundle by rendering it in headless Chromium. Every completion-path test would
    otherwise launch a real browser (the host has Chromium installed), which is
    slow and non-deterministic. Patch the screenshot module's lazy Playwright seam
    to raise ImportError so `capture_bundle_screenshot` honest-degrades to None
    without ever launching a browser — the documented test posture for that module.

    Tests that genuinely exercise capture override this: the screenshot unit tests
    re-patch this same seam to inject a fake Playwright graph, and completion-path
    success tests mock the route's `capture_bundle_screenshot` to return fake bytes.
    Both run after this autouse fixture, so their patch wins for that test.

    The report-PDF renderer (app/report_pdf.py) has the same lazy seam and is
    stubbed here too, so a report download test degrades to None (→ 503) instead
    of launching Chromium."""

    def _no_playwright():
        raise ImportError("playwright disabled in tests")

    for mod_name in ("app.design_agent.screenshot", "app.report_pdf"):
        try:
            import importlib

            mod = importlib.import_module(mod_name)
            monkeypatch.setattr(
                mod, "_resolve_async_playwright", _no_playwright, raising=False
            )
        except Exception:
            pass
    yield


@pytest.fixture(scope="session", autouse=True)
def _no_llm_usage_background_writer():
    """Disable `app.db.llm_usage`'s process-wide background flusher thread
    before ANY test in this worker/process runs.

    `_ensure_writer()` lazily spawns a daemon thread (`llm-usage-writer`) the
    FIRST time any code path calls `record_usage()` (e.g. any `install_metering`-
    wrapped LLM client). That thread runs `while True` for the rest of the
    process, flushing whatever's buffered via `require_client()` on its own
    5-second cadence — resolved dynamically, so it keeps firing long after
    whichever test originally started it, into WHATEVER test's fake DB happens
    to be current at that moment. `test_llm_usage_metering.py` already opts
    itself out locally (`disable_background_writer()` + `reset_for_tests()`),
    but that only helps once THAT file happens to run — under `-n auto`, most
    worker processes never execute it at all, so the writer stays live and
    contends `_fake_supabase._LOCK` at unpredictable points for their entire
    session. Confirmed via instrumentation: this thread is the direct cause of
    an intermittent class of unrelated test failures (ticket-sync tests
    observing a mid-flight state their own setup never produced) that only
    reproduces under parallel execution. Disabling it session-wide, once, up
    front removes the hazard outright — buffered rows are just queued
    (harmless; this ledger is explicitly fail-open/analytics-only, see
    `app/db/llm_usage.py`'s module docstring) until a test explicitly calls
    `flush()`, exactly as that module already documents."""
    from app.db import llm_usage

    llm_usage.disable_background_writer()


@pytest.fixture(autouse=True)
async def _drain_orphaned_executor_work():
    """STRUCTURAL fix for cross-test contamination via orphaned
    `asyncio.to_thread`/`loop.run_in_executor` background work — the actual
    root cause behind an intermittent class of failures in the ticket-sync /
    fake-tracker test family (test_ticket_sync.py, test_ticket_lifecycle.py,
    test_tracker_native_sync.py and any other test sharing that fixture
    machinery) that reproduced even after the targeted per-test fixes below
    (see git history on this fixture's neighbors) and got WORSE, not better,
    on GitHub's 2-vCPU runners — a slower run gives an orphaned thread more
    real wall-clock time to land badly.

    `asyncio.run()` is safe: its cleanup calls `shutdown_default_executor()`,
    which BLOCKS until every `to_thread` call has actually finished (verified
    directly — a fire-and-forget `asyncio.create_task(...)` whose coroutine is
    suspended inside `to_thread` when the outer coroutine returns still keeps
    `asyncio.run()` from returning until that executor thread is done,
    because cancelling the asyncio-level future does NOT interrupt an
    already-dispatched executor thread).

    pytest-asyncio's own per-test event loop teardown does NOT do this — it
    calls `loop.close()` directly (see `pytest_asyncio/plugin.py`), with no
    executor drain. Confirmed directly: with that teardown, an orphaned
    `to_thread` call keeps running for its FULL duration strictly AFTER
    "the test" has already returned and the next one has started — meaning
    ANY test (not just the couple already found and stubbed) that exercises a
    route/function scheduling `asyncio.create_task(...)` fire-and-forget work
    (PRD/impl-spec pre-warm, ticket-generation warm, connector sync kicks,
    etc.) without itself explicitly draining that work is a potential source,
    regardless of which specific test files happen to intersect with the
    ticket-sync fixture family. This fixture makes every pytest-asyncio-
    managed test wait the same way `asyncio.run()` already does, closing the
    hole at its source rather than in each downstream victim.

    Async (not sync) so pytest-asyncio hands it a REAL running loop to await
    on for its teardown — `asyncio_mode = auto` still wraps this correctly
    for sync test functions too (verified). A test with no orphaned work
    pays ~nothing (shutdown_default_executor() on an idle executor returns
    immediately)."""
    yield
    import asyncio

    loop = asyncio.get_running_loop()
    await loop.shutdown_default_executor()


@pytest.fixture(autouse=True)
def _no_leftover_daemon_threads():
    """Guard against a fire-and-forget daemon thread (`kick_prd_sync_from_key`,
    `kick_comment_push`, `kick_comment_delete`, `app.kg_ingest.auto_sync`'s
    kickoffs, and anything else following that pattern) outliving its own
    test and racing a LATER test's freshly-reset fake DB / class-level fixture
    state.

    These kicks resolve their targets (`_Tracker`, `supabase_client()`, etc.)
    by NAME at call time, not at thread-spawn time — so a thread still mid-
    flight when the next test's `isolated_settings`/`fake_tracker` fixtures
    reset that state runs against the NEW test's fake DB using the OLD test's
    captured ids, corrupting a completely unrelated test (this is the
    documented "intermittent, order-dependent" hazard `_no_background_
    connector_sync` calls out; a handful of tests intentionally exercise a
    real kick thread and only wait on an observable side effect, not a
    `join()`). Snapshot the live threads before the test, then after, join
    anything NEW with a bounded grace period — a legitimate daemon thread has
    already done its (in-memory, fast) work by the time the test's own
    assertions pass, so this is a no-op in the common case and only matters
    when a thread is still mid-unwind."""
    import threading

    before = set(threading.enumerate())
    yield
    for t in threading.enumerate():
        if t not in before and t is not threading.current_thread() and t.daemon:
            t.join(timeout=5)


@pytest.fixture
def fake_llm(isolated_settings, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Patch every imported reference to `call_json` so no test ever hits Anthropic."""
    state: dict[str, Any] = {
        "payload": {"week_label": "Test Week", "_schema_version": 1, "insights": []},
        "calls": [],
    }

    def _fake_call_json(system: str, user: str, **kwargs):  # noqa: ARG001
        state["calls"].append({"system": system, "user": user, "kwargs": kwargs})
        return state["payload"]

    import app.llm as llm_mod
    monkeypatch.setattr(llm_mod, "call_json", _fake_call_json, raising=False)
    for mod_name in (
        "app.brief_runner",
        "app.ask_runner",
        "app.evidence_runner",
        "app.prd_runner",
        "app.routes.brief",
        "app.routes.ask",
        "app.routes.evidence",
        "app.routes.prd",
    ):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "call_json"):
            monkeypatch.setattr(mod, "call_json", _fake_call_json, raising=False)
    return state


@pytest.fixture
def app_client(fake_llm) -> TestClient:
    """A FastAPI TestClient with the auth cookie pre-set via a real login call."""
    import app.main as main_mod
    client = TestClient(main_mod.app)
    resp = client.post("/v1/auth/login", json={"password": "test-pw"})
    assert resp.status_code == 200, resp.text
    return client


@pytest.fixture
def unauth_client(fake_llm) -> TestClient:
    """TestClient without authentication, for testing the auth gate itself."""
    import app.main as main_mod
    return TestClient(main_mod.app)


# ── P6-10: Supabase-bearer auth seam for the Design Agent route suites ────────
# After the require_app_session → require_company migration, the authed DA routes
# gate on a Supabase `Authorization: Bearer` JWT + a company_members row (resolved
# by require_company) instead of the legacy `sprntly_app_session` cookie. These
# helpers + the `company_client` fixture give the route suites a bearer-authed
# client whose resolved `workspace_id` is `_TEST_COMPANY_ID`. The JWT shape +
# membership seed are lifted verbatim from test_require_company.py (_mint_token,
# _seed_membership) so the suites exercise the real require_company path.
_TEST_SUPABASE_SECRET = "shared-hs256-test-secret"
_TEST_COMPANY_ID = "co-test"
_TEST_USER_ID = "user-test"


def _mint_supabase_token(sub: str = _TEST_USER_ID) -> str:
    """An HS256 Supabase JWT (aud='authenticated') the way require_session/
    _decode_supabase_token expects. Mirrors test_require_company._mint_token."""
    return pyjwt.encode(
        {"sub": sub, "aud": "authenticated", "exp": int(time.time()) + 300},
        _TEST_SUPABASE_SECRET,
        algorithm="HS256",
    )


def _bearer_header(sub: str = _TEST_USER_ID) -> dict[str, str]:
    """`Authorization: Bearer <token>` header dict for the given user."""
    return {"Authorization": f"Bearer {_mint_supabase_token(sub)}"}


def _seed_company_membership(
    db,
    company_id: str = _TEST_COMPANY_ID,
    user_id: str = _TEST_USER_ID,
    role: str = "owner",
) -> None:
    """Seed a company_members row so require_company resolves user_id → company_id.
    Mirrors test_require_company._seed_membership. `db` is the fake Supabase client
    (isolated_settings["supabase"])."""
    # The connector-multitenancy slice (#136) turned on PRAGMA foreign_keys in the
    # fake supabase, so an orphan company_members row now violates the FK to
    # companies(id). Seed the parent first (mirrors
    # test_require_company._seed_membership). Existence-guarded so a test that
    # already seeded the company doesn't hit a duplicate-PK.
    existing = (
        db.table("companies").select("id").eq("id", company_id).execute().data
    )
    if not existing:
        db.table("companies").insert(
            {
                "id": company_id,
                "slug": f"slug-{company_id}",
                "display_name": company_id.title(),
            }
        ).execute()
    db.table("company_members").insert(
        {
            "id": f"cm-{company_id}-{user_id}",
            "company_id": company_id,
            "user_id": user_id,
            "role": role,
        }
    ).execute()
    # Seed a profiles row so require_company's profiles lookup resolves to None
    # (no full_name/first_name/last_name in the test fixture) rather than raising
    # "no such table: profiles". The author fallback in the route uses user_email
    # then user_id, so the empty profile produces the expected "user-test" author.
    existing_profile = (
        db.table("profiles").select("id").eq("id", user_id).execute().data
    )
    if not existing_profile:
        db.table("profiles").insert({"id": user_id}).execute()


def _enable_supabase_bearer(monkeypatch) -> None:
    """Make the already-built app's `require_company` verify a minted HS256 bearer.

    `require_company` → `require_session` → `_decode_supabase_token` reads
    `app.auth.settings.supabase_jwt_secret` at call time. `app.auth.settings` is
    the same Settings object the live dependency closes over (only conftest's
    `isolated_settings` reloads config/auth; no DA suite reloads auth again), so
    patching the attribute on it — rather than reloading config/auth/routes/main —
    is sufficient and reload-free. Same monkeypatch-on-settings pattern the smoke
    suite already uses for storage_dir."""
    import app.auth as auth_mod

    monkeypatch.setattr(
        auth_mod.settings, "supabase_jwt_secret", _TEST_SUPABASE_SECRET, raising=False
    )
    # `tenant_client`-based suites hit real `/v1/projects/...` routes too; the
    # router-level gate 404s them all when unset, so flip it on here — the
    # second of the two independent client-building seams (the other is
    # `setup_supabase_auth` in `_company_helpers.py`).
    monkeypatch.setenv("PROJECTS_ENABLED", "1")


@pytest.fixture
def tenant_client(fake_llm, isolated_settings, monkeypatch):
    """A Supabase-bearer-authed TestClient bound to a seeded company whose slug
    is controllable, for the legacy dataset/id-keyed route suites after the
    tenant-isolation fix (require_session → require_company).

    Returns a SimpleNamespace with:
      - `make(slug, user_id=...)` → seed a company with that slug + membership,
        and return a TestClient already carrying that user's Bearer header. The
        dataset slug used by briefs/prds/asks MUST equal the company slug for the
        ownership chain (dataset slug → company) to resolve to the caller.
      - `bearer(user_id)` → an Authorization header dict for an arbitrary user.

    Composes on `fake_llm`/`isolated_settings` (the same in-memory fake Supabase
    + reloaded app the legacy suites already use), and patches the bearer secret
    onto the live `app.auth.settings` so require_company verifies minted tokens."""
    from types import SimpleNamespace

    import app.main as main_mod
    from app.db.client import require_client

    _enable_supabase_bearer(monkeypatch)

    def _seed(slug: str, user_id: str, company_id: str | None) -> str:
        import uuid as _uuid

        c = require_client()
        existing = c.table("companies").select("id").eq("slug", slug).execute().data
        if existing:
            company_id = existing[0]["id"]
        else:
            company_id = company_id or _uuid.uuid4().hex
            c.table("companies").insert(
                {"id": company_id, "slug": slug, "display_name": slug.title()}
            ).execute()
        c.table("company_members").insert(
            {
                "id": f"cm-{company_id}-{user_id}",
                "company_id": company_id,
                "user_id": user_id,
                "role": "owner",
            }
        ).execute()
        if not c.table("profiles").select("id").eq("id", user_id).execute().data:
            c.table("profiles").insert({"id": user_id}).execute()
        return company_id

    def make(
        slug: str, user_id: str | None = None, company_id: str | None = None
    ) -> SimpleNamespace:
        import uuid as _uuid

        uid = user_id or ("user-" + _uuid.uuid4().hex[:8])
        company_id = _seed(slug, uid, company_id)
        client = TestClient(main_mod.app)
        client.headers["Authorization"] = f"Bearer {_mint_supabase_token(uid)}"
        return SimpleNamespace(
            client=client, company_id=company_id, user_id=uid, slug=slug
        )

    return SimpleNamespace(
        make=make,
        bearer=lambda uid: {"Authorization": f"Bearer {_mint_supabase_token(uid)}"},
    )


@pytest.fixture
def company_client(env, isolated_settings, monkeypatch) -> TestClient:
    """Sync TestClient authed via a Supabase Bearer JWT + a seeded company membership
    (the require_company path). Drop-in replacement for the legacy cookie-login
    `client` fixture across the Class-1 DA route suites: every authed call resolves
    `workspace_id == _TEST_COMPANY_ID`.

    Composes on the suite-local `env` fixture (which reloads the DA module stack and
    builds `env.main.app`); it only patches the bearer secret onto the live settings,
    seeds the membership row, and pre-attaches the Authorization header. The P5-06
    pytest_configure hook already defaults a same-origin `Origin` header, so authed
    mutating routes are not rejected by require_same_origin."""
    _enable_supabase_bearer(monkeypatch)
    _seed_company_membership(isolated_settings["supabase"])
    c = TestClient(env.main.app)
    c.headers["Authorization"] = f"Bearer {_mint_supabase_token()}"
    return c
