"""Static AST autofixer — invokes the Node @babel/parser companion (P1-10).

Per agent-build-research.md §2.4 (v0's deterministic post-stream autofixer is
"the biggest reliability boost in the ecosystem") + §5.2 (hallucinated imports
is the most common single failure mode). Per AD22: static analysis is
permitted; browser self-testing is NOT (no runtime feedback loop).

Four deterministic fixers (each sub-250ms target per file; the actual AST work
lives in the Node companion autofixer.js):
1. Hallucinated-import detection (against the prototype virtual_fs + allowlist)
2. Tailwind class validation (shadcn semantic-token detection)
3. shadcn component validation (against the installed registry)
4. JSX/TS syntax soundness (parse failure -> is_error)

Return shape:
  {"ok": True}                                          on clean validation
  {"ok": False, "errors": [{"fixer", "line", "col",     on validation failure
                            "message", "suggestion"}]}

Best-effort contract: on Node-missing / timeout / subprocess failure / invalid
JSON, returns {"ok": True} and logs ONE warning. A broken validator must never
block the agent loop — tsc/build errors downstream catch what the static
analyser missed.

Module resolution: @babel/parser lives in prototype-runtime/node_modules (the
existing P0 Vite-pipeline install). The subprocess is launched with NODE_PATH
pointed there, so no backend-side Node install is introduced (AD13 / no new
build tooling).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from app.design_agent.autofixer_data import payload_data
from app.design_agent.build_env import scrubbed_node_env

logger = logging.getLogger(__name__)

_AUTOFIXER_JS = Path(__file__).with_suffix(".js")
# design_agent -> app -> backend -> repo root; prototype-runtime is a sibling
# of backend/ holding the @babel/parser install (P0-02 pin). This is the
# production default location for the @babel/parser resolution.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROTOTYPE_RUNTIME_NODE_MODULES = _REPO_ROOT / "prototype-runtime" / "node_modules"
_NODE_BIN = os.environ.get("NODE_BIN", "node")
_SUBPROCESS_TIMEOUT_S = 8.0


def _node_modules_path() -> Path:
    """Directory placed on NODE_PATH so `require('@babel/parser')` resolves.

    Defaults to prototype-runtime/node_modules (the P0 Vite install) in
    production. Overridable via `DESIGN_AGENT_NODE_PATH` — used by backend CI
    to point at an isolated @babel/parser install (the CI image does not carry
    the prototype-runtime node_modules). Production behaviour is unchanged when
    the env var is unset."""
    override = os.environ.get("DESIGN_AGENT_NODE_PATH")
    return Path(override) if override else _PROTOTYPE_RUNTIME_NODE_MODULES


def _subprocess_env() -> dict[str, str]:
    """Secret-free env for the Node subprocess, with NODE_PATH pointing at the
    node_modules that holds @babel/parser so `require('@babel/parser')` resolves
    without a backend-side install. Prepends to any inherited NODE_PATH rather
    than clobbering it.

    The autofixer runs Node on agent-generated (user-influenced) code, so — like
    the Vite build — it must NOT inherit the backend's secrets. We build from an
    allowlisted base (see build_env) instead of `dict(os.environ)`."""
    node_modules = str(_node_modules_path())
    existing = os.environ.get("NODE_PATH")
    node_path = (
        f"{node_modules}{os.pathsep}{existing}" if existing else node_modules
    )
    return scrubbed_node_env({"NODE_PATH": node_path})


async def run(file_path: str, content: str, virtual_fs: dict[str, str]) -> dict[str, Any]:
    """Run the autofixer on a single emitted file. Returns the structured result.

    `virtual_fs` is the prototype's in-memory file map (path -> content); the
    hallucinated-import fixer cross-references imports against its keys. Only
    `.tsx`/`.ts` files are validated; anything else short-circuits to ok without
    spawning Node. The Node script reads the payload from stdin and writes the
    result to stdout (one-shot; no persistent process).
    """
    if not file_path.endswith((".tsx", ".ts")):
        return {"ok": True}

    payload = json.dumps({
        "file_path": file_path,
        "content": content,
        "virtual_fs_paths": list(virtual_fs.keys()),
        "data": payload_data(),
    })

    # Run via asyncio.to_thread + synchronous subprocess.run so the autofixer
    # works under uvicorn's WindowsSelectorEventLoopPolicy, which does not support
    # asyncio.create_subprocess_exec (raises NotImplementedError). Mirrors the
    # approach used by storage.vite_build (same constraint, same fix).
    try:
        import subprocess as _subprocess
        result = await asyncio.to_thread(
            _subprocess.run,
            [_NODE_BIN, str(_AUTOFIXER_JS)],
            input=payload.encode("utf-8"),
            capture_output=True,
            timeout=_SUBPROCESS_TIMEOUT_S,
            env=_subprocess_env(),
        )
    except FileNotFoundError:
        logger.warning("autofixer_node_missing bin=%s", _NODE_BIN)
        return {"ok": True}
    except _subprocess.TimeoutExpired:
        logger.warning("autofixer_timeout file_path=%s", file_path)
        return {"ok": True}
    except Exception as exc:
        logger.warning("autofixer_subprocess_error file_path=%s error_class=%s", file_path, type(exc).__name__)
        return {"ok": True}

    if result.returncode != 0:
        logger.warning(
            "autofixer_subprocess_failed file_path=%s rc=%s stderr=%s",
            file_path, result.returncode,
            result.stderr.decode("utf-8", errors="replace")[:200],
        )
        return {"ok": True}

    try:
        return json.loads(result.stdout.decode("utf-8"))
    except json.JSONDecodeError:
        logger.warning("autofixer_invalid_json file_path=%s", file_path)
        return {"ok": True}



def format_errors_for_agent(result: dict[str, Any]) -> str:
    """Render the autofixer result as plain-text feedback for the agent's
    tool_result block. On ok results returns a one-line pass message."""
    if result.get("ok"):
        return "Static analysis passed."
    lines = ["Static analysis failed. Fix each error and retry:"]
    for err in result.get("errors", []):
        loc = (
            f"line {err.get('line')}, col {err.get('col')}"
            if err.get("line")
            else "(no location)"
        )
        suggestion = f" Suggestion: {err.get('suggestion')}" if err.get("suggestion") else ""
        lines.append(f"  - [{err.get('fixer')}] {loc}: {err.get('message')}{suggestion}")
    return "\n".join(lines)
