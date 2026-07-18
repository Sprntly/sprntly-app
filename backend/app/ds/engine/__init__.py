"""Vendored Sprntly DS Agent engine (prototype v5.8, July 2026).

Source: sprntly-ds-agent-v5_4-3.zip — the deterministic EDA engine: semantic
ingestion layer, 14-primitive battery, trust pipeline (leakage filter,
split-half replication, supersede/dedup, model-based shadow removal), and the
v5.5–v5.8 additive capabilities (analysis router, trend/bucket/multi-numerator/
cross-table/lagged scans, text/meaning layer). Point `run()` at a directory of
CSV exports and it returns four separated channels: MEASURED findings,
directional leads, null results, and quarantine — every claim replication-gated
with cohort-as-code. No LLM computes a number anywhere in this package.

Local changes vs upstream (keep this list current):
  * flat cross-module imports rewritten package-relative
  * llm_labeler.active_labeler() pinned to the offline discovery labeler —
    the direct-Anthropic-API path bypasses the LLM gateway (BYOK/telemetry);
    route it through app.graph.gateway.llm_call before re-enabling.

Entry point for the app: `app.ds.chat_analysis` (chat command adapter). The
upstream benchmark suites / registry-feedback workstream docs live in the zip,
not here.
"""
from .ds_agent import run

__all__ = ["run"]
