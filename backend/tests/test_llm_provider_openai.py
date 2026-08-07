"""OpenAI as a second LLM provider — resolution, the Anthropic-shaped adapter,
the admin routes, and metering.

The load-bearing claim of this feature is that NO call site changes: a company
flips `llm_provider` to 'openai' and every runner, prompt and retry path keeps
working because `app.openai_client.OpenAIMessagesClient` presents the Anthropic
Messages surface. So most of what is tested here is the TRANSLATION — the places
where the two APIs disagree and a wrong mapping would be silent:

  * an `sk-ant-` key must never be sent to api.openai.com (provider and key are
    resolved together, never separately)
  * `temperature` must be dropped for GPT-5 models, which 400 on any value but
    the default — this repo passes temperature=0.2 at several call sites
  * OpenAI's `prompt_tokens` INCLUDES cached tokens; Anthropic's `input_tokens`
    excludes them, so copying it straight across would bill cache hits at ~10x
  * `max_tokens` must gain reasoning headroom, or a long generation returns an
    empty string with finish_reason='length'
"""
from __future__ import annotations

import contextlib
import json

import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def fernet_key(monkeypatch):
    import app.connectors.tokens as tokens_mod

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(tokens_mod.settings, "token_encryption_key", key)
    return key


@contextlib.contextmanager
def _bind(company_id: str):
    import app.llm_keys as llm_keys

    llm_keys.invalidate(company_id)
    token = llm_keys._current_company_id.set(company_id)
    try:
        yield
    finally:
        llm_keys._current_company_id.reset(token)
        llm_keys.invalidate(company_id)


def _stub_config(monkeypatch, **fields):
    import app.db.companies as companies_mod
    from app.db.companies import CompanyLLMConfig

    monkeypatch.setattr(
        companies_mod,
        "get_company_llm_config",
        lambda _cid: CompanyLLMConfig(**fields),
    )


# ── resolution: provider and key are ONE decision ────────────────────────────


def test_openai_company_key_wins(isolated_settings, monkeypatch, fernet_key):
    from app.connectors.tokens import encrypt_token_json
    from app.llm_keys import resolve_llm_client_config

    _stub_config(
        monkeypatch,
        provider="openai",
        openai_cipher=encrypt_token_json("sk-proj-COMPANY"),
        onboarding_complete=True,
    )
    with _bind("co-1"):
        assert resolve_llm_client_config(
            anthropic_platform_key="sk-ant-platform",
            openai_platform_key="sk-platform",
        ) == ("openai", "sk-proj-COMPANY", "customer")


def test_openai_without_a_key_falls_back_to_the_openai_platform_key(
    isolated_settings, monkeypatch
):
    """A keyless OpenAI workspace runs on OUR OpenAI key — not on the Anthropic
    one, and not on a failure. Same rule keyless Claude workspaces run under."""
    from app.llm_keys import resolve_llm_client_config

    _stub_config(monkeypatch, provider="openai", onboarding_complete=True)
    with _bind("co-1"):
        assert resolve_llm_client_config(
            anthropic_platform_key="sk-ant-platform",
            openai_platform_key="sk-platform",
        ) == ("openai", "sk-platform", "platform")


def test_a_claude_key_is_never_handed_to_the_openai_client(
    isolated_settings, monkeypatch, fernet_key
):
    """The failure this whole resolver shape exists to prevent.

    A company can hold BOTH keys. Pointed at OpenAI, it must get its OpenAI key
    or OUR OpenAI key — never the `sk-ant-` secret it also has stored, which
    would be transmitted to a third party that has no business seeing it.
    """
    from app.connectors.tokens import encrypt_token_json
    from app.llm_keys import resolve_llm_client_config

    _stub_config(
        monkeypatch,
        provider="openai",
        anthropic_cipher=encrypt_token_json("sk-ant-SECRET"),
        openai_cipher=None,
        onboarding_complete=True,
    )
    with _bind("co-1"):
        provider, key, _mode = resolve_llm_client_config(
            anthropic_platform_key="sk-ant-platform",
            openai_platform_key="sk-platform",
        )
    assert provider == "openai"
    assert key == "sk-platform"
    assert key != "sk-ant-SECRET"


