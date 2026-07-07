"""Tests for BearerAuthMiddleware (mcp_server/middleware.py).

Drives the middleware as a raw ASGI callable with a stub inner app +
stubbed backend resolve_token, asserting:
  - a missing/malformed Authorization header -> 401 (inner app never runs)
  - a token the backend rejects (BackendError) -> 401
  - a valid token -> inner app runs AND sees the right CompanyContext
  - exempt paths (/health) bypass auth entirely
  - CONTEXT ISOLATION: two sequential requests carrying two different
    tokens never leak company context into each other (the concrete
    regression test for the contextvar-based design).
"""
from __future__ import annotations

import pytest

from mcp_server import backend_client, middleware
from mcp_server.auth import _current_company


class _Recorder:
    """A stub ASGI inner app that records the CompanyContext visible to it
    at call time, and emits a trivial 200 for http requests."""

    def __init__(self):
        self.seen: list = []
        self.called = 0

    async def __call__(self, scope, receive, send):
        self.called += 1
        self.seen.append(_current_company.get())
        if scope["type"] == "http":
            await send(
                {"type": "http.response.start", "status": 200, "headers": []}
            )
            await send({"type": "http.response.body", "body": b"ok"})


def _http_scope(path="/mcp", auth_header: bytes | None = None):
    headers = []
    if auth_header is not None:
        headers.append((b"authorization", auth_header))
    return {"type": "http", "path": path, "headers": headers}


async def _drive(app, scope):
    """Run one ASGI request through `app`, collecting the response start."""
    sent: list[dict] = []

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def _send(msg):
        sent.append(msg)

    await app(scope, _receive, _send)
    return sent


def _status(sent: list[dict]) -> int:
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def _patch_resolve(monkeypatch, mapping: dict):
    """Stub backend_client.resolve_token: map raw token -> resolved dict, or
    raise BackendError for anything not in the mapping."""

    async def _fake_resolve(token: str):
        if token not in mapping:
            raise backend_client.BackendError(401, "invalid")
        return mapping[token]

    monkeypatch.setattr(middleware, "resolve_token", _fake_resolve)


@pytest.mark.asyncio
async def test_missing_bearer_returns_401(monkeypatch):
    inner = _Recorder()
    _patch_resolve(monkeypatch, {})
    app = middleware.BearerAuthMiddleware(inner)

    sent = await _drive(app, _http_scope(auth_header=None))
    assert _status(sent) == 401
    assert inner.called == 0


@pytest.mark.asyncio
async def test_malformed_authorization_returns_401(monkeypatch):
    inner = _Recorder()
    _patch_resolve(monkeypatch, {})
    app = middleware.BearerAuthMiddleware(inner)

    sent = await _drive(app, _http_scope(auth_header=b"Basic abc"))
    assert _status(sent) == 401
    assert inner.called == 0


@pytest.mark.asyncio
async def test_invalid_token_returns_401(monkeypatch):
    inner = _Recorder()
    _patch_resolve(monkeypatch, {})  # every token rejected
    app = middleware.BearerAuthMiddleware(inner)

    sent = await _drive(app, _http_scope(auth_header=b"Bearer nope"))
    assert _status(sent) == 401
    assert inner.called == 0


@pytest.mark.asyncio
async def test_valid_token_runs_inner_with_context(monkeypatch):
    inner = _Recorder()
    _patch_resolve(
        monkeypatch,
        {"tok-a": {"company_id": "co-a", "user_id": "u-a", "role": "owner"}},
    )
    app = middleware.BearerAuthMiddleware(inner)

    sent = await _drive(app, _http_scope(auth_header=b"Bearer tok-a"))
    assert _status(sent) == 200
    assert inner.called == 1
    assert inner.seen[0].company_id == "co-a"

    # Context is reset after the request — it must not leak past the call.
    assert _current_company.get() is None


@pytest.mark.asyncio
async def test_exempt_path_bypasses_auth(monkeypatch):
    inner = _Recorder()
    _patch_resolve(monkeypatch, {})
    app = middleware.BearerAuthMiddleware(inner, exempt_paths=frozenset({"/health"}))

    sent = await _drive(app, _http_scope(path="/health", auth_header=None))
    assert _status(sent) == 200
    assert inner.called == 1
    # No token resolved, so no company context is set for an exempt path.
    assert inner.seen[0] is None


@pytest.mark.asyncio
async def test_two_tokens_do_not_leak_context(monkeypatch):
    """Two sequential requests with two different tokens must each see their
    OWN company — the concrete regression guard for the contextvar design."""
    inner = _Recorder()
    _patch_resolve(
        monkeypatch,
        {
            "tok-a": {"company_id": "co-a", "user_id": "u-a", "role": "owner"},
            "tok-b": {"company_id": "co-b", "user_id": "u-b", "role": "member"},
        },
    )
    app = middleware.BearerAuthMiddleware(inner)

    await _drive(app, _http_scope(auth_header=b"Bearer tok-a"))
    await _drive(app, _http_scope(auth_header=b"Bearer tok-b"))

    assert inner.seen[0].company_id == "co-a"
    assert inner.seen[1].company_id == "co-b"
    assert _current_company.get() is None
