"""Provider limit errors, named — so the user is told instead of guessing.

When the LLM provider refuses a request for a QUOTA reason — the account is out
of credits, the key is rate limited, the org hit its spend cap — every surface
in this product degrades quietly and correctly:

  * `chat_intent` fails open to `answer` (its documented contract),
  * `ask_planner` answers unplanned,
  * `qa_agent`'s classifier answers directly,
  * `ask_job_runner` marks the job `error` with a stringified exception.

Each of those is the right local behaviour and the sum of them is a product
that looks broken for no stated reason. Observed 2026-08-16: the Anthropic
balance ran out, the chat stopped acting on commands entirely (no action can be
chosen when the planner cannot run), and NOTHING on screen said why — the only
evidence was `credit balance is too low` in the container log.

This module is the single place that recognises that class of failure and
turns it into a stable code plus a sentence a person can act on.

THE MESSAGES ARE FIXED STRINGS, never the provider's own text. A provider error
body is untrusted output that can carry request ids, org names, key prefixes
and billing detail; it belongs in the log, not on a customer's screen. The code
is what callers branch on, the message is what they show.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

#: The account cannot spend right now — out of credits, over quota, or rate
#: limited. An operator has to act (top up, raise the limit) or the caller has
#: to wait; either way retrying the same request immediately will not help.
PROVIDER_LIMIT = "provider_limit"

#: The provider is up but shedding load (529 overloaded, 5xx). Transient —
#: `llm._is_retryable` already retries these, so reaching a caller means the
#: retries were exhausted too.
PROVIDER_UNAVAILABLE = "provider_unavailable"

#: A provider refusal that is neither of the above (a malformed request, an
#: invalid key). Not actionable by the user; named so it does not masquerade as
#: a billing problem, which is what the previous "any APIStatusError is
#: billing" rule did.
PROVIDER_ERROR = "provider_error"

#: Every code this module can produce. Surfaces test membership rather than
#: listing the three literals, so adding a code reaches them automatically.
PROVIDER_CODES: frozenset[str] = frozenset(
    {PROVIDER_LIMIT, PROVIDER_UNAVAILABLE, PROVIDER_ERROR}
)

_MESSAGES: dict[str, str] = {
    PROVIDER_LIMIT: (
        "Sprntly's AI provider has hit a usage limit — the account is out of "
        "credits or rate limited, so requests can't be processed right now. "
        "An admin needs to top up or raise the limit; nothing you did caused "
        "this, and nothing was lost."
    ),
    PROVIDER_UNAVAILABLE: (
        "Sprntly's AI provider is overloaded and turned this request away "
        "after several retries. This usually clears on its own — try again in "
        "a minute."
    ),
    PROVIDER_ERROR: (
        "Sprntly's AI provider rejected this request. If it keeps happening, "
        "let support know."
    ),
}

#: Substrings that mark a QUOTA refusal rather than an ordinary bad request.
#: Needed because the headline case arrives as a 400, not a 429: Anthropic
#: reports an exhausted balance as
#:   400 invalid_request_error "Your credit balance is too low to access the
#:   Anthropic API. Please go to Plans & Billing to upgrade or purchase
#:   credits."
#: which is indistinguishable from a malformed request by status code alone.
#: OpenAI's equivalent is `insufficient_quota` (also commonly a 429).
#: Matched case-insensitively against the exception text ONLY — never shown.
_LIMIT_MARKERS: tuple[str, ...] = (
    "credit balance is too low",
    "insufficient_quota",
    "insufficient quota",
    "exceeded your current quota",
    "billing",
    "plans & billing",
    "spend limit",
    "usage limit",
    "quota exceeded",
    "rate_limit",
    "rate limit",
)


def user_message(code: str) -> str:
    """The sentence to show for a code. Unknown codes fall back to the generic
    provider refusal rather than raising — a classifier miss must never turn
    into a second failure on the error path."""
    return _MESSAGES.get(code, _MESSAGES[PROVIDER_ERROR])


def classify_provider_error(exc: BaseException) -> Optional[str]:
    """The provider-failure code for `exc`, or None when this is not a
    provider refusal at all (a local gate, a bug, a timeout).

    None is the important return: it means "not mine", and the caller keeps
    whatever classification it already had. This function widens what the
    product can EXPLAIN; it never takes over the general error path.
    """
    status, text = _status_and_text(exc)
    if status is None and not text:
        return None

    haystack = text.lower()

    # 429 is a rate limit by definition, whatever the body says.
    if status == 429:
        return PROVIDER_LIMIT
    # A quota refusal delivered as a 4xx — the headline case. Checked before
    # the generic 4xx arm below so it is not flattened into PROVIDER_ERROR.
    if any(marker in haystack for marker in _LIMIT_MARKERS):
        return PROVIDER_LIMIT
    # 529 is Anthropic's "overloaded"; 5xx is the provider's own fault.
    if status is not None and status >= 500:
        return PROVIDER_UNAVAILABLE
    if status is not None:
        return PROVIDER_ERROR
    return None


def _status_and_text(exc: BaseException) -> tuple[Optional[int], str]:
    """(HTTP status, message text) for a provider exception, or (None, "").

    Imports are local and guarded: this module is imported on paths that must
    not depend on either SDK being installed, and a missing provider package
    must degrade to "not a provider error" rather than an ImportError raised
    while handling some other error.
    """
    status: Optional[int] = None
    matched = False

    try:
        import anthropic

        if isinstance(exc, anthropic.APIStatusError):
            status, matched = exc.status_code, True
        elif isinstance(exc, anthropic.APIError):
            matched = True
    except Exception:  # noqa: BLE001 — SDK absent/broken: not a provider error
        pass

    if not matched:
        try:
            from openai import APIError as OpenAIAPIError
            from openai import APIStatusError as OpenAIAPIStatusError

            if isinstance(exc, OpenAIAPIStatusError):
                status, matched = exc.status_code, True
            elif isinstance(exc, OpenAIAPIError):
                matched = True
        except Exception:  # noqa: BLE001 — same reasoning as above
            pass

    if not matched:
        return None, ""
    return status, str(exc)


def limit_notice(exc: BaseException) -> Optional[dict]:
    """`{code, message}` when `exc` is a provider failure worth telling the
    user about, else None.

    The one entry point surfaces should call. Logs the real provider text at
    WARNING (the operator needs the actual reason; the user gets the fixed
    sentence) and returns only the safe pair.
    """
    code = classify_provider_error(exc)
    if code is None:
        return None
    logger.warning(
        "provider refusal code=%s detail=%s", code, str(exc)[:500],
    )
    return {"code": code, "message": user_message(code)}