def test_anthropic_resolver_refuses_to_answer_for_an_openai_company(
    isolated_settings, monkeypatch, fernet_key
):
    """The legacy Anthropic-only entry point must not return an OpenAI key."""
    from app.connectors.tokens import encrypt_token_json
    from app.llm_keys import resolve_llm_api_key_with_mode

    _stub_config(
        monkeypatch,
        provider="openai",
        openai_cipher=encrypt_token_json("sk-proj-COMPANY"),
        onboarding_complete=True,
    )
    with _bind("co-1"):
        assert resolve_llm_api_key_with_mode("sk-ant-platform") == (
            "sk-ant-platform",
            "platform",
        )


def test_unbound_still_resolves_to_anthropic(isolated_settings):
    from app.llm_keys import current_provider, resolve_llm_client_config

    assert current_provider() == "anthropic"
    assert resolve_llm_client_config(anthropic_platform_key="sk-ant-platform") == (
        "anthropic",
        "sk-ant-platform",
        "platform",
    )


# ── the factories build the right client ─────────────────────────────────────


def test_all_three_factories_return_an_openai_client(
    isolated_settings, monkeypatch, fernet_key
):
    import app.design_agent.client as da_client
    import app.llm as llm
    import app.routes.agent_chat as agent_chat
    from app.connectors.tokens import encrypt_token_json
    from app.openai_client import OpenAIMessagesClient

    for mod in (llm, da_client, agent_chat):
        monkeypatch.setattr(mod.settings, "anthropic_api_key", "sk-ant-platform")
        monkeypatch.setattr(mod.settings, "openai_api_key", "sk-platform")
    monkeypatch.setattr(da_client.settings, "design_agent_anthropic_api_key", "sk-ant-design")

    _stub_config(
        monkeypatch,
        provider="openai",
        openai_cipher=encrypt_token_json("sk-proj-COMPANY"),
        onboarding_complete=True,
    )
    with _bind("co-1"):
        for client in (
            llm.get_client(),
            da_client.get_design_agent_client(),
            agent_chat.get_llm_client(),
        ):
            assert isinstance(client, OpenAIMessagesClient)
            assert client.api_key == "sk-proj-COMPANY"


def test_anthropic_company_still_gets_an_anthropic_client(
    isolated_settings, monkeypatch, fernet_key
):
    """The default path is untouched — no company moves provider by accident."""
    import app.llm as llm
    from anthropic import Anthropic
    from app.connectors.tokens import encrypt_token_json

    monkeypatch.setattr(llm.settings, "anthropic_api_key", "sk-ant-platform")
    _stub_config(
        monkeypatch,
        anthropic_cipher=encrypt_token_json("sk-ant-COMPANY"),
        onboarding_complete=True,
    )
    with _bind("co-1"):
        client = llm.get_client()
    assert isinstance(client, Anthropic)
    assert client.api_key == "sk-ant-COMPANY"


# ── model tier mapping ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "claude_model,expected",
    [
        ("claude-haiku-4-5", "gpt-5.6-luna"),
        ("claude-haiku-4-5-20251001", "gpt-5.6-luna"),
        ("claude-sonnet-4-6", "gpt-5.6-terra"),
        ("claude-sonnet-4-7", "gpt-5.6-terra"),
        ("claude-opus-4-7", "gpt-5.6-sol"),
        # An unmapped Claude id still lands on a tier rather than 404-ing.
        ("claude-opus-9-9", "gpt-5.6-sol"),
        ("claude-something-new", "gpt-5.6-terra"),
        # Already an OpenAI id — untouched.
        ("gpt-5.6-sol", "gpt-5.6-sol"),
    ],
)
def test_model_ids_map_to_the_same_tier(claude_model, expected):
    from app.openai_client import map_model

    assert map_model(claude_model) == expected


