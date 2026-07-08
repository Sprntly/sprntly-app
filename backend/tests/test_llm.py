"""Tests for app.llm.call_json — the thin Anthropic SDK wrapper.

We never hit the network. Instead we patch `app.llm.get_client()` to return
a stub whose `messages.create(...)` returns canned response blocks shaped
like the real SDK's content-block list.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import llm


def _text_block(text: str):
    """Fake an Anthropic SDK TextBlock — type='text' + .text attribute."""
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(name: str, input_dict: dict):
    return SimpleNamespace(type="tool_use", name=name, input=input_dict)


class _StubClient:
    """Records every messages.create call; returns a configurable response."""

    def __init__(self, content):
        self._content = content
        self.calls: list[dict] = []

        # `client.messages.create(...)` shape
        outer = self

        class _Messages:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                return SimpleNamespace(content=outer._content)

        self.messages = _Messages()


@pytest.fixture
def stub_client(monkeypatch):
    """Patch llm.get_client to return a stub we control per test."""
    holder = {"client": None}

    def _factory(content):
        holder["client"] = _StubClient(content)
        monkeypatch.setattr(llm, "get_client", lambda: holder["client"])
        return holder["client"]

    return _factory


# ---- text-mode call_json (no schema) ----------------------------------------

def test_call_json_parses_plain_json_text(stub_client):
    client = stub_client([_text_block('{"a": 1, "b": "two"}')])
    out = llm.call_json(system="sys", user="usr")
    assert out == {"a": 1, "b": "two"}
    assert len(client.calls) == 1


def test_call_json_strips_markdown_code_fence(stub_client):
    """The model sometimes wraps JSON in ```json … ``` — call_json tolerates it."""
    stub_client([_text_block('```json\n{"x": 42}\n```')])
    out = llm.call_json(system="sys", user="usr")
    assert out == {"x": 42}


def test_call_json_raises_on_non_json_content(stub_client):
    stub_client([_text_block("definitely not json")])
    with pytest.raises(HTTPException) as excinfo:
        llm.call_json(system="sys", user="usr")
    assert excinfo.value.status_code == 502


def test_call_json_passes_through_system_and_user_prompts(stub_client):
    client = stub_client([_text_block('{"ok": true}')])
    llm.call_json(system="SYS-PROMPT", user="USR-PROMPT")
    kwargs = client.calls[0]
    assert kwargs["system"] == "SYS-PROMPT"
    # Default (no cacheable prefix) shape: content is just the user string.
    assert kwargs["messages"][0]["content"] == "USR-PROMPT"


def test_call_json_max_tokens_threads_through(stub_client):
    client = stub_client([_text_block('{"ok": 1}')])
    llm.call_json(system="s", user="u", max_tokens=1234)
    assert client.calls[0]["max_tokens"] == 1234


def test_call_json_default_model_is_sent(stub_client):
    client = stub_client([_text_block('{"ok": 1}')])
    llm.call_json(system="s", user="u")
    assert client.calls[0]["model"] == llm.DEFAULT_MODEL


# ---- schema-mode (tool-use) call_json ---------------------------------------

def test_call_json_with_schema_returns_tool_input_dict(stub_client):
    """When `schema` is given, the SDK runs in tool-use mode and the dict
    we return comes from the tool_use block's `.input`."""
    stub_client([_tool_use_block("submit_response", {"answer": "yes"})])
    out = llm.call_json(
        system="s",
        user="u",
        schema={"type": "object", "properties": {}, "required": []},
    )
    assert out == {"answer": "yes"}


def test_call_json_with_schema_raises_when_tool_not_invoked(stub_client):
    """If the model returns text instead of invoking the tool, 502."""
    stub_client([_text_block("no tool here")])
    with pytest.raises(HTTPException) as excinfo:
        llm.call_json(
            system="s",
            user="u",
            schema={"type": "object", "properties": {}, "required": []},
        )
    assert excinfo.value.status_code == 502


