"""The request this codebase builds is one the INSTALLED SDK will accept.

THE GAP THIS CLOSES. `conftest.py` patches `app.llm.call_json` and substitutes
a fake Supabase, so no test in this suite has ever called the real Anthropic
SDK — right for speed and cost, and it leaves the suite structurally blind to
the SDK's own surface changing underneath it. `requirements.txt` carried
`anthropic>=0.55.0`, the only bare `>=` in a file of `==` pins, and
`deploy-backend.yml` runs `pip install -r requirements.txt` on every deploy.
A new major could therefore arrive with no code change, no failing test, and
no deploy error.

It did. `anthropic` 1.0.0 removed `temperature`, `top_p` and `top_k` from
`messages.create()`, and `ask_planner` passes `temperature=0`. Every planner
call raised

    TypeError: Messages.create() got an unexpected keyword argument 'temperature'

before a single byte reached Anthropic. `plan_for_answer` catches that and
logs "answering unplanned" — a safety net written for a transient outage,
quietly absorbing a permanent one. Nothing surfaced as broken; the product
simply lost its router, and every question fell to the generic answer path.
"Show me the PRDs I created" came back assembled from the knowledge graph and
a Slack channel index, naming PRD ids that do not exist in the workspace.

WHAT THIS ASSERTS. Not a version number — that goes stale the day the pin
moves and teaches nobody anything. It asserts the CONTRACT, against whatever
`pip install -r requirements.txt` actually resolved: the keys `app.llm` puts
in its request survive translation into something `messages.create` accepts.
It is deliberately built from `app.llm`'s own output rather than a list
written here, so it keeps covering the payload as that changes.
"""
from __future__ import annotations

import inspect

import pytest

anthropic = pytest.importorskip("anthropic")

from app import llm  # noqa: E402 — after the SDK availability check


def _accepted_parameters() -> set[str]:
    """Keywords `client.messages.create` accepts, per the installed SDK."""
    client = anthropic.Anthropic(api_key="not-a-real-key")
    return set(inspect.signature(client.messages.create).parameters)


def _payload(**over) -> dict:
    """A request exactly as `app.llm` builds one, planner-shaped."""
    kwargs = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 1024,
        "system": "sys",
        "user": "hello",
        "user_cacheable_prefix": None,
        "temperature": 0,
    }
    kwargs.update(over)
    return llm._build_base_kwargs(**kwargs)


def test_every_key_we_send_is_one_the_sdk_accepts():
    """The whole point: no keyword reaches `messages.create` that it will
    reject with a TypeError."""
    client = anthropic.Anthropic(api_key="not-a-real-key")
    sent = llm._anthropic_kwargs(client, _payload())
    rejected = sorted(set(sent) - _accepted_parameters())
    assert not rejected, (
        f"The installed anthropic SDK ({anthropic.__version__}) does not accept "
        f"{rejected} on messages.create(). Passing one raises TypeError before "
        "any request is made, which app.ask_planner catches and degrades to "
        "'answering unplanned' — the product loses its router without "
        "reporting an error. Fix the call shape or hold the pin; do not let "
        "this test be the only thing that noticed."
    )


def test_temperature_still_reaches_the_request_body():
    """Determinism is the reason `temperature=0` is on sixteen call sites. It
    must still be SENT — moved into `extra_body`, not dropped."""
    client = anthropic.Anthropic(api_key="not-a-real-key")
    sent = llm._anthropic_kwargs(client, _payload())
    assert sent.get("extra_body", {}).get("temperature") == 0
    assert "temperature" not in sent, (
        "temperature must not ALSO ride the signature — that is the TypeError."
    )
    assert "extra_body" in _accepted_parameters(), (
        "The installed SDK has no `extra_body`, so there is nowhere left to "
        "put a sampling parameter."
    )


def test_a_request_without_temperature_is_untouched():
    """No caller pays for this. A payload that never set one keeps the exact
    shape it had before — no empty `extra_body` appears."""
    client = anthropic.Anthropic(api_key="not-a-real-key")
    payload = _payload(temperature=None)
    assert llm._anthropic_kwargs(client, payload) == payload
    assert "extra_body" not in payload


def test_the_openai_shim_still_gets_temperature_where_it_looks():
    """`app.openai_client` reads `kwargs["temperature"]` off this same dict and
    decides for itself whether the model takes one. The translation is
    Anthropic-only; moving it for every provider would silently drop it
    there."""
    from app.openai_client import OpenAIMessagesClient

    source = inspect.getsource(OpenAIMessagesClient.__module__ and __import__(
        "app.openai_client", fromlist=["x"]
    ))
    assert 'kwargs.get("temperature")' in source, (
        "The OpenAI shim no longer reads temperature off the payload — "
        "re-check whether the Anthropic-only translation is still correct."
    )
    payload = _payload()
    # Not an Anthropic client → untouched, so the shim finds it where it looks.
    assert llm._anthropic_kwargs(object(), payload) is payload
    assert payload["temperature"] == 0