def test_every_mapped_model_is_priced():
    """An unpriced model records tokens but a null cost, which shows on the
    dashboard as a gap. Every model this adapter can select must be in the
    table."""
    from app.llm_telemetry import MODEL_PRICING
    from app.openai_client import (
        CHEAP_MODEL,
        DEFAULT_MODEL,
        FLAGSHIP_MODEL,
        _SEARCH_MODEL,
    )

    for model in (FLAGSHIP_MODEL, DEFAULT_MODEL, CHEAP_MODEL, _SEARCH_MODEL):
        assert model in MODEL_PRICING, model


# ── request translation ──────────────────────────────────────────────────────


def _payload(**kwargs):
    from app.openai_client import _build_payload

    payload, _timeout = _build_payload(kwargs)
    return payload


def test_system_prompt_becomes_a_system_message():
    payload = _payload(
        model="claude-sonnet-4-6",
        max_tokens=100,
        system="You are a PM.",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert payload["messages"][0] == {"role": "system", "content": "You are a PM."}
    assert payload["messages"][1] == {"role": "user", "content": "hi"}


def test_cache_control_blocks_are_flattened_not_forwarded():
    """`_build_base_kwargs` emits cache_control blocks for Anthropic's opt-in
    prompt caching. OpenAI caches automatically and has no such marker, so the
    blocks are rejoined into one prompt — sending them would be a 400."""
    payload = _payload(
        model="claude-sonnet-4-6",
        max_tokens=100,
        system=[{"type": "text", "text": "METHOD", "cache_control": {"type": "ephemeral"}}],
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "PREFIX", "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": "QUESTION"},
            ],
        }],
    )
    assert payload["messages"][0] == {"role": "system", "content": "METHOD"}
    assert payload["messages"][1]["content"] == "PREFIX\n\nQUESTION"
    assert "cache_control" not in json.dumps(payload)


def test_temperature_is_dropped_for_gpt5_models():
    """GPT-5 models reject any temperature but the default with a 400
    `unsupported_value`. Several call sites here pass 0.2, so forwarding it
    would fail every one of them."""
    payload = _payload(
        model="claude-sonnet-4-6",
        max_tokens=100,
        system="s",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.2,
    )
    assert "temperature" not in payload


def test_max_tokens_gains_reasoning_headroom():
    """`max_completion_tokens` budgets INVISIBLE reasoning as well as output on
    the GPT-5 family. Passing the caller's number straight through lets a long
    generation spend it all thinking and return an empty string."""
    payload = _payload(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        system="s",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert payload["max_completion_tokens"] > 16000


def test_forced_tool_use_becomes_a_function_tool():
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    payload = _payload(
        model="claude-sonnet-4-6",
        max_tokens=100,
        system="s",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{
            "name": "submit_response",
            "description": "Submit the structured response.",
            "input_schema": schema,
        }],
        tool_choice={"type": "tool", "name": "submit_response"},
    )
    assert payload["tools"] == [{
        "type": "function",
        "function": {
            "name": "submit_response",
            "parameters": schema,
            "description": "Submit the structured response.",
        },
    }]
    assert payload["tool_choice"] == {
        "type": "function",
        "function": {"name": "submit_response"},
    }


def test_web_search_switches_model_instead_of_sending_a_tool():
    """Chat Completions has no web_search TOOL — OpenAI ships search as a model.
    The Anthropic server tool is stripped and the request routed accordingly, so
    `call_with_web_search` keeps working without a provider conditional."""
    from app.openai_client import _SEARCH_MODEL

    payload = _payload(
        model="claude-sonnet-4-6",
        max_tokens=100,
        system="s",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
    )
    assert payload["model"] == _SEARCH_MODEL
    assert "tools" not in payload


