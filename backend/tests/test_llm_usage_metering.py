"""Tests for the unified LLM usage ledger.

Covers the four moving parts of the feature:
  1. `app.usage_context`  — scope inheritance + the gateway agent -> feature map
  2. `app.llm_keys`       — which key paid (customer vs platform)
  3. `app.llm_metering`   — the client proxy that turns a model call into a row
  4. `app.db.llm_usage`   — the buffered writer and the rollup reader

The metering tests drive a stand-in client rather than the real SDK: the proxy's
contract is "whatever object has `.messages`, intercept `.create` / `.stream`",
so a SimpleNamespace exercises the same code path an `anthropic.Anthropic` does
without a network call or an API key.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.llm_metering import install_metering
from app.usage_context import Feature, current_scope, feature_for_agent, usage_scope

_CO = "co-usage-1"


def _msg(*, model="claude-sonnet-4-6", inp=1000, out=500, cache_read=0, cache_write=0):
    """A stand-in Anthropic Message carrying only what metering reads."""
    return SimpleNamespace(
        model=model,
        usage=SimpleNamespace(
            input_tokens=inp,
            output_tokens=out,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
        ),
    )


@pytest.fixture
def captured(monkeypatch):
    """Capture `record_usage` kwargs instead of buffering them for the DB."""
    rows: list[dict] = []
    import app.db.llm_usage as llm_usage

    monkeypatch.setattr(llm_usage, "record_usage", lambda **kw: rows.append(kw))
    return rows


@pytest.fixture
def bound():
    """Bind an acting company, as the request middleware does in production."""
    from app.llm_keys import company_llm_key

    with company_llm_key(_CO):
        yield


# --- usage_context -----------------------------------------------------------

def test_scope_defaults_to_unattributed():
    assert current_scope().feature == Feature.UNATTRIBUTED


def test_nested_scope_inherits_unset_fields():
    """An inner block narrows the operation without restating feature/user."""
    with usage_scope(feature=Feature.PRD, user_id="u-1"):
        with usage_scope(operation="clarify"):
            scope = current_scope()
            assert (scope.feature, scope.operation, scope.user_id) == (
                Feature.PRD, "clarify", "u-1",
            )


def test_scope_restored_after_exception():
    with pytest.raises(ValueError):
        with usage_scope(feature=Feature.PRD):
            raise ValueError("boom")
    assert current_scope().feature == Feature.UNATTRIBUTED


@pytest.mark.parametrize(
    "agent,expected",
    [
        ("prd", Feature.PRD),
        ("qa-router", Feature.QA),
        ("technical_design", Feature.DOCUMENTS),
        ("market_research", Feature.RESEARCH),
        ("ingest:github", Feature.KG_INGEST),
        ("ingest:google_drive", Feature.KG_INGEST),
        # Unmapped agents keep their own name — a new agent is still labelled,
        # not silently pooled with genuinely unattributed calls.
        ("brand_new_agent", "brand_new_agent"),
    ],
)
def test_feature_for_agent(agent, expected):
    assert feature_for_agent(agent) == expected


# --- llm_keys: whose key paid ------------------------------------------------

def test_key_mode_is_platform_when_unbound():
    from app.llm_keys import KEY_MODE_PLATFORM, resolve_llm_api_key_with_mode

    assert resolve_llm_api_key_with_mode("sk-ant-platform") == (
        "sk-ant-platform", KEY_MODE_PLATFORM,
    )


def test_key_mode_is_customer_when_company_has_own_key(monkeypatch, bound):
    import app.llm_keys as keys

    monkeypatch.setattr(
        keys, "_resolve", lambda _cid: keys._Resolution(company_key="sk-ant-THEIRS")
    )
    assert keys.resolve_llm_api_key_with_mode("sk-ant-platform") == (
        "sk-ant-THEIRS", keys.KEY_MODE_CUSTOMER,
    )


def test_key_mode_is_platform_when_company_has_no_key(monkeypatch, bound):
    import app.llm_keys as keys

    monkeypatch.setattr(
        keys, "_resolve", lambda _cid: keys._Resolution(company_key=None)
    )
    assert keys.resolve_llm_api_key_with_mode("sk-ant-platform") == (
        "sk-ant-platform", keys.KEY_MODE_PLATFORM,
    )


# --- metering: messages.create ----------------------------------------------

def test_create_records_one_row_with_tokens_and_cost(captured, bound):
    client = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **kw: _msg())
    )
    install_metering(client, "customer")

    with usage_scope(feature=Feature.PRD, operation="generate", user_id="u-9"):
        client.messages.create(model="claude-sonnet-4-6", max_tokens=10)

    assert len(captured) == 1
    row = captured[0]
    assert row["company_id"] == _CO
    assert row["feature"] == Feature.PRD
    assert row["operation"] == "generate"
    assert row["user_id"] == "u-9"
    assert row["key_mode"] == "customer"
    assert row["input_tokens"] == 1000
    assert row["output_tokens"] == 500
    assert row["status"] == "succeeded"
    # sonnet-4-6: 1000 in @ $3/MTok + 500 out @ $15/MTok = $0.0105
    assert row["est_cost_usd"] == pytest.approx(0.0105)


def test_create_returns_the_real_message_untouched(captured, bound):
    sentinel = _msg()
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: sentinel))
    install_metering(client, "platform")
    assert client.messages.create(model="claude-sonnet-4-6") is sentinel


def test_unattributed_call_is_still_recorded(captured, bound):
    """A forgotten scope loses the LABEL, never the SPEND."""
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: _msg()))
    install_metering(client, "platform")
    client.messages.create(model="claude-sonnet-4-6")
    assert captured[0]["feature"] == Feature.UNATTRIBUTED
    assert captured[0]["input_tokens"] == 1000


def test_unbound_company_records_nothing(captured):
    """No tenant in scope (CLI / startup probe) → nothing to attribute."""
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: _msg()))
    install_metering(client, "platform")
    client.messages.create(model="claude-sonnet-4-6")
    assert captured == []


def test_unpriced_model_keeps_tokens_and_nulls_cost(captured, bound):
    """Fails SOFT: an unpriced model must not take down the calling feature."""
    client = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **kw: _msg(model="claude-unpriced-9"))
    )
    install_metering(client, "platform")
    client.messages.create(model="claude-unpriced-9")

    assert captured[0]["est_cost_usd"] is None
    assert captured[0]["input_tokens"] == 1000
    assert captured[0]["model"] == "claude-unpriced-9"


def test_failed_call_records_failure_and_reraises(captured, bound):
    def _boom(**_kw):
        raise RuntimeError("overloaded")

    client = SimpleNamespace(messages=SimpleNamespace(create=_boom))
    install_metering(client, "platform")

    with pytest.raises(RuntimeError):
        client.messages.create(model="claude-sonnet-4-6")

    assert captured[0]["status"] == "failed"
    assert captured[0]["error_class"] == "RuntimeError"


def test_metering_failure_never_breaks_the_call(monkeypatch, bound):
    """The whole point of fail-open: a broken ledger degrades the dashboard."""
    import app.db.llm_usage as llm_usage

    def _explode(**_kw):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(llm_usage, "record_usage", _explode)
    sentinel = _msg()
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: sentinel))
    install_metering(client, "platform")

    assert client.messages.create(model="claude-sonnet-4-6") is sentinel


# --- metering: messages.stream ----------------------------------------------

class _FakeStreamManager:
    """Mimics the SDK's `MessageStreamManager` context manager."""

    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return SimpleNamespace(
            get_final_message=lambda: self._message,
            __iter__=lambda self_: iter([]),
        )

    def __exit__(self, *_a):
        return False


