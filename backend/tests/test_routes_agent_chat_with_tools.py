"""Tests for POST /v1/agent/chat-with-tools (C2 of the agent-tools-github slice).

Parallel endpoint that runs an Anthropic tool-use loop:
    user msg → model returns tool_use blocks → backend runs the tools
    → tool_result blocks fed back → loop until end_turn or max iters.

The home page chat (`ask_runner.py`) is NOT touched by this slice —
this is an isolated, opt-in surface so the new architecture can be
validated without risking the existing one-shot chat path.

All Anthropic + GitHub calls are mocked.
"""
from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import app.auth  # noqa: F401

from tests._company_helpers import company_client


# ─────────────────────── tenant-isolation guard setup ───────────────────────
#
# /v1/agent/chat-with-tools now requires the supplied installation_id to
# belong to the caller's company (PR #230 closed the same path on
# connector routes; this surface was the last open install-id gap). The
# tests below aren't about the guard itself — they're about the tool-use
# loop — so this helper seeds the bare minimum (a github_installations
# row bound to the company) so the guard admits the call.


def _bind_install(company_id: str, installation_id: int) -> None:
    """Bind `installation_id` to `company_id` in github_installations so
    the chat-with-tools ownership check admits the call. Idempotent."""
    from app import db

    db.upsert_github_installation(
        installation_id=installation_id,
        account_id=installation_id,
        account_login="acme",
        account_type="Organization",
        company_id=company_id,
    )


# ─────────────────────── helpers ───────────────────────


def _content_text(text: str):
    """Anthropic-shaped text block (object with .type/.text — the loop
    iterates via attribute access, not subscripting)."""
    return SimpleNamespace(type="text", text=text)


def _content_tool_use(tool_use_id: str, name: str, input_args: dict):
    return SimpleNamespace(
        type="tool_use", id=tool_use_id, name=name, input=input_args
    )


def _resp(stop_reason: str, content_blocks: list):
    """Mock Anthropic Messages response shape."""
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=content_blocks,
        usage=SimpleNamespace(input_tokens=0, output_tokens=0),
    )


def _install_anthropic_mock(monkeypatch, responses: list):
    """Replace `app.routes.agent_chat.get_llm_client()` so messages.create
    returns the given sequence of responses (one per loop iteration)."""
    seq = iter(responses)

    def _create(**kwargs):
        return next(seq)

    fake_client = SimpleNamespace(messages=SimpleNamespace(create=_create))

    import app.routes.agent_chat as mod

    monkeypatch.setattr(mod, "get_llm_client", lambda: fake_client)
    return fake_client


# ─────────────────────── happy: no tools needed ───────────────────────


