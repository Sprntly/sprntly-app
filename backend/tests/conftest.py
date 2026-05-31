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
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests._fake_supabase import FakeSupabaseClient, reset_fake_db


# Modules that import `settings` at top level and therefore need to be
# reloaded after env vars change. Order matters: config first, then its
# consumers, then anything that imports the consumers.
_RELOAD_ORDER = [
    "app.config",
    "app.db.client",
    "app.db.schema",
    "app.db.briefs",
    "app.db.prds",
    "app.db.evidences",
    "app.db.asks",
    "app.db.datasets",
    "app.db.connections",
    "app.db.github",
    "app.db",
    "app.corpus",
    "app.auth",
    "app.llm",
    "app.ingest",
    "app.datasets",
    "app.prompts",
    "app.ask_runner",
    "app.evidence_runner",
    "app.prd_runner",
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
    status           TEXT NOT NULL DEFAULT 'ready',
    error            TEXT,
    template_version INTEGER,
    variant          TEXT NOT NULL DEFAULT 'v1'
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
    variant          TEXT NOT NULL DEFAULT 'v1'
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

CREATE TABLE datasets (
    slug         TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE connections (
    id                   TEXT PRIMARY KEY,
    provider             TEXT NOT NULL UNIQUE,
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

CREATE TABLE github_installations (
    installation_id      INTEGER PRIMARY KEY,
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
