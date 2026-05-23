"""Multi-turn chat loop driving Claude with the ds-agent tools.

`turn()` accepts a user message and returns the assistant's reply
text plus the list of tool invocations that happened during the turn
(handy for the UI to render activity chips).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from anthropic import Anthropic

from . import tools
from .state import SessionState


_MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-4-6")
_MAX_TOOL_LOOPS = 6


_SYSTEM_PROMPT = """You are Sprntly's data-science chat agent.

You're talking to a product manager or operator who wants insights from their data. They've loaded a dataset (CSV) into the session. Your job is to surface findings from it using the ds-agent pipeline, then answer follow-ups.

How you operate:
1. On the FIRST turn after a dataset is loaded, call `describe_dataset` to see what columns exist, propose a sensible goal metric (e.g. retention_30d if present), confirm with the user before running, then call `set_goal_metric` and `run_pattern_discovery`.
2. Present findings in plain English. Lead with the headline (which behavior moved the metric most, by how much, with what confidence). Then 2–4 supporting findings. Then offer to drill in.
3. For follow-ups ("what about by region?", "why does posts matter so much?"), call `focus_on_finding` for the relevant behavior — its segment_variation field already covers per-stratum splits.
4. NEVER make up numbers. If a tool returns an error, say so honestly and ask the user what they want to do.
5. Be terse. PMs don't have time for hedging paragraphs.

Hard rules:
- If the user asks about a column that isn't in `describe_dataset`'s output, say so — don't hallucinate.
- If `run_pattern_discovery` hasn't been called yet, don't claim any specific finding. Run it first.
- If confidence on a finding is LOW, label it as "early signal" not "result".
- If the user uploaded their own data (vs. a sample), be extra careful about data-quality caveats.

Available tools: describe_dataset, set_goal_metric, run_pattern_discovery, focus_on_finding."""


@dataclass
class TurnResult:
    assistant_text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class ChatRunner:
    def __init__(self, api_key: str | None = None, model: str = _MODEL) -> None:
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is required for the agent service.")
        self.client = Anthropic(api_key=key)
        self.model = model

    def turn(self, session: SessionState, user_message: str) -> TurnResult:
        # Append the user turn to the persistent transcript
        session.messages.append({"role": "user", "content": user_message})

        tool_calls: list[dict[str, Any]] = []
        final_text_parts: list[str] = []

        for _ in range(_MAX_TOOL_LOOPS):
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=1500,
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=tools.TOOL_SCHEMAS,
                messages=session.messages,
            )

            assistant_blocks: list[dict[str, Any]] = []
            tool_use_blocks: list[Any] = []
            text_in_this_pass: list[str] = []

            for block in resp.content:
                if block.type == "text":
                    text_in_this_pass.append(block.text)
                    assistant_blocks.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    tool_use_blocks.append(block)
                    assistant_blocks.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )

            # Record the assistant message as-is so subsequent turns see it
            session.messages.append({"role": "assistant", "content": assistant_blocks})

            if resp.stop_reason != "tool_use" or not tool_use_blocks:
                # Conversation turn is finished
                final_text_parts.extend(text_in_this_pass)
                break

            # Execute each requested tool, append the results as a user message
            tool_results: list[dict[str, Any]] = []
            for tb in tool_use_blocks:
                result = tools.execute(tb.name, tb.input or {}, session)
                tool_calls.append(
                    {"name": tb.name, "input": tb.input, "is_error": "error" in result}
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tb.id,
                        "content": json.dumps(result, default=str),
                        "is_error": "error" in result,
                    }
                )
            session.messages.append({"role": "user", "content": tool_results})

            # Carry any text from this pass into the final answer so we don't
            # drop pre-tool commentary like "Let me look at the columns..."
            final_text_parts.extend(text_in_this_pass)
        else:
            final_text_parts.append(
                "_(stopped after too many tool loops — ask me to try a smaller question)_"
            )

        return TurnResult(
            assistant_text="\n\n".join(p.strip() for p in final_text_parts if p.strip()),
            tool_calls=tool_calls,
        )