def test_tool_loop_round_trip_translates_both_directions():
    """`run_tool_loop` appends the PREVIOUS response's content blocks — our own
    objects, not dicts — straight back onto the message list, then a user turn
    of tool_result dicts. Both have to translate."""
    from app.openai_client import ToolUseBlock

    payload = _payload(
        model="claude-sonnet-4-6",
        max_tokens=100,
        system="s",
        messages=[
            {"role": "user", "content": "look it up"},
            {"role": "assistant", "content": [ToolUseBlock("call_1", "lookup", {"q": "x"})]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": "42"},
            ]},
        ],
    )
    assistant = payload["messages"][2]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["id"] == "call_1"
    assert assistant["tool_calls"][0]["function"]["name"] == "lookup"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"q": "x"}
    # The result is its own `tool` message keyed by the call id — NOT a user turn.
    assert payload["messages"][3] == {
        "role": "tool", "tool_call_id": "call_1", "content": "42",
    }


# ── response translation ─────────────────────────────────────────────────────


def test_response_becomes_anthropic_shaped_blocks():
    from app.openai_client import _message_from_payload

    msg = _message_from_payload({
        "model": "gpt-5.6-terra",
        "choices": [{
            "finish_reason": "tool_calls",
            "message": {
                "content": "thinking out loud",
                "tool_calls": [{
                    "id": "call_9",
                    "function": {"name": "submit_response", "arguments": '{"answer":"yes"}'},
                }],
            },
        }],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
    }, "gpt-5.6-terra")

    assert msg.stop_reason == "tool_use"
    assert msg.model == "gpt-5.6-terra"
    assert [b.type for b in msg.content] == ["text", "tool_use"]
    assert msg.content[1].name == "submit_response"
    assert msg.content[1].input == {"answer": "yes"}


def test_cached_tokens_are_not_billed_as_fresh_input():
    """OpenAI's `prompt_tokens` INCLUDES cache hits and writes; Anthropic's
    `input_tokens` excludes them and reports them separately. Copying the number
    across would price a cache read (~10x cheaper) at the full input rate."""
    from app.openai_client import _usage_from

    usage = _usage_from({
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "prompt_tokens_details": {"cached_tokens": 600, "cache_write_tokens": 100},
    })

    assert usage.input_tokens == 300          # 1000 - 600 cached - 100 written
    assert usage.cache_read_input_tokens == 600
    assert usage.cache_creation_input_tokens == 100
    assert usage.output_tokens == 200
    # The three input fields still account for every token the provider counted.
    assert (
        usage.input_tokens
        + usage.cache_read_input_tokens
        + usage.cache_creation_input_tokens
    ) == 1000


def test_invalid_tool_json_does_not_raise_from_inside_the_client():
    """The model is documented as capable of emitting invalid JSON here. It has
    to surface as the caller's own 'tool wasn't invoked' path, not as a
    JSONDecodeError from deep in the client."""
    from app.openai_client import _message_from_payload

    msg = _message_from_payload({
        "model": "gpt-5.6-terra",
        "choices": [{
            "finish_reason": "tool_calls",
            "message": {"tool_calls": [
                {"id": "c1", "function": {"name": "submit_response", "arguments": "{not json"}},
            ]},
        }],
    }, "gpt-5.6-terra")
    assert msg.content[0].input == {}


# ── the existing call helpers run unchanged on the adapter ───────────────────
#
# The whole point of the adapter: `call_json` / `call_md` / `run_tool_loop` are
# provider-agnostic, so these exercise the REAL helpers end to end with only the
# HTTP layer faked.


@pytest.fixture
def openai_client(isolated_settings, monkeypatch):
    """An OpenAIMessagesClient wired to `sent` / `reply` instead of the network."""
    from app.openai_client import OpenAIMessagesClient

    client = OpenAIMessagesClient(api_key="sk-test")
    state: dict = {"sent": None, "reply": {}}

    def _fake_post(path, payload, timeout):
        state["sent"] = payload
        return state["reply"]

    monkeypatch.setattr(client, "_post", _fake_post)
    monkeypatch.setattr("app.llm.get_client", lambda: client)
    return state