def test_call_json_with_schema_sends_tool_choice(stub_client):
    """Tool-use mode must force `tool_choice` so Claude is required to call the tool."""
    client = stub_client([_tool_use_block("submit_response", {"x": 1})])
    llm.call_json(
        system="s",
        user="u",
        schema={"type": "object", "properties": {}, "required": []},
    )
    kwargs = client.calls[0]
    assert kwargs["tool_choice"] == {"type": "tool", "name": "submit_response"}
    assert kwargs["tools"][0]["name"] == "submit_response"


# ---- cacheable-prefix shape -------------------------------------------------

def test_call_json_cacheable_prefix_builds_content_list(stub_client):
    """When a cacheable prefix is supplied, content becomes a list of text
    blocks (not a plain string), and the prefix block carries cache_control."""
    client = stub_client([_text_block('{"x": 1}')])
    llm.call_json(
        system="short-sys",
        user="user-tail",
        user_cacheable_prefix="big-corpus",
    )
    kwargs = client.calls[0]
    content = kwargs["messages"][0]["content"]
    assert isinstance(content, list)
    assert content[0]["text"] == "big-corpus"
    assert content[0].get("cache_control") == {"type": "ephemeral"}
    assert content[1]["text"] == "user-tail"


# ---- get_client ANTHROPIC_API_KEY guard -------------------------------------

def test_get_client_raises_without_api_key(monkeypatch, isolated_settings):
    """If the key is unset, get_client must 500 instead of constructing a
    broken Anthropic() — that error is much harder to debug downstream."""
    monkeypatch.setattr(llm, "_client", None)
    monkeypatch.setattr(
        isolated_settings["config"].settings, "anthropic_api_key", ""
    )
    # llm.settings is imported at module-load — patch the live reference too.
    monkeypatch.setattr(llm.settings, "anthropic_api_key", "")
    with pytest.raises(HTTPException) as excinfo:
        llm.get_client()
    assert excinfo.value.status_code == 500


# ---- _is_retryable: transport-layer retry classification --------------------

def test_llm_is_retryable_unaffected():
    """`app.design_agent.provider_errors.is_retryable` (a safe-taxonomy alias
    with zero production callers) was removed as dead code. This module's own
    `_is_retryable` is a separate function — different signature, different
    layer — and must classify transient failures exactly as before."""
    import httpx
    import anthropic

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    assert llm._is_retryable(anthropic.APIConnectionError(request=request)) is True
    assert llm._is_retryable(anthropic.APITimeoutError(request=request)) is True

    rate_limited = anthropic.APIStatusError(
        "rate limited",
        response=httpx.Response(status_code=429, request=request),
        body=None,
    )
    assert llm._is_retryable(rate_limited) is True

    server_error = anthropic.APIStatusError(
        "service unavailable",
        response=httpx.Response(status_code=503, request=request),
        body=None,
    )
    assert llm._is_retryable(server_error) is True

    bad_request = anthropic.APIStatusError(
        "bad request",
        response=httpx.Response(status_code=400, request=request),
        body=None,
    )
    assert llm._is_retryable(bad_request) is False
    assert llm._is_retryable(ValueError("not a provider error")) is False


# ── strip_code_fence: unwrap a Markdown code fence from model output ──────────

class TestStripCodeFence:
    def test_strips_html_fence(self):
        out = llm.strip_code_fence("```html\n<!DOCTYPE html>\n<div>x</div>\n```")
        assert out == "<!DOCTYPE html>\n<div>x</div>"
        assert "```" not in out

    def test_strips_fence_without_language(self):
        assert llm.strip_code_fence("```\nhello\n```") == "hello"

    def test_leaves_unfenced_text_unchanged(self):
        html = '<div class="wrap"><h1>x</h1></div>'
        assert llm.strip_code_fence(html) == html

    def test_does_not_strip_inner_fences(self):
        # A fence that only opens mid-document (not wrapping the whole thing) is
        # left alone — we only unwrap a single fence around the entire payload.
        s = "<div>before</div>\n```\ncode\n```\n<div>after</div>"
        assert llm.strip_code_fence(s) == s

    def test_tolerates_surrounding_whitespace(self):
        assert llm.strip_code_fence("\n  ```html\n<p>hi</p>\n```  \n") == "<p>hi</p>"
