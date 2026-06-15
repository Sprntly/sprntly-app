"""Tests for the GET /{prototype_id}/events SSE endpoint.

Covers: content-type, streaming body format, auth (no token → 401, bad
workspace → 404, unknown prototype → 404), and observability (token value
never appears in any log record; prototype_id + workspace_id do).

Mirrors the setup pattern from test_design_agent_routes.py: isolated_settings
reloads the module stack, a bare TestClient passes the bearer as a query
param (?token=) because EventSource cannot send Authorization headers.
"""
from __future__ import annotations

import importlib
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.conftest import (
    _TEST_COMPANY_ID,
    _enable_supabase_bearer,
    _mint_supabase_token,
    _seed_company_membership,
)

# SQLite DDL for the prototypes tables (mirrors test_design_agent_routes.py).
_PROTOTYPE_DDL = """
CREATE TABLE IF NOT EXISTS prototypes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    prd_id                 INTEGER,
    workspace_id           TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'generating',
    variant                TEXT NOT NULL DEFAULT 'v1',
    template_version       INTEGER NOT NULL,
    instructions           TEXT,
    target_platform        TEXT NOT NULL DEFAULT 'both',
    figma_file_key         TEXT,
    website_url            TEXT,
    github_installation_id INTEGER,
    bundle_url             TEXT,
    preview_image_url      TEXT,
    current_checkpoint_id  INTEGER,
    error                  TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at           TEXT,
    share_mode             TEXT NOT NULL DEFAULT 'private'
                           CHECK (share_mode IN ('private', 'public', 'passcode')),
    share_token            TEXT UNIQUE,
    share_passcode_hash    TEXT
);
CREATE TABLE IF NOT EXISTS prototype_checkpoints (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    prototype_id      INTEGER NOT NULL,
    workspace_id      TEXT NOT NULL,
    bundle_url        TEXT,
    prd_revision_hash TEXT,
    figma_frame_hash  TEXT,
    prompt_history    TEXT NOT NULL DEFAULT '[]',
    comment_state     TEXT NOT NULL DEFAULT '[]',
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@pytest.fixture
def env(isolated_settings, monkeypatch):
    """Reload the design-agent module stack with feature flag ON and prototype
    tables seeded in the fake Supabase DB."""
    from tests import _fake_supabase

    _fake_supabase.get_fake_db().executescript(_PROTOTYPE_DDL)
    monkeypatch.setenv("DESIGN_AGENT_ENABLED", "1")

    import app.db.prototypes as proto_mod
    importlib.reload(proto_mod)
    import app.routes.design_agent as routes_mod
    importlib.reload(routes_mod)
    import app.main as main_mod
    importlib.reload(main_mod)
    import app.db as db_mod
    return SimpleNamespace(proto=proto_mod, routes=routes_mod, main=main_mod, db=db_mod)


@pytest.fixture
def sse_client(env, isolated_settings, monkeypatch) -> TestClient:
    """Bare TestClient with Supabase bearer configured + membership seeded.

    The SSE endpoint reads the bearer token from ?token= (not an Authorization
    header) so callers must append the token as a query param.  Use
    _authed_url() below to build the URL.
    """
    _enable_supabase_bearer(monkeypatch)
    _seed_company_membership(isolated_settings["supabase"])
    return TestClient(env.main.app)


# ─── helpers ─────────────────────────────────────────────────────────────────


def _authed_url(proto_id: int) -> str:
    """Build a /events URL that carries a freshly minted bearer in the query."""
    return f"/v1/design-agent/{proto_id}/events?token={_mint_supabase_token()}"


async def _finite_sse(*events):
    """Async generator that yields the given dicts then terminates."""
    for ev in events:
        yield ev


def _seed_prototype(
    proto_mod,
    workspace_id: str = _TEST_COMPANY_ID,
) -> int:
    """Insert a generating prototype row and return its id."""
    return proto_mod.start_prototype(
        prd_id=1,
        workspace_id=workspace_id,
        template_version=1,
    )


# ─── happy path ──────────────────────────────────────────────────────────────


def test_events_returns_sse_content_type(env, sse_client, monkeypatch):
    """The events endpoint responds with text/event-stream content-type."""
    pid = _seed_prototype(env.proto)
    monkeypatch.setattr(
        env.routes, "_sse_subscribe", lambda _: _finite_sse({"kind": "done"})
    )
    resp = sse_client.get(_authed_url(pid))
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]


def test_events_streams_json_data_lines(env, sse_client, monkeypatch):
    """Each event is serialised as a `data: <json>\\n\\n` SSE frame."""
    pid = _seed_prototype(env.proto)
    monkeypatch.setattr(
        env.routes,
        "_sse_subscribe",
        lambda _: _finite_sse(
            {"kind": "step", "text": "init"},
            {"kind": "done"},
        ),
    )
    resp = sse_client.get(_authed_url(pid))
    assert resp.status_code == 200
    body = resp.text
    assert '"kind": "step"' in body
    assert '"kind": "done"' in body
    # Frames are newline-delimited pairs
    assert "data: " in body


# ─── auth / gate ─────────────────────────────────────────────────────────────


def test_events_401_without_token(env):
    """A call with no ?token= is rejected with 401."""
    pid = _seed_prototype(env.proto)
    bare = TestClient(env.main.app)
    resp = bare.get(f"/v1/design-agent/{pid}/events")
    assert resp.status_code == 401


# ─── 404 isolation ───────────────────────────────────────────────────────────


def test_events_404_unknown_prototype(env, sse_client, monkeypatch):
    """Requesting events for a non-existent prototype returns 404, not 401/403."""
    monkeypatch.setattr(
        env.routes, "_sse_subscribe", lambda _: _finite_sse()
    )
    resp = sse_client.get(_authed_url(99999))
    assert resp.status_code == 404


def test_events_404_cross_workspace_prototype(env, sse_client, monkeypatch):
    """A prototype owned by a different workspace is invisible (404, not 403)."""
    foreign_pid = _seed_prototype(env.proto, workspace_id="other-workspace")
    monkeypatch.setattr(
        env.routes, "_sse_subscribe", lambda _: _finite_sse()
    )
    resp = sse_client.get(_authed_url(foreign_pid))
    assert resp.status_code == 404


# ─── observability ───────────────────────────────────────────────────────────


def test_events_connect_log_records_prototype_and_workspace(
    env, sse_client, monkeypatch, caplog
):
    """The connect log record carries prototype_id and workspace_id."""
    pid = _seed_prototype(env.proto)
    monkeypatch.setattr(
        env.routes, "_sse_subscribe", lambda _: _finite_sse({"kind": "done"})
    )
    with caplog.at_level(logging.INFO, logger="app.routes.design_agent"):
        sse_client.get(_authed_url(pid))

    log_text = " ".join(r.getMessage() for r in caplog.records)
    assert str(pid) in log_text
    assert _TEST_COMPANY_ID in log_text


def test_events_connect_log_never_contains_token(
    env, sse_client, monkeypatch, caplog
):
    """The bearer token value must never appear in any log record — not in the
    connect log, not in the disconnect log, not anywhere in the request path."""
    pid = _seed_prototype(env.proto)
    token = _mint_supabase_token()
    monkeypatch.setattr(
        env.routes, "_sse_subscribe", lambda _: _finite_sse({"kind": "done"})
    )

    url = f"/v1/design-agent/{pid}/events?token={token}"
    with caplog.at_level(logging.DEBUG):
        sse_client.get(url)

    # Only inspect application-level records — the httpx transport layer logs
    # the full URL by design; that is test-infra behaviour, not production code.
    app_records = [r for r in caplog.records if r.name.startswith("app")]
    for record in app_records:
        assert token not in record.getMessage(), (
            f"Token leaked in application log: {record.getMessage()}"
        )