def test_call_json_gets_a_dict_back_through_the_adapter(openai_client):
    from app.llm import call_json

    openai_client["reply"] = {
        "model": "gpt-5.6-terra",
        "choices": [{
            "finish_reason": "tool_calls",
            "message": {"tool_calls": [{
                "id": "c1",
                "function": {
                    "name": "submit_response",
                    "arguments": '{"insights":["a","b"]}',
                },
            }]},
        }],
        "usage": {"prompt_tokens": 40, "completion_tokens": 8},
    }

    meta: dict = {}
    out = call_json(
        system="You are a PM.",
        user="Summarise.",
        schema={"type": "object", "properties": {"insights": {"type": "array"}}},
        meta_out=meta,
    )

    assert out == {"insights": ["a", "b"]}
    # The forced-tool request really was translated, not passed through.
    assert openai_client["sent"]["tool_choice"]["function"]["name"] == "submit_response"
    # Gateway telemetry records the model that ACTUALLY ran, so it prices the
    # tokens against the right rate card.
    assert meta["model"] == "gpt-5.6-terra"
    assert meta["input_tokens"] == 40


def test_call_md_gets_text_back_through_the_adapter(openai_client):
    from app.llm import call_md

    openai_client["reply"] = {
        "model": "gpt-5.6-terra",
        "choices": [{"finish_reason": "stop", "message": {"content": "# Brief\n\nBody."}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 6},
    }

    assert call_md(system="s", user="u") == "# Brief\n\nBody."
    assert openai_client["sent"]["model"] == "gpt-5.6-terra"


def test_run_tool_loop_dispatches_and_terminates(openai_client):
    """Two turns: the model calls a tool, gets the result, then answers."""
    from app.llm import run_tool_loop

    turns = iter([
        {
            "model": "gpt-5.6-terra",
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {"tool_calls": [{
                    "id": "call_1",
                    "function": {"name": "lookup", "arguments": '{"id":"T-1"}'},
                }]},
            }],
        },
        {
            "model": "gpt-5.6-terra",
            "choices": [{"finish_reason": "stop", "message": {"content": "T-1 is open."}}],
        },
    ])

    def _post(path, payload, timeout):
        openai_client["sent"] = payload
        return next(turns)

    import app.llm as llm

    llm.get_client()._post = _post  # type: ignore[method-assign]

    seen: list = []
    out = run_tool_loop(
        system="s",
        user="status of T-1?",
        tools=[{"name": "lookup", "description": "d", "input_schema": {"type": "object"}}],
        dispatch=lambda name, inp: seen.append((name, inp)) or "open",
    )

    assert out == "T-1 is open."
    assert seen == [("lookup", {"id": "T-1"})]
    # The second request carried the tool RESULT as a `tool` role message.
    assert any(m.get("role") == "tool" for m in openai_client["sent"]["messages"])


# ── streaming ────────────────────────────────────────────────────────────────


class _FakeSSE:
    """Stands in for the streaming httpx.Response — only `iter_lines` is used."""

    def __init__(self, chunks: list[dict]):
        self._lines = [f"data: {json.dumps(c)}" for c in chunks] + ["data: [DONE]"]

    def iter_lines(self):
        return iter(self._lines)


def test_streamed_text_is_forwarded_and_assembled():
    from app.openai_client import _OpenAIStream

    stream = _OpenAIStream(
        _FakeSSE([
            {"model": "gpt-5.6-terra", "choices": [{"delta": {"content": "Hel"}}]},
            {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]},
            # include_usage puts usage in a FINAL chunk with empty choices.
            {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 2}},
        ]),
        "gpt-5.6-terra",
    )

    assert list(stream.text_stream) == ["Hel", "lo"]
    msg = stream.get_final_message()
    assert msg.content[0].text == "Hello"
    assert msg.stop_reason == "end_turn"
    assert msg.usage.output_tokens == 2


