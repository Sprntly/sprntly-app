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


def call_json(
    *,
    system: str,
    user: str,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 16000,
) -> dict:
    """Call Claude expecting a strict JSON object response."""
    msg = get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
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
    max_tokens: int = 12000,
) -> str:
    """Call Claude expecting plain markdown output."""
    msg = get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()
