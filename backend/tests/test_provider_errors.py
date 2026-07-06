"""Provider-error classification + safe-message + fail-open alert tests.

The whole point: raw provider text NEVER reaches a client-visible field. These
tests pin the taxonomy, prove the safe message drops the raw credit text, and
prove the ops alert is deduped + fail-open + a clean no-op when unconfigured.
"""

from __future__ import annotations

import pytest

from app import config
from app.design_agent import provider_alert
from app.design_agent.provider_errors import (
    ProviderErrorClass,
    classify_provider_error,
    is_alertable,
    is_retryable,
    safe_error_class,
    safe_error_message,
)


# --- dummy exceptions matching the defensive name + status_code + body shape ---
# The classifier's guarded path falls back to `type(exc).__name__` +
# `status_code` + a structured `body`, so a synthetic exception with the right
# class name exercises it without needing the anthropic SDK's constructors.

def _mk(name, *, status_code=None, message="", body=None):
    cls = type(name, (Exception,), {})  # a real subclass so type(exc).__name__ == name
    exc = cls(message)
    if status_code is not None:
        exc.status_code = status_code
    if body is not None:
        exc.body = body
    return exc


def test_classify_billing_from_400_credit_signature():
    exc = _mk(
        "BadRequestError",
        status_code=400,
        message="Your credit balance is too low to access the Anthropic API.",
    )
    assert classify_provider_error(exc) is ProviderErrorClass.PROVIDER_BILLING


def test_classify_billing_from_body_invalid_request_marker():
    exc = _mk(
        "BadRequestError",
        status_code=400,
        message="request failed",
        body={"error": {"type": "invalid_request_error", "message": "insufficient quota"}},
    )
    assert classify_provider_error(exc) is ProviderErrorClass.PROVIDER_BILLING


def test_classify_capacity_ratelimit():
    exc = _mk("RateLimitError", status_code=429, message="rate limited")
    assert classify_provider_error(exc) is ProviderErrorClass.PROVIDER_CAPACITY


def test_classify_capacity_529():
    exc = _mk("APIStatusError", status_code=529, message="overloaded")
    assert classify_provider_error(exc) is ProviderErrorClass.PROVIDER_CAPACITY


def test_classify_auth():
    exc = _mk("AuthenticationError", status_code=401, message="invalid x-api-key")
    assert classify_provider_error(exc) is ProviderErrorClass.PROVIDER_AUTH


def test_classify_unavailable_connection():
    exc = _mk("APIConnectionError", message="connection error")
    assert classify_provider_error(exc) is ProviderErrorClass.PROVIDER_UNAVAILABLE
    exc5xx = _mk("APIStatusError", status_code=503, message="service unavailable")
    assert classify_provider_error(exc5xx) is ProviderErrorClass.PROVIDER_UNAVAILABLE


def test_classify_unknown_is_internal():
    assert classify_provider_error(ValueError("boom")) is ProviderErrorClass.INTERNAL
    # A 400 with NO billing signature must NOT be mistaken for billing.
    exc = _mk("BadRequestError", status_code=400, message="messages: too many tokens")
    assert classify_provider_error(exc) is ProviderErrorClass.INTERNAL


def test_safe_error_class_returns_value():
    exc = _mk("RateLimitError", status_code=429)
    assert safe_error_class(exc) == "PROVIDER_CAPACITY"


def test_safe_error_message_never_raw():
    exc = _mk(
        "BadRequestError",
        status_code=400,
        message="Your credit balance is too low to access the Anthropic API.",
    )
    cls = classify_provider_error(exc)
    msg = safe_error_message(cls)
    assert "credit balance" not in msg.lower()
    assert "anthropic" not in msg.lower()
    assert msg == "The prototype service is temporarily unavailable."


def test_is_retryable():
    assert is_retryable(ProviderErrorClass.PROVIDER_CAPACITY) is True
    assert is_retryable(ProviderErrorClass.PROVIDER_UNAVAILABLE) is True
    assert is_retryable(ProviderErrorClass.PROVIDER_BILLING) is False
    assert is_retryable(ProviderErrorClass.PROVIDER_AUTH) is False
    assert is_retryable(ProviderErrorClass.INTERNAL) is False