def test_get_final_message_drains_a_stream_nobody_read():
    """`_create_with_retries` skips the delta loop entirely when no callback was
    given and goes straight to `get_final_message()` — which therefore has to
    consume the body itself rather than return an empty message."""
    from app.openai_client import _OpenAIStream

    stream = _OpenAIStream(
        _FakeSSE([
            {"model": "gpt-5.6-terra", "choices": [{"delta": {"content": "whole"}, "finish_reason": "stop"}]},
        ]),
        "gpt-5.6-terra",
    )
    assert stream.get_final_message().content[0].text == "whole"


def test_streamed_tool_arguments_arrive_as_input_json_events():
    """`app.ask_stream` extracts display text from `input_json` partial-JSON
    fragments as they land — the event name and shape have to match what the
    Anthropic path emits."""
    from app.openai_client import _OpenAIStream

    stream = _OpenAIStream(
        _FakeSSE([
            {"model": "gpt-5.6-terra", "choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "c1", "function": {"name": "submit_response", "arguments": '{"a":'}},
            ]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": '"b"}'}},
            ]}, "finish_reason": "tool_calls"}]},
        ]),
        "gpt-5.6-terra",
    )

    fragments = [e.partial_json for e in stream if e.type == "input_json"]
    assert fragments == ['{"a":', '"b"}']
    msg = stream.get_final_message()
    assert msg.stop_reason == "tool_use"
    assert msg.content[0].name == "submit_response"
    assert msg.content[0].input == {"a": "b"}


# ── retry classification ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status,retryable",
    [(429, True), (500, True), (503, True), (None, True), (400, False), (401, False)],
)
def test_openai_errors_are_classified_like_anthropic_ones(status, retryable):
    from app.llm import _is_retryable
    from app.openai_client import OpenAIAPIError

    assert _is_retryable(OpenAIAPIError("boom", status_code=status)) is retryable


# ── metering ─────────────────────────────────────────────────────────────────


def test_openai_calls_are_metered_as_provider_openai(isolated_settings, monkeypatch):
    """Metering is installed on the client, not the call sites — so an OpenAI
    workspace lands in the ledger with no new write path, tagged so the usage
    dashboard can split the two providers."""
    import app.db.llm_usage as usage_db
    from app.llm_metering import install_metering
    from app.openai_client import Message, Usage

    recorded: list[dict] = []
    monkeypatch.setattr(usage_db, "record_usage", lambda **row: recorded.append(row))

    class _FakeMessages:
        def create(self, **kwargs):
            return Message(
                content=[],
                model="gpt-5.6-terra",
                stop_reason="end_turn",
                usage=Usage(input_tokens=10, output_tokens=5),
            )

    class _FakeClient:
        messages = _FakeMessages()

    client = install_metering(_FakeClient(), "customer", provider="openai")
    with _bind("co-1"):
        client.messages.create(model="claude-sonnet-4-6", max_tokens=10)

    assert len(recorded) == 1
    row = recorded[0]
    assert row["provider"] == "openai"
    # The RESPONSE's model is authoritative, so the ledger records what actually
    # ran, not the Claude id the call site named.
    assert row["model"] == "gpt-5.6-terra"
    assert row["key_mode"] == "customer"
    assert row["est_cost_usd"] == pytest.approx(10 * 2.0e-6 + 5 * 12.0e-6)


# ── admin routes ─────────────────────────────────────────────────────────────


def test_llm_config_reports_both_providers(tenant_client, fernet_key):
    t = tenant_client.make(slug="acme")
    c = t.client

    body = c.get("/v1/admin/llm-config").json()
    assert body["provider"] == "anthropic"
    assert body["providers"]["anthropic"] == {"configured": False, "masked": None}
    assert body["providers"]["openai"] == {"configured": False, "masked": None}


