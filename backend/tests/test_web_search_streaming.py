"""call_with_web_search must stream on the long read timeout.

A search-heavy request (server-side web_search runs up to max_searches
sweeps before composing) routinely outlives the default non-streaming read
timeout — the public-feedback capture pass hit exactly that httpx.ReadTimeout
on staging. Streaming is the SDK's required pattern for slow requests; these
tests pin that the call goes through the streaming path with the long timeout.
"""
from __future__ import annotations

from types import SimpleNamespace

import app.llm as llm


def test_web_search_streams_on_long_timeout(monkeypatch):
    seen: dict = {}

    def fake_create(client, *, stream=False, background=False, on_delta=None, **kwargs):
        seen["stream"] = stream
        seen["timeout"] = kwargs.get("timeout")
        seen["tools"] = kwargs.get("tools")
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="found things")],
            usage=None, stop_reason="end_turn",
        )

    monkeypatch.setattr(llm, "_create_with_retries", fake_create)
    monkeypatch.setattr(llm, "get_client", lambda: object())

    meta: dict = {}
    out = llm.call_with_web_search(
        system="sys", user="u", max_searches=12, meta_out=meta,
    )
    assert out == "found things"
    assert seen["stream"] is True
    assert seen["timeout"] == llm.LONG_REQUEST_TIMEOUT_S
    assert seen["tools"][0]["max_uses"] == 12
    assert meta["stop_reason"] == "end_turn"