def test_chat_responds_without_calling_tools(isolated_settings, monkeypatch):
    """User asks something the model can answer with no GitHub lookup."""
    ctx = company_client(monkeypatch)
    _bind_install(ctx.company_id, 1)
    _install_anthropic_mock(
        monkeypatch,
        [_resp("end_turn", [_content_text("Hi! I'm the Sprntly agent.")])],
    )

    r = ctx.client.post(
        "/v1/agent/chat-with-tools",
        json={"message": "hi", "installation_id": 1},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["response"] == "Hi! I'm the Sprntly agent."
    assert body["iterations"] == 1
    assert body["tool_calls"] == []


# ─────────────────────── happy: one tool call then answer ───────────────────────


def test_chat_dispatches_tool_then_synthesises_answer(
    isolated_settings, monkeypatch
):
    """Model returns tool_use → backend dispatches → model returns text."""
    ctx = company_client(monkeypatch)
    _bind_install(ctx.company_id, 42)

    # Patch the github tool dispatch so we don't hit the network.
    import app.agent_tools.registry as reg

    def _fake_dispatch(name, args, *, installation_id):
        assert name == "github_get_file"
        assert installation_id == 42
        return {"path": args["path"], "content": "fake file body", "sha": "abc"}

    monkeypatch.setattr(reg, "dispatch", _fake_dispatch)

    _install_anthropic_mock(
        monkeypatch,
        [
            _resp(
                "tool_use",
                [
                    _content_tool_use(
                        "tu_1",
                        "github_get_file",
                        {"repo": "a/b", "path": "README.md"},
                    )
                ],
            ),
            _resp(
                "end_turn",
                [_content_text("The README says: fake file body.")],
            ),
        ],
    )

    r = ctx.client.post(
        "/v1/agent/chat-with-tools",
        json={"message": "what's in the readme?", "installation_id": 42},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "fake file body" in body["response"]
    assert body["iterations"] == 2
    assert body["tool_calls"] == ["github_get_file"]


def test_chat_passes_installation_id_to_dispatch(isolated_settings, monkeypatch):
    """The installation_id from the request body must flow into dispatch."""
    ctx = company_client(monkeypatch)
    _bind_install(ctx.company_id, 7777)
    import app.agent_tools.registry as reg

    captured = {}

    def _fake_dispatch(name, args, *, installation_id):
        captured["installation_id"] = installation_id
        return {"ok": True}

    monkeypatch.setattr(reg, "dispatch", _fake_dispatch)

    _install_anthropic_mock(
        monkeypatch,
        [
            _resp(
                "tool_use",
                [_content_tool_use("t1", "github_get_file", {"repo": "x/y", "path": "z"})],
            ),
            _resp("end_turn", [_content_text("done")]),
        ],
    )

    ctx.client.post(
        "/v1/agent/chat-with-tools",
        json={"message": "...", "installation_id": 7777},
    )
    assert captured["installation_id"] == 7777


# ─────────────────────── max-iterations cap ───────────────────────


def test_chat_caps_tool_iterations(isolated_settings, monkeypatch):
    """If the model keeps requesting tools, the loop must bail at the
    configured limit instead of running forever."""
    ctx = company_client(monkeypatch)
    _bind_install(ctx.company_id, 1)
    import app.agent_tools.registry as reg

    monkeypatch.setattr(
        reg, "dispatch", lambda *a, **kw: {"ok": True}
    )

    # Model returns tool_use every time → we never reach end_turn.
    # The endpoint should cap iterations and return what we have.
    endless = [
        _resp(
            "tool_use",
            [_content_tool_use(f"t{i}", "github_get_file", {"repo": "a/b", "path": "x"})],
        )
        for i in range(30)
    ]
    _install_anthropic_mock(monkeypatch, endless)

    r = ctx.client.post(
        "/v1/agent/chat-with-tools",
        json={"message": "loop forever", "installation_id": 1},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["iterations"] <= 8  # cap defined in routes/agent_chat.py
    assert body["truncated"] is True


# ─────────────────────── tool error is returned as a tool_result ───────────────────────


def test_chat_returns_tool_errors_to_model_gracefully(
    isolated_settings, monkeypatch
):
    """When a tool raises, the loop should still continue — feed the
    error back as a tool_result so the model can recover."""
    ctx = company_client(monkeypatch)
    _bind_install(ctx.company_id, 1)
    import app.agent_tools.registry as reg

    def _raises(name, args, *, installation_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(reg, "dispatch", _raises)

    _install_anthropic_mock(
        monkeypatch,
        [
            _resp(
                "tool_use",
                [_content_tool_use("t1", "github_get_file", {"repo": "x/y", "path": "z"})],
            ),
            _resp(
                "end_turn",
                [_content_text("Sorry, I couldn't read that file.")],
            ),
        ],
    )

    r = ctx.client.post(
        "/v1/agent/chat-with-tools",
        json={"message": "what's in z?", "installation_id": 1},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "couldn't" in body["response"].lower() or "could not" in body["response"].lower()
    assert body["iterations"] == 2


# ─────────────────────── validation + auth ───────────────────────


def test_chat_requires_message(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/agent/chat-with-tools", json={"installation_id": 1}
    )
    assert r.status_code == 422


def test_chat_requires_installation_id(isolated_settings, monkeypatch):
    ctx = company_client(monkeypatch)
    r = ctx.client.post(
        "/v1/agent/chat-with-tools", json={"message": "hi"}
    )
    assert r.status_code == 422


def test_chat_requires_auth(isolated_settings, monkeypatch):
    company_client(monkeypatch)
    from fastapi.testclient import TestClient
    import app.main as main_mod

    unauth = TestClient(main_mod.app)
    r = unauth.post(
        "/v1/agent/chat-with-tools",
        json={"message": "hi", "installation_id": 1},
    )
    assert r.status_code == 401


def test_chat_requires_company(isolated_settings, monkeypatch):
    from tests._company_helpers import setup_supabase_auth, supabase_bearer
    import importlib
    import sys
    import uuid

    setup_supabase_auth(monkeypatch)
    importlib.reload(sys.modules["app.main"])
    from fastapi.testclient import TestClient
    import app.main as main_mod

    orphan = "orphan-" + uuid.uuid4().hex[:8]
    client = TestClient(main_mod.app, headers=supabase_bearer(orphan))
    r = client.post(
        "/v1/agent/chat-with-tools",
        json={"message": "hi", "installation_id": 1},
    )
    assert r.status_code == 403