def test_openai_key_roundtrip(tenant_client, fernet_key):
    t = tenant_client.make(slug="acme")
    c = t.client

    r = c.put("/v1/admin/llm-key?provider=openai", json={"api_key": "sk-proj-abcdef1234WXYZ"})
    assert r.status_code == 200, r.text
    assert r.json() == {"configured": True, "masked": "sk-…WXYZ"}

    assert c.get("/v1/admin/llm-key?provider=openai").json()["configured"] is True
    # The Claude slot is untouched — the two keys are independent.
    assert c.get("/v1/admin/llm-key").json()["configured"] is False

    assert c.delete("/v1/admin/llm-key?provider=openai").json() == {
        "configured": False, "masked": None,
    }


def test_both_keys_coexist_and_the_provider_switch_picks_between_them(
    tenant_client, fernet_key
):
    t = tenant_client.make(slug="acme")
    c = t.client

    c.put("/v1/admin/llm-key", json={"api_key": "sk-ant-abcdef1234567890WXYZ"})
    c.put("/v1/admin/llm-key?provider=openai", json={"api_key": "sk-proj-abcdef1234WXYZ"})

    body = c.put("/v1/admin/llm-config", json={"provider": "openai"}).json()
    assert body["provider"] == "openai"
    assert body["providers"]["anthropic"]["configured"] is True
    assert body["providers"]["openai"]["configured"] is True

    # Switching back does not require re-entering anything.
    assert c.put("/v1/admin/llm-config", json={"provider": "anthropic"}).json()[
        "provider"
    ] == "anthropic"


def test_removing_a_key_does_not_change_the_active_provider(tenant_client, fernet_key):
    """"Stop holding my key" and "put me back on Claude" are separate decisions.
    A delete that silently switched provider would move every subsequent call to
    a different model family without anyone asking."""
    t = tenant_client.make(slug="acme")
    c = t.client

    c.put("/v1/admin/llm-key?provider=openai", json={"api_key": "sk-proj-abcdef1234WXYZ"})
    c.put("/v1/admin/llm-config", json={"provider": "openai"})
    c.delete("/v1/admin/llm-key?provider=openai")

    assert c.get("/v1/admin/llm-config").json()["provider"] == "openai"


def test_pasting_a_claude_key_into_the_openai_field_is_rejected(tenant_client, fernet_key):
    t = tenant_client.make(slug="acme")
    r = t.client.put("/v1/admin/llm-key?provider=openai", json={"api_key": "sk-ant-wrongfield12345"})
    assert r.status_code == 400
    assert "OpenAI" in r.json()["detail"]


def test_pasting_an_openai_key_into_the_claude_field_is_rejected(tenant_client, fernet_key):
    t = tenant_client.make(slug="acme")
    r = t.client.put("/v1/admin/llm-key", json={"api_key": "sk-proj-wrongfield12345"})
    assert r.status_code == 400
    assert "sk-ant-" in r.json()["detail"]


def test_unknown_provider_is_rejected_on_write(tenant_client, fernet_key):
    t = tenant_client.make(slug="acme")
    assert t.client.put("/v1/admin/llm-config", json={"provider": "gemini"}).status_code == 400
    assert t.client.put(
        "/v1/admin/llm-key?provider=gemini", json={"api_key": "sk-whatever12345"}
    ).status_code == 400


def test_llm_config_restricted_to_owner_admin(tenant_client, fernet_key):
    from app.db.client import require_client

    t = tenant_client.make(slug="acme")
    require_client().table("company_members").update({"role": "member"}).eq(
        "company_id", t.company_id
    ).execute()

    assert t.client.get("/v1/admin/llm-config").status_code == 403
    assert t.client.put("/v1/admin/llm-config", json={"provider": "openai"}).status_code == 403


def test_llm_config_requires_auth(unauth_client):
    assert unauth_client.get("/v1/admin/llm-config").status_code == 401
