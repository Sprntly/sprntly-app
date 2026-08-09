"""Tests for app.middleware_timing.RequestTimingMiddleware.

The load-bearing assertion here is the ROUTE TEMPLATE one. The whole reason
this middleware exists alongside nginx's `$upstream_response_time` is that
nginx can only log the concrete path, so `/v1/thing/1` and `/v1/thing/2` never
aggregate. If `scope["route"]` ever stops being populated by Starlette, this
middleware silently degrades to logging concrete paths and becomes redundant —
`test_logs_route_template_not_concrete_path` is what catches that.
"""
from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from starlette.testclient import TestClient

from app.middleware_timing import RequestTimingMiddleware


def _parse(record_message: str) -> dict[str, str]:
    """`request method=GET route=/x status=200 duration_ms=3` -> dict."""
    parts = record_message.split()
    assert parts[0] == "request"
    return dict(p.split("=", 1) for p in parts[1:])


def _build(slow_ms: int = 3000) -> FastAPI:
    app = FastAPI()

    @app.get("/v1/thing/{thing_id}")
    def _thing(thing_id: int):
        return {"id": thing_id}

    @app.get("/v1/slow")
    def _slow():
        return {"ok": True}

    @app.get("/v1/boom")
    def _boom():
        raise RuntimeError("kaboom")

    @app.get("/v1/prd/{prd_id}/stream")
    def _stream(prd_id: int):
        return StreamingResponse(iter([b"a"]), media_type="text/event-stream")

    app.add_middleware(RequestTimingMiddleware, slow_ms=slow_ms)
    return app


def _records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records
            if r.name == "app.middleware_timing" and r.getMessage().startswith("request ")]


def test_logs_route_template_not_concrete_path(caplog):
    """The point of the middleware: ids collapse into one bucket."""
    with caplog.at_level(logging.DEBUG, logger="app.middleware_timing"):
        client = TestClient(_build())
        client.get("/v1/thing/1")
        client.get("/v1/thing/2")

    fields = [_parse(r.getMessage()) for r in _records(caplog)]
    assert [f["route"] for f in fields] == ["/v1/thing/{thing_id}", "/v1/thing/{thing_id}"]
    # ...and specifically NOT the concrete paths, which is nginx's failure mode.
    assert "/v1/thing/1" not in [f["route"] for f in fields]


def test_records_method_and_status(caplog):
    with caplog.at_level(logging.DEBUG, logger="app.middleware_timing"):
        TestClient(_build()).get("/v1/thing/7")
    f = _parse(_records(caplog)[0].getMessage())
    assert f["method"] == "GET"
    assert f["status"] == "200"
    assert int(f["duration_ms"]) >= 0


def test_slow_request_logs_at_warning(caplog):
    """slow_ms=0 forces every request over the threshold."""
    with caplog.at_level(logging.DEBUG, logger="app.middleware_timing"):
        TestClient(_build(slow_ms=0)).get("/v1/slow")
    assert _records(caplog)[0].levelno == logging.WARNING


def test_fast_request_logs_at_info(caplog):
    with caplog.at_level(logging.DEBUG, logger="app.middleware_timing"):
        TestClient(_build(slow_ms=10_000)).get("/v1/slow")
    assert _records(caplog)[0].levelno == logging.INFO


def test_streaming_route_is_debug_even_when_slow(caplog):
    """An SSE subscription's duration measures how long the client watched.

    Logging it as a slow request would put a 10-minute PRD stream in the same
    bucket as a slow query and wreck any p95 built from these lines.
    """
    with caplog.at_level(logging.DEBUG, logger="app.middleware_timing"):
        TestClient(_build(slow_ms=0)).get("/v1/prd/5/stream")
    rec = _records(caplog)[0]
    assert rec.levelno == logging.DEBUG
    assert _parse(rec.getMessage())["route"] == "/v1/prd/{prd_id}/stream"


def test_raising_endpoint_is_still_timed(caplog):
    """The endpoint that blows up after 30s is the one worth seeing."""
    with caplog.at_level(logging.DEBUG, logger="app.middleware_timing"):
        client = TestClient(_build(), raise_server_exceptions=False)
        client.get("/v1/boom")
    f = _parse(_records(caplog)[0].getMessage())
    assert f["route"] == "/v1/boom"
    assert int(f["duration_ms"]) >= 0


def test_unrouted_path_falls_back_to_concrete_path(caplog):
    """A 404 matches no route, so there IS no template — log what was asked for."""
    with caplog.at_level(logging.DEBUG, logger="app.middleware_timing"):
        TestClient(_build()).get("/v1/nope")
    f = _parse(_records(caplog)[0].getMessage())
    assert f["route"] == "/v1/nope"
    assert f["status"] == "404"


def test_query_string_is_never_logged(caplog):
    """EventSource routes carry auth tokens in ?token= — they must not leak."""
    with caplog.at_level(logging.DEBUG, logger="app.middleware_timing"):
        TestClient(_build()).get("/v1/thing/3?token=supersecret&x=1")
    msg = _records(caplog)[0].getMessage()
    assert "supersecret" not in msg
    assert "token" not in msg


@pytest.mark.anyio
async def test_non_http_scope_passes_through():
    """A websocket/lifespan scope must be forwarded untouched, not timed."""
    seen = {}

    async def inner(scope, receive, send):
        seen["type"] = scope["type"]

    mw = RequestTimingMiddleware(inner)
    await mw({"type": "lifespan"}, None, None)
    assert seen["type"] == "lifespan"


@pytest.fixture
def anyio_backend():
    return "asyncio"
