"""Thin wrapper over the Anthropic SDK."""
import json

from anthropic import Anthropic
from fastapi import HTTPException

from app.config import settings

DEFAULT_MODEL = "claude-sonnet-4-6"

_client: Anthropic | None = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise HTTPException(500, "ANTHROPIC_API_KEY not configured")
        _client = Anthropic(api_key=settings.anthropic_api_key)
    return _client


def _build_base_kwargs(
    *,
    model: str,
    max_tokens: int,
    system: str,
    user: str,
    user_cacheable_prefix: str | None,
) -> dict:
    """Build the kwargs dict passed to `messages.create`.

    If `user_cacheable_prefix` is None, returns the simple `content=str` form
    used by every existing caller — behavior is unchanged. Otherwise builds
    content as a list of text blocks, with `cache_control: ephemeral` on the
    prefix (and on the system prompt when it's substantial enough to be
    worth caching).
    """
    if user_cacheable_prefix is None:
        return {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
    system_param: list[dict] = [
        {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        if len(system) > 1000
        else {"type": "text", "text": system}
    ]
    content = [
        {
            "type": "text",
            "text": user_cacheable_prefix,
            "cache_control": {"type": "ephemeral"},
        },
        {"type": "text", "text": user},
    ]
    return {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_param,
        "messages": [{"role": "user", "content": content}],
    }


def call_json(
    *,
    system: str,
    user: str,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 16000,
    schema: dict | None = None,
    user_cacheable_prefix: str | None = None,
) -> dict:
    """Call Claude expecting a strict JSON object response.

    If `schema` is provided, uses Anthropic tool-use with a forced tool_choice
    — the SDK validates the structured input and returns a real dict, which
    eliminates the JSON-string-escaping failures that happen when an LLM
    hand-writes JSON containing markdown tables, quoted text, etc.

    If `schema` is None, falls back to parsing the model's text response as
    JSON (used by endpoints whose payload is simple enough to round-trip
    safely).

    If `user_cacheable_prefix` is provided, it is sent as a separate text
    block before `user` with `cache_control: ephemeral` set, so subsequent
    calls within the cache TTL reuse the prefix tokens. When the system
    prompt is also substantial (>1000 chars), it gets the same treatment.
    """
    client = get_client()
    base_kwargs: dict = _build_base_kwargs(
        model=model,
        max_tokens=max_tokens,
        system=system,
        user=user,
        user_cacheable_prefix=user_cacheable_prefix,
    )
    if schema is not None:
        tool = {
            "name": "submit_response",
            "description": "Submit the structured response. All fields required.",
            "input_schema": schema,
        }
        msg = client.messages.create(
            **base_kwargs,
            tools=[tool],
            tool_choice={"type": "tool", "name": "submit_response"},
        )
        for block in msg.content:
            if block.type == "tool_use" and block.name == "submit_response":
                return dict(block.input) if not isinstance(block.input, dict) else block.input
        raise HTTPException(
            502, "LLM did not invoke the structured response tool"
        )

    msg = client.messages.create(**base_kwargs)
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    # Tolerate accidental fences
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.lstrip("json").lstrip("\n").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            502, f"LLM returned invalid JSON: {exc}; first 400 chars: {text[:400]!r}"
        ) from exc


def call_md(
    *,
    system: str,
    user: str,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 16000,
) -> str:
    """Call Claude expecting plain markdown output."""
    msg = get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()