def test_is_alertable():
    assert is_alertable(ProviderErrorClass.PROVIDER_BILLING) is True
    for cls in (
        ProviderErrorClass.PROVIDER_CAPACITY,
        ProviderErrorClass.PROVIDER_AUTH,
        ProviderErrorClass.PROVIDER_UNAVAILABLE,
        ProviderErrorClass.INTERNAL,
    ):
        assert is_alertable(cls) is False


# ------------------------------- alert tests --------------------------------

@pytest.fixture(autouse=True)
def _reset_alert_state(monkeypatch):
    """Reset module dedup state + point settings at a known recipient/api_key so
    each alert test starts clean."""
    provider_alert._last_sent.clear()
    monkeypatch.setattr(config.settings, "design_agent_alert_email", "ops@example.com", raising=False)
    monkeypatch.setattr(config.settings, "resend_api_key", "re_test", raising=False)
    yield
    provider_alert._last_sent.clear()


def test_alert_fires_once_per_window(monkeypatch):
    calls = {"n": 0}

    def _fake_send(api_key, *, to, subject, html_body, text_body):
        calls["n"] += 1

    from app.synthesis import email_delivery
    monkeypatch.setattr(email_delivery, "_send_via_resend", _fake_send)

    cls = ProviderErrorClass.PROVIDER_BILLING
    provider_alert.maybe_alert_provider_outage(cls, context={"prototype_id": 7})
    provider_alert.maybe_alert_provider_outage(cls, context={"prototype_id": 7})
    assert calls["n"] == 1  # second within the window is deduped


def test_alert_fail_open_on_send_error(monkeypatch):
    def _boom(api_key, *, to, subject, html_body, text_body):
        raise RuntimeError("resend down")

    from app.synthesis import email_delivery
    monkeypatch.setattr(email_delivery, "_send_via_resend", _boom)

    # Must return normally — a failed send never breaks the run.
    provider_alert.maybe_alert_provider_outage(
        ProviderErrorClass.PROVIDER_BILLING, context={"prototype_id": 9}
    )


def test_alert_noop_when_recipient_unset(monkeypatch):
    calls = {"n": 0}

    def _fake_send(api_key, *, to, subject, html_body, text_body):
        calls["n"] += 1

    from app.synthesis import email_delivery
    monkeypatch.setattr(email_delivery, "_send_via_resend", _fake_send)
    monkeypatch.setattr(config.settings, "design_agent_alert_email", "", raising=False)

    provider_alert.maybe_alert_provider_outage(
        ProviderErrorClass.PROVIDER_BILLING, context={"prototype_id": 1}
    )
    assert calls["n"] == 0  # no recipient ⇒ clean no-op, no crash


def test_alert_noop_for_non_alertable_class(monkeypatch):
    calls = {"n": 0}

    def _fake_send(api_key, *, to, subject, html_body, text_body):
        calls["n"] += 1

    from app.synthesis import email_delivery
    monkeypatch.setattr(email_delivery, "_send_via_resend", _fake_send)

    provider_alert.maybe_alert_provider_outage(
        ProviderErrorClass.PROVIDER_CAPACITY, context={"prototype_id": 1}
    )
    assert calls["n"] == 0


def test_runner_error_message_is_safe_not_raw():
    """Compose the runner's sanitization (classify → safe class + safe message)
    on a billing exception: error_message is generic, error_class is the safe
    token, and the raw credit text is absent."""
    exc = _mk(
        "BadRequestError",
        status_code=400,
        message="Your credit balance is too low. Please add billing.",
    )
    cls = classify_provider_error(exc)
    error_class = cls.value
    error_message = safe_error_message(cls)

    assert error_class == "PROVIDER_BILLING"
    assert "credit balance" not in error_message.lower()
    assert "billing" not in error_message.lower()
    assert error_message == "The prototype service is temporarily unavailable."