def test_stream_records_when_final_message_is_built(captured, bound):
    message = _msg(inp=2000, out=100)
    client = SimpleNamespace(
        messages=SimpleNamespace(stream=lambda **kw: _FakeStreamManager(message))
    )
    install_metering(client, "customer")

    with usage_scope(feature=Feature.DESIGN_AGENT, operation="generate"):
        with client.messages.stream(model="claude-sonnet-4-6") as stream:
            assert stream.get_final_message() is message

    assert len(captured) == 1
    assert captured[0]["feature"] == Feature.DESIGN_AGENT
    assert captured[0]["input_tokens"] == 2000


def test_stream_records_once_when_final_message_read_twice(captured, bound):
    message = _msg()
    client = SimpleNamespace(
        messages=SimpleNamespace(stream=lambda **kw: _FakeStreamManager(message))
    )
    install_metering(client, "platform")

    with client.messages.stream(model="claude-sonnet-4-6") as stream:
        stream.get_final_message()
        stream.get_final_message()

    assert len(captured) == 1


# --- install_metering --------------------------------------------------------

def test_install_metering_is_idempotent(captured, bound):
    """A double-wrap must not double-count."""
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: _msg()))
    install_metering(client, "platform")
    first = client.messages
    install_metering(client, "platform")
    assert client.messages is first

    client.messages.create(model="claude-sonnet-4-6")
    assert len(captured) == 1


def test_unintercepted_client_attributes_are_forwarded():
    client = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **kw: _msg(), count_tokens="TOKENS"),
    )
    install_metering(client, "platform")
    # Anything that isn't create/stream still reaches the real messages object.
    assert client.messages.count_tokens == "TOKENS"


# --- writer ------------------------------------------------------------------

def test_flush_writes_buffered_rows(isolated_settings):
    import app.db.llm_usage as llm_usage
    from app.db.client import require_client

    llm_usage.disable_background_writer()
    llm_usage.reset_for_tests()

    llm_usage.record_usage(
        company_id=_CO, feature=Feature.PRD, operation="generate",
        model="claude-sonnet-4-6", key_mode="customer",
        input_tokens=10, output_tokens=5, est_cost_usd=0.001,
    )
    llm_usage.record_usage(
        company_id=_CO, feature=Feature.CHAT, model="claude-sonnet-4-6",
        key_mode="customer", input_tokens=1, output_tokens=1,
    )
    assert llm_usage.flush() == 2

    rows = (
        require_client().table("llm_usage_events")
        .select("*").eq("company_id", _CO).execute().data
    )
    assert {r["feature"] for r in rows} == {Feature.PRD, Feature.CHAT}
    assert sum(r["input_tokens"] for r in rows) == 11


def test_writer_failure_is_swallowed(isolated_settings, monkeypatch):
    import app.db.llm_usage as llm_usage

    llm_usage.disable_background_writer()
    llm_usage.reset_for_tests()
    monkeypatch.setattr(
        llm_usage, "_insert", lambda _rows: (_ for _ in ()).throw(RuntimeError("down"))
    )
    llm_usage.record_usage(company_id=_CO, feature=Feature.PRD)
    llm_usage.flush()  # must not raise


def test_fetch_summary_returns_empty_on_rpc_failure(isolated_settings, monkeypatch):
    """An unavailable ledger yields an empty dashboard, never a 500."""
    import app.db.llm_usage as llm_usage

    monkeypatch.setattr(
        llm_usage, "require_client",
        lambda: (_ for _ in ()).throw(RuntimeError("no db")),
    )
    assert llm_usage.fetch_usage_summary(
        company_id=_CO, start="2026-07-01T00:00:00Z", end="2026-07-31T00:00:00Z"
    ) == []
