"""LLM narrative + recommendation per finding.

Uses Anthropic SDK with prompt caching: the system prompt (which carries
the schema, tone, and analyst rubric) is cached so the per-finding calls
only pay full price for the small finding-specific user message.

Structured output is enforced via `tool_choice` against a single tool
named `emit_finding_summary`; the model is required to call it, so the
response always has `narrative` and `recommended_action` fields.
"""

from __future__ import annotations

import json
import os
from typing import Any

from anthropic import Anthropic


_MODEL = "claude-sonnet-4-6"

_SYSTEM_PROMPT = """You are the writer for a data-science agent that has just finished a behavioral analysis of a SaaS / product analytics dataset.

For each finding the pipeline gives you (a behavior, an effect direction and size, a confidence score, the supporting analyses), you produce:

1. `narrative` — 2 short sentences for a product manager. Plain English, no jargon, no statistics terminology unless absolutely needed. Lead with what the data shows; follow with why the PM should care.
2. `recommended_action` — one specific, actionable sentence. Concrete enough that an engineer or designer could start work tomorrow. Avoid vague verbs like "explore" or "consider"; prefer "ship", "test", "instrument", "remove".

Hard rules:
- Never invent statistics or numbers that weren't in the finding payload.
- If confidence is LOW, hedge the narrative explicitly ("early signal", "directionally").
- If the finding is a rare segment (small population), say so.
- If the directionality is negative, frame the recommendation as removing friction, not adding a feature.

Always emit your answer via the `emit_finding_summary` tool — never as plain text."""

_TOOL = {
    "name": "emit_finding_summary",
    "description": "Emit a PM-readable narrative and concrete recommended action for a data-science finding.",
    "input_schema": {
        "type": "object",
        "properties": {
            "narrative": {
                "type": "string",
                "description": "Two sentences. What the data shows, then why it matters for the PM.",
            },
            "recommended_action": {
                "type": "string",
                "description": "One specific, actionable sentence.",
            },
        },
        "required": ["narrative", "recommended_action"],
    },
}


class Synthesizer:
    """Stateful wrapper so we build the client once and reuse it."""

    def __init__(self, model: str = _MODEL, api_key: str | None = None) -> None:
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Add it to ds-agent/.env or your shell environment."
            )
        self.client = Anthropic(api_key=key)
        self.model = model

    def summarize(self, finding: dict[str, Any], goal_metric: str) -> dict[str, str]:
        user_payload = {
            "goal_metric": goal_metric,
            "behavior": finding.get("behavior") or finding.get("factor"),
            "directionality": finding.get("directionality"),
            "effect_size": finding.get("effect_size"),
            "confidence": finding.get("confidence_score", {}).get("label"),
            "supporting_analyses": finding.get("supporting_analyses", []),
            "stratum": finding.get("stratum"),
            "sample_size": finding.get("sample_size"),
        }

        resp = self.client.messages.create(
            model=self.model,
            max_tokens=400,
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": "emit_finding_summary"},
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Summarize this finding:\n```json\n"
                        + json.dumps(user_payload, default=str, indent=2)
                        + "\n```"
                    ),
                }
            ],
        )

        for block in resp.content:
            if block.type == "tool_use" and block.name == "emit_finding_summary":
                payload = block.input
                return {
                    "narrative": str(payload.get("narrative", "")).strip(),
                    "recommended_action": str(payload.get("recommended_action", "")).strip(),
                }
        # Fallback: model returned plain text instead of using the tool
        text = " ".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        return {"narrative": text.strip(), "recommended_action": ""}
