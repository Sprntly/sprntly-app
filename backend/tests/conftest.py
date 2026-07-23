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
    theme_id         TEXT
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
    theme_id         TEXT
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
    -- Phase 2 user-action (mirrors 20260616140000_brief_finding_state_action.sql).
    action              TEXT NOT NULL DEFAULT 'surfaced'
                        CHECK (action IN ('surfaced', 'prd_created', 'dismissed', 'done')),
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
    generated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_prd_tickets_company ON prd_tickets (company_id);

-- Per-PRD tracker sync state (mirrors 20260710120000_prd_ticket_sync.sql).
-- One row per (company, prd): the ClickUp list / Jira project the PRD's
-- tickets sync with, the last sync outcome, and the pulled per-ticket
-- tracker statuses (jsonb → TEXT here).
CREATE TABLE prd_ticket_sync (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id       TEXT NOT NULL,
    workspace_id     TEXT,
    prd_id           INTEGER NOT NULL,
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
    UNIQUE (company_id, prd_id)
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
-- the original file (base64) + extracted text the weekly brief reads + the
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
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_conversations_company ON conversations (company_id, created_at);
CREATE INDEX idx_conversations_company_prd ON conversations (company_id, prd_id, updated_at);

CREATE TABLE conversation_turns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    role            TEXT NOT NULL DEFAULT 'user',
    content         TEXT NOT NULL DEFAULT '',
    -- Extracted attachment texts [{name, content}] persisted with the turn
    -- (20260723170000_conversation_turn_attachments.sql).
    attachments     TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_conv_turns_conv ON conversation_turns (conversation_id, created_at);
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
    except Exception:
        pass
    try:
        scheduler_mod = importlib.import_module("app.scheduler")
        monkeypatch.setattr(scheduler_mod, "kickoff_sync", _noop_sync, raising=False)
    except Exception:
        pass
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
    Both run after this autouse fixture, so their patch wins for that test."""
    try:
        import app.design_agent.screenshot as _screenshot

        def _no_playwright():
            raise ImportError("playwright disabled in tests")

        monkeypatch.setattr(_screenshot, "_resolve_async_playwright", _no_playwright, raising=False)
    except Exception:
        pass
    yield


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
