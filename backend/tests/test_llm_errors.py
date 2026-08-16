"""Provider limit errors, named — so a refusal can be shown instead of guessed.

The failure this file exists for: on 2026-08-16 the Anthropic balance ran out,
every surface degraded correctly and silently (chat fell open to `answer`, the
planner answered unplanned, the classifier answered directly, the ask job
failed with a stringified exception), and NOTHING told the user why. Commands
simply stopped being acted on.

Two things are pinned here:
  * the out-of-credits refusal is recognised — it arrives as a 400, not a 429,
    so status code alone cannot find it;
  * the provider's own text NEVER becomes the user's message, because a
    provider body can carry request ids, org names and billing detail.
"""
from __future__ import annotations

import pytest

from app import llm_errors


class _FakeStatusError(Exception):
    """Stands in for anthropic.APIStatusError / openai.APIStatusError without
    constructing a real SDK error (which needs an httpx response)."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def _as_anthropic(monkeypatch, exc_cls) -> None:
    """Make `_status_and_text` treat `exc_cls` as an anthropic status error."""
    import anthropic

    monkeypatch.setattr(anthropic, "APIStatusError", exc_cls, raising=False)


# ── the reported case ────────────────────────────────────────────────────────

_CREDIT_MSG = (
    "Error code: 400 - {'type': 'error', 'error': {'type': "
    "'invalid_request_error', 'message': 'Your credit balance is too low to "
    "access the Anthropic API. Please go to Plans & Billing to upgrade or "
    "purchase credits.'}, 'request_id': 'req_011Ce6viFqTQFqWBL5XzFv1r'}"
)


def test_an_exhausted_balance_is_a_limit_even_though_it_is_a_400(monkeypatch):
    """THE case. Anthropic reports no credits as a 400 invalid_request_error —
    indistinguishable from a malformed request by status code — so recognising
    it needs the body. Getting this wrong is what made the outage invisible."""
    _as_anthropic(monkeypatch, _FakeStatusError)
    exc = _FakeStatusError(_CREDIT_MSG, 400)
    assert llm_errors.classify_provider_error(exc) == llm_errors.PROVIDER_LIMIT


def test_the_users_message_never_carries_the_providers_own_text(monkeypatch):
    """A provider body holds request ids and billing detail. The user gets a
    fixed sentence; the raw text belongs in the log."""
    _as_anthropic(monkeypatch, _FakeStatusError)
    notice = llm_errors.limit_notice(_FakeStatusError(_CREDIT_MSG, 400))
    assert notice is not None
    assert "req_011Ce6vi" not in notice["message"]
    assert "Plans & Billing" not in notice["message"]
    assert "Anthropic" not in notice["message"]
    # It still has to be actionable.
    assert "admin" in notice["message"].lower()


# ── the rest of the space ────────────────────────────────────────────────────


@pytest.mark.parametrize("status,text,expected", [
    (429, "rate limited", llm_errors.PROVIDER_LIMIT),
    (400, "You exceeded your current quota", llm_errors.PROVIDER_LIMIT),
    (429, "insufficient_quota", llm_errors.PROVIDER_LIMIT),
    (529, "Overloaded", llm_errors.PROVIDER_UNAVAILABLE),
    (503, "upstream unavailable", llm_errors.PROVIDER_UNAVAILABLE),
    (400, "messages.0.content: field required", llm_errors.PROVIDER_ERROR),
    (401, "invalid x-api-key", llm_errors.PROVIDER_ERROR),
])
def test_classification(monkeypatch, status, text, expected):
    _as_anthropic(monkeypatch, _FakeStatusError)
    assert llm_errors.classify_provider_error(_FakeStatusError(text, status)) == expected


def test_a_429_is_a_limit_whatever_the_body_says(monkeypatch):
    _as_anthropic(monkeypatch, _FakeStatusError)
    assert llm_errors.classify_provider_error(
        _FakeStatusError("no useful text here", 429)
    ) == llm_errors.PROVIDER_LIMIT


def test_a_malformed_request_is_not_reported_as_a_billing_problem(monkeypatch):
    """The old rule was "any APIStatusError is billing", which sent an admin
    to check a balance that was fine. A 400 with no quota marker is a
    PROVIDER_ERROR, and its copy does not mention credits."""
    _as_anthropic(monkeypatch, _FakeStatusError)
    exc = _FakeStatusError("messages: at least one message is required", 400)
    code = llm_errors.classify_provider_error(exc)
    assert code == llm_errors.PROVIDER_ERROR
    assert "credit" not in llm_errors.user_message(code).lower()


@pytest.mark.parametrize("exc", [
    ValueError("a plain bug"),
    KeyError("missing"),
    TimeoutError("slow"),
])
def test_a_non_provider_exception_is_not_ours(exc):
    """None means "not mine" — the caller keeps its own classification. This
    function widens what the product can EXPLAIN; it never takes over the
    general error path."""
    assert llm_errors.classify_provider_error(exc) is None
    assert llm_errors.limit_notice(exc) is None


def test_every_code_has_copy():
    for code in llm_errors.PROVIDER_CODES:
        assert llm_errors.user_message(code).strip()


def test_an_unknown_code_still_returns_a_sentence():
    """A classifier miss must never become a second failure on the error
    path — an empty or raised message is worse than a generic one."""
    assert llm_errors.user_message("something_new").strip()


# ── the ask job's classifier delegates here ──────────────────────────────────


def test_ask_job_classifier_reports_the_provider_code(monkeypatch):
    """`ask_job_runner._classify_error` used to answer `billing` for every
    provider status error. It now delegates, so the stored `error_class` is
    specific enough for a surface to turn into a sentence."""
    from app import ask_job_runner

    _as_anthropic(monkeypatch, _FakeStatusError)
    assert ask_job_runner._classify_error(
        _FakeStatusError(_CREDIT_MSG, 400)
    ) == llm_errors.PROVIDER_LIMIT


def test_ask_job_classifier_leaves_the_other_categories_alone(monkeypatch):
    """The provider arm widened; timeout / local_gate / app did not move."""
    import asyncio

    from fastapi import HTTPException

    from app import ask_job_runner

    _as_anthropic(monkeypatch, _FakeStatusError)
    assert ask_job_runner._classify_error(asyncio.TimeoutError()) == "timeout"
    assert ask_job_runner._classify_error(HTTPException(403, "nope")) == "local_gate"
    assert ask_job_runner._classify_error(ValueError("bug")) == "app"


# ── the chat intent envelope carries it ──────────────────────────────────────


def test_a_provider_failure_rides_the_fallback_envelope(monkeypatch):
    """The fail-open contract is right, but with the planner down NO action can
    be chosen — so the envelope has to say why, or commands just stop working
    with nothing on screen."""
    from app import chat_intent

    _as_anthropic(monkeypatch, _FakeStatusError)
    env = chat_intent._fallback("resolver error", _FakeStatusError(_CREDIT_MSG, 400))
    assert env["intent"] == "answer"
    assert env["provider_error"]["code"] == llm_errors.PROVIDER_LIMIT
    assert env["provider_error"]["message"]


def test_an_ordinary_fallback_carries_no_provider_error():
    """Present-and-null on every ordinary failure, so nothing changes for the
    failures that are genuinely ours."""
    from app import chat_intent

    assert chat_intent._fallback("unknown action")["provider_error"] is None
    assert chat_intent._fallback("resolver error", ValueError("bug"))["provider_error"] is None
