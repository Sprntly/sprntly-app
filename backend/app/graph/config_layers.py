"""4-layer config system (contract S5 / design §1c).

Resolution = deep-merge of three config layers (later wins):
    1. PLATFORM_DEFAULTS         — global, versioned in code (this file)
    2. SOURCE_TYPE_CONFIG[st]    — per-source_type adjustments, shared globally
    3. enterprise overrides      — optional per-enterprise rows in
                                   `enterprise_config` (written via Settings)
Layer 4 — per-enterprise *learned state* (scoring profile, signal weights,
enterprise/PM model) — is NOT config: it lives in the KG and is read by the
agents directly.

Onboarding a new enterprise touches no code and, by default, no config:
defaults + self-calibration + learned state cover it (§1c).
"""
from __future__ import annotations

import copy
import logging
from typing import Any, Optional

from app.graph.types import SOURCE_STALE_WINDOW_DAYS

logger = logging.getLogger(__name__)

CONFIG_VERSION = 1

# ---- Layer 1: platform defaults (S5 namespaces) -----------------------------
PLATFORM_DEFAULTS: dict[str, Any] = {
    "resolution": {
        "tau_high": 0.86,          # ≥ → same node
        "tau_low": 0.72,           # < → new node; between → LLM adjudication
        "adjudication": "llm",
        "consolidation_sweep": {"enabled": True},
    },
    "staleness": {
        # windows (days) per source_type; None ⇒ never expires (#1)
        "windows_days": dict(SOURCE_STALE_WINDOW_DAYS),
        "decay": {"mode": "half_life"},
    },
    "oncall": {
        "trigger": {"metric_zscore": 3.0, "pct_drop": 0.20},
        "cooldown_hours": 24,
        "cold_start": "absolute_defaults",
    },
    "feedback": {
        "dismissal_taxonomy": [
            "right_problem_wrong_time",
            "not_a_priority",
            "already_known",
            "disagree_with_evidence",
            "wrong_framing",
        ],
        "ignored_after_days": 21,
        "attribution": {"sim_threshold": 0.75, "window_days": 14},
        "learning_rate": None,  # TBD — tuned in Phase 3
    },
    "research": {
        # Social/community channels the Market Research agent sweeps with
        # targeted site: queries. Per-enterprise overridable (Settings) —
        # e.g. a healthcare company might drop reddit and add specialty forums.
        "social_sources": [
            {"id": "reddit",      "query": "site:reddit.com {subject} (also search relevant subreddits for the product category)"},
            {"id": "hackernews",  "query": "site:news.ycombinator.com {subject}"},
            {"id": "linkedin",    "query": "site:linkedin.com {subject} (public posts; coverage is partial — LinkedIn is login-walled)"},
            {"id": "g2",          "query": "site:g2.com OR site:capterra.com {subject} reviews"},
        ],
        "max_searches": 12,
    },
    "scoring": {
        "dimensions": [
            "kpi_impact", "strategic_alignment", "convergence",
            "revenue_at_stake", "competitive_pressure", "reliability_risk",
            "confidence",
        ],
        # Goal-alignment factor (prioritize skill goal mode). When enabled, each
        # theme's base score is multiplied by a deterministic KPI-fit factor
        # before the Synthesis judge re-ranks. `goal_weight` blends the factor
        # toward 1.0 (1 = full effect, 0 = goal ignored) — skill default 1.0.
        "goal_factor_enabled": True,
        "goal_weight": 1.0,
    },
    "llm": {
        "default_model": "claude-sonnet-4-6",
        "embedding_provider": "openai",
        "embedding_model": "text-embedding-3-small",
        "embedding_dimensions": 1536,
        "cache": True,
    },
    "outcome": {"measurement_windows_days": [7, 14, 30]},
    "ds": {
        # Pilot-1 structured analyses. Anomaly = a weekly metric point that
        # deviates from its own trailing history. A point is a Finding if EITHER
        # the z-score magnitude or the pct-change magnitude clears its threshold.
        "anomaly": {
            "min_points": 4,      # need ≥ this many weekly points to judge a metric
            "z_threshold": 2.0,   # |z| ≥ this ⇒ anomaly (z vs trailing mean/std)
            "pct_threshold": 0.3, # |pct change vs trailing mean| ≥ this ⇒ anomaly
        },
    },
}

# ---- Layer 2: per-source_type config ----------------------------------------
# Sparse: only where a source_type deviates from platform defaults (e.g. an
# extraction profile name, rate limits). Keyed by source_type.
SOURCE_TYPE_CONFIG: dict[str, dict[str, Any]] = {
    # "analytics": {"extraction": {"profile": "metric_series_v1"}},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _enterprise_overrides(enterprise_id: str) -> dict:
    """Layer 3 — optional per-enterprise rows from `enterprise_config`.
    Missing table/row ⇒ {} (most enterprises never override anything)."""
    try:
        from app.db.client import require_client

        r = (
            require_client().table("enterprise_config")
            .select("overrides")
            .eq("enterprise_id", enterprise_id)
            .execute()
        )
        if r.data:
            return r.data[0].get("overrides") or {}
    except Exception:  # noqa: BLE001 — config reads must never break a request
        logger.warning("enterprise_config lookup failed; using defaults", exc_info=True)
    return {}


def resolve_config(
    enterprise_id: Optional[str] = None,
    source_type: Optional[str] = None,
) -> dict:
    """Merged view: platform ← source_type ← enterprise overrides."""
    cfg = copy.deepcopy(PLATFORM_DEFAULTS)
    if source_type and source_type in SOURCE_TYPE_CONFIG:
        cfg = _deep_merge(cfg, SOURCE_TYPE_CONFIG[source_type])
    if enterprise_id:
        cfg = _deep_merge(cfg, _enterprise_overrides(enterprise_id))
    return cfg


def config_get(path: str, enterprise_id: Optional[str] = None,
               source_type: Optional[str] = None, default: Any = None) -> Any:
    """Dotted-path getter: config_get("oncall.trigger.metric_zscore", eid)."""
    node: Any = resolve_config(enterprise_id, source_type)
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node
