"""Generic tool registry + dispatch.

Each connector module (currently just app/agent_tools/github.py) registers
its tools at import time via `register()`. The agent loop calls
`list_tools()` to give Anthropic the tool definitions, then `dispatch()`
to run the tool the model chose.

Tool shape mirrors Anthropic's tool-use schema:
    {
      "name": "github_get_file",
      "description": "Read a file from a connected GitHub repository.",
      "input_schema": {
        "type": "object",
        "properties": { ... },
        "required": [...]
      }
    }

The Python callable is stored separately in `_DISPATCH` and is invoked
with `installation_id` (resolved from the company's connection row by
the route layer) plus the kwargs the model provided.
"""
from __future__ import annotations

from typing import Any, Callable

# Tool definitions (the JSON Schema half the model sees).
_TOOLS: list[dict[str, Any]] = []

# Tool callables (the Python half the backend runs).
_DISPATCH: dict[str, Callable[..., Any]] = {}


def register(definition: dict[str, Any], fn: Callable[..., Any]) -> None:
    """Add a tool to the global registry.

    `definition` is the Anthropic-shaped JSON spec; `fn` is the Python
    function that runs when the model picks this tool. The function must
    accept `installation_id` as a keyword arg plus whatever its
    `input_schema` declares.
    """
    name = definition.get("name")
    if not name:
        raise ValueError("tool definition missing 'name'")
    if name in _DISPATCH:
        # Re-registering the same tool (e.g. test reload) is fine — replace.
        for i, t in enumerate(_TOOLS):
            if t.get("name") == name:
                _TOOLS[i] = definition
                break
    else:
        _TOOLS.append(definition)
    _DISPATCH[name] = fn


def list_tools() -> list[dict[str, Any]]:
    """Return all registered tool definitions (for `tools=` on Anthropic's
    messages.create)."""
    return list(_TOOLS)


def dispatch(name: str, args: dict[str, Any], *, installation_id: int) -> Any:
    """Look up the tool by name and call it. Raises KeyError if unknown."""
    fn = _DISPATCH.get(name)
    if fn is None:
        raise KeyError(f"unknown tool: {name}")
    return fn(installation_id=installation_id, **args)
