"""Provider-error classification for the Design Agent run loop.

The terminal catch in the agent loop must NEVER surface raw provider text
(SDK exception strings can carry account state, request bodies, internal
hints) to any client-visible field. This module maps a raw exception to a
small, safe taxonomy and provides fixed generic messages so the run loop
records a class + a curated message, and logs the raw text ONLY.

Default-deny: anything unrecognized classifies as INTERNAL. Raw text never
passes through.
"""

from __future__ import annotations

from enum import Enum

# Substring markers (lowercased) that indicate an Anthropic billing / credit
# hard-stop rather than a transient or malformed-request failure.
_BILLING_MARKERS = (
    "credit balance",
    "billing",
    "too low",
    "insufficient",
    "quota",
)


class ProviderErrorClass(str, Enum):
    PROVIDER_BILLING = "PROVIDER_BILLING"
    PROVIDER_CAPACITY = "PROVIDER_CAPACITY"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_AUTH = "PROVIDER_AUTH"
    INTERNAL = "INTERNAL"


def _exc_text(exc: Exception) -> str:
    """Best-effort lowercase text of an exception for marker matching, folding
    in a structured body's `message`/`type` when present. Never raises."""
    parts: list[str] = []
    try:
        parts.append(str(exc))
    except Exception:  # noqa: BLE001 — defensive; a broken __str__ must not crash us
        pass
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            for key in ("message", "type"):
                val = err.get(key)
                if isinstance(val, str):
                    parts.append(val)
        for key in ("message", "type"):
            val = body.get(key)
            if isinstance(val, str):
                parts.append(val)
    return " ".join(parts).lower()


def _has_billing_signature(exc: Exception) -> bool:
    text = _exc_text(exc)
    return any(marker in text for marker in _BILLING_MARKERS)


def classify_provider_error(exc: Exception) -> ProviderErrorClass:
    """Map a raw exception to the safe taxonomy. Defensive against a missing or
    older `anthropic` SDK: the exception-type import is guarded, and matching
    falls back to `type(exc).__name__` + a `status_code` attribute so a run in
    an environment without those types still classifies sensibly.

    Default-deny: unrecognized ⇒ INTERNAL.
    """
    try:  # guard the import so a missing/older SDK never crashes classification
        import anthropic

        RateLimitError = getattr(anthropic, "RateLimitError", ())
        AuthenticationError = getattr(anthropic, "AuthenticationError", ())
        PermissionDeniedError = getattr(anthropic, "PermissionDeniedError", ())
        BadRequestError = getattr(anthropic, "BadRequestError", ())
        APIStatusError = getattr(anthropic, "APIStatusError", ())
        APIConnectionError = getattr(anthropic, "APIConnectionError", ())
    except Exception:  # noqa: BLE001 — SDK absent/older; fall through to name matching
        RateLimitError = AuthenticationError = PermissionDeniedError = ()
        BadRequestError = APIStatusError = APIConnectionError = ()

    name = type(exc).__name__
    status_code = getattr(exc, "status_code", None)

    # Capacity: rate limits + Anthropic's 529 "overloaded".
    if (RateLimitError and isinstance(exc, RateLimitError)) or name == "RateLimitError":
        return ProviderErrorClass.PROVIDER_CAPACITY
    if status_code == 529:
        return ProviderErrorClass.PROVIDER_CAPACITY

    # Auth: bad/rejected key or insufficient permission.
    if (
        (AuthenticationError and isinstance(exc, AuthenticationError))
        or (PermissionDeniedError and isinstance(exc, PermissionDeniedError))
        or name in ("AuthenticationError", "PermissionDeniedError")
        or status_code in (401, 403)
    ):
        return ProviderErrorClass.PROVIDER_AUTH

    # Billing: a 400/invalid-request carrying a credit/billing signature.
    is_bad_request = (
        (BadRequestError and isinstance(exc, BadRequestError))
        or name == "BadRequestError"
        or status_code == 400
    )
    if is_bad_request and _has_billing_signature(exc):
        return ProviderErrorClass.PROVIDER_BILLING

    # Unavailable: connection/timeout + 5xx (excluding 529 handled above).
    if (
        (APIConnectionError and isinstance(exc, APIConnectionError))
        or name in ("APIConnectionError", "APITimeoutError")
    ):
        return ProviderErrorClass.PROVIDER_UNAVAILABLE
    if isinstance(status_code, int) and 500 <= status_code < 600:
        return ProviderErrorClass.PROVIDER_UNAVAILABLE

    # Default-deny.
    return ProviderErrorClass.INTERNAL


# Fixed generic messages, one per class. NEVER raw. The frontend overrides the
# user-facing copy via reasonCopy keyed off the class name; these exist so any
# surface that reads error_message directly still shows something safe.
_SAFE_MESSAGES: dict[ProviderErrorClass, str] = {
    ProviderErrorClass.PROVIDER_BILLING: "The prototype service is temporarily unavailable.",
    ProviderErrorClass.PROVIDER_AUTH: "The prototype service is temporarily unavailable.",
    ProviderErrorClass.PROVIDER_CAPACITY: "The service is busy. Try again shortly.",
    ProviderErrorClass.PROVIDER_UNAVAILABLE: "The prototype service is temporarily unavailable.",
    ProviderErrorClass.INTERNAL: "Something went wrong.",
}


def safe_error_class(exc: Exception) -> str:
    return classify_provider_error(exc).value


def safe_error_message(cls: ProviderErrorClass) -> str:
    return _SAFE_MESSAGES.get(cls, _SAFE_MESSAGES[ProviderErrorClass.INTERNAL])


def is_alertable(cls: ProviderErrorClass) -> bool:
    """A billing hard-stop needs a human to top up credits — worth an alert."""
    return cls is ProviderErrorClass.PROVIDER_BILLING


def is_retryable(cls: ProviderErrorClass) -> bool:
    """Billing/auth won't self-resolve on retry; capacity/unavailable might."""
    return cls in (
        ProviderErrorClass.PROVIDER_CAPACITY,
        ProviderErrorClass.PROVIDER_UNAVAILABLE,
    )
