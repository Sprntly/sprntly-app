"""Locate LLM service: maps a PRD + compact MapResult to ranked screen candidates.

Single LLM call. Returns up to three LocateCandidates ranked by confidence, each
carrying a 0-100 confidence score, a one-line rationale, and an explicit ambiguous
flag. Mirrors the single-shot messages.create + JSON-fence-strip + model_validate +
RunUsage never-raise pattern from design_system/brief.py.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.design_agent.codebase_map.gate import GateResult
    from app.design_agent.codebase_map.types import MapResult

logger = logging.getLogger(__name__)

# Canonical model for the locate call; never substitute opus here.
_MODEL = "claude-sonnet-4-6"

# Hard output cap — the JSON payload is at most three candidates.
_LOCATE_MAX_TOKENS = 1024

# Clamp free-text rationale fields after parsing.
_MAX_RATIONALE_CHARS = 300

# Guard against pathologically large repos blowing the stable prefix.
_COMPACT_MAP_CHAR_CAP = 8000


class LocateCandidate(BaseModel):
    route: str = ""
    entry_component: str = ""
    confidence: int = 0          # 0-100, clamped on parse
    rationale: str = ""          # one-line model rationale
    ambiguous: bool = False      # the model's explicit abstention flag for this candidate


class LocateResult(BaseModel):
    candidates: list[LocateCandidate] = Field(default_factory=list)  # ranked, ≤3
    is_multi_node: bool = False  # True when the PRD legitimately spans a screen set
    # honest default: empty candidates ⇒ "no codebase locate" ⇒ caller degrades


def compact_map(m: "MapResult") -> str:
    """One line per screen node: route · entry_component · N components.

    Includes a SHELL line (brand + nav labels) and the posture.
    No file bodies, no source — the registry view is sufficient for locate.
    """
    lines: list[str] = []
    lines.append(f"POSTURE: {m.posture}")

    nav_labels = ", ".join(item.label for item in m.shell.nav_items)
    lines.append(f'SHELL: brand="{m.shell.brand}" nav=[{nav_labels}]')

    lines.append("SCREENS:")
    for node in m.nodes:
        count = len(node.composed_components)
        suffix = " (route-state)" if node.is_route_state else ""
        lines.append(
            f"- {node.route} · {node.entry_component} · {count} components{suffix}"
        )

    result = "\n".join(lines)
    if len(result) > _COMPACT_MAP_CHAR_CAP:
        result = result[: _COMPACT_MAP_CHAR_CAP - 4] + "\n..."
    return result


def locate_screen(
    prd_text: str,
    map_result: "MapResult",
    *,
    client=None,
) -> LocateResult:
    """Map a PRD to ranked screen candidates via a single LLM call.

    Returns a LocateResult on success, or LocateResult() (empty candidates) on
    any failure. Never raises — callers degrade to no-locate rather than 500.

    Parameters
    ----------
    prd_text:
        The PRD text to locate a target screen for.
    map_result:
        The codebase map containing the set of valid screen nodes.
    client:
        An Anthropic client (or any compatible object). When None, the cached
        design-agent client is used. Injecting a fake here enables unit-testing
        without network calls.
    """
    from app.design_agent.prompts import LOCATE_SYSTEM

    start_ms = time.monotonic()
    _usage: Optional[object] = None
    _status = "error"
    _error_class: Optional[str] = None
    result = LocateResult()

    try:
        if client is None:
            from app.design_agent.client import get_design_agent_client

            client = get_design_agent_client()

        from app.llm_telemetry import RunUsage

        map_text = compact_map(map_result)
        valid_routes = {node.route for node in map_result.nodes}

        # System blocks: stable prefix ends with the compact map carrying the
        # cache breakpoint. PRD is the volatile user turn — no cache_control.
        system_blocks = [
            {"type": "text", "text": LOCATE_SYSTEM},
            {
                "type": "text",
                "text": map_text,
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            },
        ]
        messages = [{"role": "user", "content": f"PRD:\n{prd_text}"}]

        resp = client.messages.create(
            model=_MODEL,
            max_tokens=_LOCATE_MAX_TOKENS,
            system=system_blocks,
            messages=messages,
        )

        _usage = RunUsage()
        _usage.add(resp.usage)

        # Extract text from the first content block.
        raw_text: str = ""
        try:
            raw_text = resp.content[0].text
        except (AttributeError, IndexError, TypeError):
            logger.warning("locate: unexpected response shape; returning empty")
            _status = "empty"
            return result

        # Strip optional ```json ... ``` fences.
        text = raw_text.strip()
        fence_match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()

        # Parse and validate via the output schema.
        parsed = json.loads(text)

        # Pre-coerce confidence to int before Pydantic validation (the model may
        # emit a float like 0.92; int(float) truncates cleanly before clamping).
        if isinstance(parsed, dict):
            for cand in parsed.get("candidates", []):
                if isinstance(cand, dict) and "confidence" in cand:
                    try:
                        cand["confidence"] = int(float(cand["confidence"]))
                    except (ValueError, TypeError):
                        cand["confidence"] = 0

        raw_result = LocateResult.model_validate(parsed)

        # Post-parse normalization.
        candidates = []
        for c in raw_result.candidates:
            # Drop hallucinated routes that don't appear in the map.
            if c.route not in valid_routes:
                continue
            # Clamp confidence to [0, 100]; coerce to int first.
            c.confidence = max(0, min(100, int(c.confidence)))
            # Clamp free-text rationale.
            if len(c.rationale) > _MAX_RATIONALE_CHARS:
                c.rationale = c.rationale[:_MAX_RATIONALE_CHARS]
            candidates.append(c)

        # Enforce the ≤3 cap even when the model returns more.
        result = LocateResult(
            candidates=candidates[:3],
            is_multi_node=raw_result.is_multi_node,
        )
        _status = "complete" if result.candidates else "empty"
        return result

    except Exception as exc:
        _error_class = type(exc).__name__
        _status = "error"
        logger.warning("locate: failed; returning empty — %r", exc)
        return result

    finally:
        duration_ms = int((time.monotonic() - start_ms) * 1000)
        try:
            from app.llm_telemetry import RunUsage, log_llm_run

            if _usage is None:
                _usage = RunUsage()
            log_llm_run(
                operation="design_agent.locate.complete",
                identifier={"repo": map_result.repo, "sha": map_result.commit_sha},
                usage=_usage,
                duration_ms=duration_ms,
                status=_status,
                model=_MODEL,
                error_class=_error_class,
                iters=1,
                n_candidates=len(result.candidates),
            )
        except Exception:
            logger.debug("locate: telemetry failed (non-fatal)", exc_info=True)


def emit_locate_telemetry(
    *,
    repo: str,
    sha: str,
    gate_result: "GateResult",
    n_candidates: int,
) -> None:
    """Emit one structured calibration line per locate request.

    Mirrors the k=v discipline of llm_telemetry.log_llm_run: identifiers only,
    no PRD body, no screen source, no rationale, no installation token.
    Emitted on every /locate request including the unmapped fail-open path
    (sha='', n_candidates=0) so the unmapped rate is observable in logs.
    """
    chosen_screen = gate_result.chosen[0].route if gate_result.chosen else ""
    leading_ranked = gate_result.ranked[0] if gate_result.ranked else None
    ambiguous = leading_ranked.ambiguous if leading_ranked is not None else False
    logger.info(
        "codebase_map.locate repo=%s sha=%s top_confidence=%d decision=%s"
        " chosen_screen=%s ambiguous=%s n_candidates=%d threshold=%d",
        repo,
        sha,
        gate_result.top_confidence,
        gate_result.decision,
        chosen_screen,
        ambiguous,
        n_candidates,
        gate_result.threshold,
    )
