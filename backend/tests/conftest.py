"""Shared pytest fixtures.

Settings are read into module-level objects (e.g. `from app.config import settings`)
all over the codebase. That means per-test isolation requires reloading every
module that holds a reference to `settings`, not just `app.config` itself.

Each test gets:
- A fresh on-disk SQLite under tmp_path.
- A fresh DATA_DIR under tmp_path.
- A patched app.llm.call_json that returns deterministic payloads instead of
  hitting Anthropic.
- An authenticated FastAPI TestClient with a real session cookie minted via
  the login route.

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


# Modules that import `settings` at top level and therefore need to be
# reloaded after env vars change. Order matters: config first, then its
# consumers, then anything that imports the consumers.
_RELOAD_ORDER = [
    "app.config",
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
    "app.routes.evidence_v2",
    "app.routes.prd",
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
                # If reload fails (e.g. import-time crash), surface the error.
                raise


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
        "sprntly_evidence_v2_template.md",
    ):
        src = repo_root / "data" / name
        if src.exists():
            shutil.copy(src, data_dir / name)
    return data_dir


@pytest.fixture
def isolated_settings(tmp_path: Path, tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_data_dir))
    monkeypatch.setenv("TEMPLATE_DIR", str(tmp_data_dir))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "sprintly.db"))
    monkeypatch.setenv("DEMO_PASSWORD", "test-pw")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-used")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:3000")

    _reload_app_modules()
    import app.db as db_mod
    import app.config as config_mod
    import app.corpus as corpus_mod
    db_mod.init_db()
    yield {
        "config": config_mod,
        "db": db_mod,
        "corpus": corpus_mod,
        "data_dir": tmp_data_dir,
        "db_path": tmp_path / "sprintly.db",
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
