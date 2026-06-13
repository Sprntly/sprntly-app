"""Run a skill's bundled deterministic script ON OUR OWN INFRA.

Decision (2026-06-13): the 4 script-bearing skills carry small, fixed,
deterministic Python (RICE/ICE scoring, A/B sample size, SaaS-metric math, PRD
structural lint). They are OURS — not model-authored — so we run them locally
rather than via an external sandbox. The only untrusted thing is the
*arguments*, which the model proposes and we validate against a JSON schema
before running. **We invoke by a typed argv list (never a shell string)** and
feed JSON/text payloads on stdin.

Each script is exposed to the answer loop as one tool: `{name, description,
input_schema}` for the Messages API, plus a `run(args) -> str` that validates,
builds argv, and returns stdout (or a clean error string the model can read).
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Callable

from app.skills.loader import SKILLS_ROOT

_RUN_TIMEOUT_S = 15  # these scripts are arithmetic; anything slower is a bug


def _script_path(skill_id: str, name: str):
    return SKILLS_ROOT / skill_id / "scripts" / name


def _run(skill_id: str, name: str, argv: list[str], stdin: str | None = None) -> str:
    """Run `python <script> <argv...>` with stdin; return stdout or an error
    string. argv is a list — no shell, so user-derived values can't inject."""
    path = _script_path(skill_id, name)
    if not path.is_file():
        return f"(script {name} not found for skill {skill_id})"
    try:
        proc = subprocess.run(
            [sys.executable, str(path), *argv],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=_RUN_TIMEOUT_S,
            cwd=str(path.parent),
        )
    except subprocess.TimeoutExpired:
        return f"(script {name} timed out after {_RUN_TIMEOUT_S}s)"
    out = proc.stdout.strip()
    # Some scripts (e.g. prd_lint) use a nonzero exit code to *signal a result*
    # (blocking issues found) while the real output is on stdout. Treat any
    # non-empty stdout as the result; only a nonzero exit with no stdout is a
    # genuine failure.
    if out:
        return out
    if proc.returncode != 0:
        return f"(script {name} failed: {proc.stderr.strip() or 'nonzero exit'})"
    return out


# ── per-script arg validation + argv builders ────────────────────────────────


def _flag(args: dict, key: str) -> list[str]:
    v = args.get(key)
    return [f"--{key.replace('_', '-')}", str(v)] if v is not None else []


def _run_score(args: dict) -> str:
    method = args.get("method")
    if method not in ("rice", "wsjf", "voc", "northstar"):
        return "(score: 'method' must be one of rice|wsjf|voc|northstar)"
    items = args.get("items")
    if not isinstance(items, list) or not items:
        return "(score: 'items' must be a non-empty JSON array)"
    argv = ["--method", method]
    argv += _flag(args, "mode") + _flag(args, "north_star")
    argv += _flag(args, "ns_weight") + _flag(args, "goal_weight")
    return _run("prioritize", "score.py", argv, stdin=json.dumps(items))


def _run_sample_size(args: dict) -> str:
    try:
        baseline = float(args["baseline"])
        mde = float(args["mde"])
    except (KeyError, TypeError, ValueError):
        return "(sample_size: 'baseline' and 'mde' are required numbers)"
    argv = ["--baseline", str(baseline), "--mde", str(mde)]
    argv += _flag(args, "power") + _flag(args, "alpha")
    return _run("experiment-design", "sample_size.py", argv)


def _run_saas_metrics(args: dict) -> str:
    metrics = args.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        return "(saas_metrics: 'metrics' must be a non-empty JSON object)"
    return _run("saas-metrics-diagnosis", "saas_metrics.py", [], stdin=json.dumps(metrics))


def _run_prd_lint(args: dict) -> str:
    text = args.get("prd_text")
    if not isinstance(text, str) or not text.strip():
        return "(prd_lint: 'prd_text' must be the PRD markdown to check)"
    return _run("prd-critique", "prd_lint.py", [], stdin=text)


@dataclass(frozen=True)
class ScriptTool:
    skill_id: str
    name: str
    description: str
    input_schema: dict
    run: Callable[[dict], str]

    def as_tool(self) -> dict:
        """Anthropic Messages API tool definition."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


# skill_id → the one script tool it exposes to the answer loop.
SCRIPT_TOOLS: dict[str, ScriptTool] = {
    "prioritize": ScriptTool(
        skill_id="prioritize",
        name="prioritize_score",
        description=(
            "Compute a deterministic prioritization ranking. Pass the items to "
            "rank and the framework; returns the ranked table. Use this instead "
            "of doing the arithmetic yourself."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["rice", "wsjf", "voc", "northstar"]},
                "mode": {"type": "string", "enum": ["plain", "goal"]},
                "north_star": {"type": "string"},
                "ns_weight": {"type": "number"},
                "goal_weight": {"type": "number"},
                "items": {
                    "type": "array",
                    "description": "List of item objects with the fields the chosen method needs (e.g. reach/impact/confidence/effort for RICE).",
                    "items": {"type": "object"},
                },
            },
            "required": ["method", "items"],
        },
        run=_run_score,
    ),
    "experiment-design": ScriptTool(
        skill_id="experiment-design",
        name="experiment_sample_size",
        description="Compute per-arm and total A/B sample size from a baseline rate and a minimum detectable effect (absolute lift).",
        input_schema={
            "type": "object",
            "properties": {
                "baseline": {"type": "number", "description": "Baseline conversion rate, e.g. 0.10"},
                "mde": {"type": "number", "description": "Absolute lift to detect, e.g. 0.005 for +0.5pp"},
                "power": {"type": "number"},
                "alpha": {"type": "number"},
            },
            "required": ["baseline", "mde"],
        },
        run=_run_sample_size,
    ),
    "saas-metrics-diagnosis": ScriptTool(
        skill_id="saas-metrics-diagnosis",
        name="saas_metrics",
        description="Compute standard SaaS metrics (LTV/CAC, payback, magic number, etc.) from raw inputs.",
        input_schema={
            "type": "object",
            "properties": {
                "metrics": {
                    "type": "object",
                    "description": "Raw metric inputs (e.g. mrr, new_mrr, churned_mrr, cac, arpa, gross_margin).",
                }
            },
            "required": ["metrics"],
        },
        run=_run_saas_metrics,
    ),
    "prd-critique": ScriptTool(
        skill_id="prd-critique",
        name="prd_lint",
        description="Run deterministic structural checks on a PRD's markdown and return blocking issues + warnings.",
        input_schema={
            "type": "object",
            "properties": {
                "prd_text": {"type": "string", "description": "The PRD markdown to lint."}
            },
            "required": ["prd_text"],
        },
        run=_run_prd_lint,
    ),
}


def tool_for(skill_id: str) -> ScriptTool | None:
    return SCRIPT_TOOLS.get(skill_id)
