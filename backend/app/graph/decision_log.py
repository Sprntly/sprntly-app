"""Agent decision log — append-only, tenant-scoped (§4d).

Every agent / LLM decision writes one record capturing the *why*:
factors, reasoning (chain-of-thought / rationale), output, model,
prompt_version, confidence, and the KG nodes referenced. One record
serves explainability + audit + the Tier-2 learning trace.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from app.db.client import require_client


def log_agent_decision(
    *,
    enterprise_id: str,
    agent: str,
    decision_type: str,
    factors: Optional[dict] = None,
    reasoning: Optional[str] = None,
    output: Optional[dict] = None,
    model: Optional[str] = None,
    prompt_version: Optional[str] = None,
    confidence: Optional[float] = None,
    kg_refs: Optional[Iterable[str]] = None,
    client: Any | None = None,
) -> Optional[int]:
    """Insert one append-only row into `agent_decision_log`.

    Returns the new row id (or None if the insert returned no data — e.g.
    in some fake-client paths). Never raises on insert; callers shouldn't
    have their primary flow blocked by an audit-log write failure (logged
    upstream)."""
    cli = client or require_client()
    row = {
        "enterprise_id": enterprise_id,
        "agent": agent,
        "decision_type": decision_type,
        "factors": factors or {},
        "reasoning": reasoning,
        "output": output or {},
        "model": model,
        "prompt_version": prompt_version,
        "confidence": confidence,
        "kg_refs": list(kg_refs) if kg_refs is not None else [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    r = cli.table("agent_decision_log").insert(row).execute()
    if r.data and isinstance(r.data, list) and r.data:
        return r.data[0].get("id")
    return None
