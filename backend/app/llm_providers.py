"""What a "provider" is — the one definition every layer imports.

A company runs its LLM calls on Anthropic (Claude) or on OpenAI. That choice
shows up in five places: the `companies.llm_provider` column, the key resolver
(`app.llm_keys`), the client factories, the admin routes, and the usage ledger's
`provider` column. This module holds the identity facts they all need — the
provider names, the human label, and what a valid key for each one looks like —
so none of them re-states a literal that can drift.

Deliberately dependency-free (stdlib only). `app.llm_keys` imports it, and so
does `app.db.companies`; anything with a config or DB import here would create
the cycle the resolver's lazy DB import already exists to avoid.
"""
from __future__ import annotations

PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI = "openai"

#: Every provider the backend can build a client for, in display order.
PROVIDERS = (PROVIDER_ANTHROPIC, PROVIDER_OPENAI)

#: Product-facing name. The UI says "Claude", not "Anthropic" — that is what the
#: existing Settings copy and the onboarding step already call it.
PROVIDER_LABELS = {
    PROVIDER_ANTHROPIC: "Claude",
    PROVIDER_OPENAI: "OpenAI",
}

#: The prefix a key for each provider must start with, used to catch the single
#: most common paste error (a key pasted into the wrong provider's field) before
#: it is encrypted and stored. Anthropic keys are `sk-ant-…`; OpenAI's are
#: `sk-…` in several shapes (`sk-proj-`, `sk-svcacct-`, plain `sk-`), so the
#: OpenAI check is the loose `sk-` plus an explicit "and not an Anthropic key"
#: rule in `validate_key` — a bare prefix test would accept `sk-ant-` here.
KEY_PREFIXES = {
    PROVIDER_ANTHROPIC: "sk-ant-",
    PROVIDER_OPENAI: "sk-",
}


def normalize_provider(value: str | None) -> str:
    """Coerce anything to a known provider, defaulting to Anthropic.

    Used on every read of `companies.llm_provider`. The column is CHECK-
    constrained, but this sits on the LLM hot path: an unexpected value (a row
    written before the constraint, a future provider read by an older process)
    must degrade to today's behaviour, not take generation down.
    """
    v = (value or "").strip().lower()
    return v if v in PROVIDERS else PROVIDER_ANTHROPIC


def key_error_message(provider: str) -> str:
    """The 400 shown when a key doesn't match the field it was pasted into."""
    if provider == PROVIDER_OPENAI:
        return (
            "That doesn't look like an OpenAI API key — it should start with "
            "'sk-' (and an 'sk-ant-' key is a Claude key, not an OpenAI one)."
        )
    return (
        "That doesn't look like an Anthropic API key — it should start with "
        "'sk-ant-'."
    )


def validate_key(provider: str, api_key: str) -> bool:
    """Cheap shape check on a pasted key. Not authentication — the admin
    "Test key" button does that against the live API."""
    key = (api_key or "").strip()
    if provider == PROVIDER_OPENAI:
        # `sk-ant-` also starts with `sk-`, so exclude it explicitly: pasting a
        # Claude key into the OpenAI field is the error this guard exists for.
        return key.startswith("sk-") and not key.startswith("sk-ant-")
    return key.startswith(KEY_PREFIXES[PROVIDER_ANTHROPIC])


def mask_key(provider: str, api_key: str) -> str:
    """A safe preview: keep the provider's prefix + the last 4 chars.

    Enough for an admin to recognise WHICH key is stored without the value ever
    leaving the server in full.
    """
    key = (api_key or "").strip()
    prefix = KEY_PREFIXES.get(provider, "sk-")
    if len(key) <= len(prefix) + 7:
        return f"{prefix}…"
    head = len(prefix) if key.startswith(prefix) else 3
    return f"{key[:head]}…{key[-4:]}"
