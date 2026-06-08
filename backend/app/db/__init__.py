"""SQLite store, organized by domain.

The package replaces the monolithic backend/app/db.py. Public API is
preserved: anywhere that previously did `from app import db;
db.get_current_brief(...)` or `from app.db import save_brief` continues
to work because every function is re-exported here.

Submodule layout:
  client.py       — sqlite3 conn() context manager + utc_now timestamp
  schema.py       — CREATE TABLE DDL + idempotent ALTERs in init_db()
  briefs.py       — weekly briefs (is_current row per dataset)
  prds.py         — PRDs (generating → ready, variant-scoped)
  evidences.py    — evidence pages (same shape, different lifecycle)
  asks.py         — ask_log (append-only) + cached_asks
  datasets.py     — dataset slug registry
  connections.py  — OAuth tokens (Google / Figma / GitHub)

Next phase will add a Supabase client alongside the sqlite one in
client.py; per-domain submodules will gain dual-write helpers behind a
flag without changing the public API.
"""
# Timestamp helper + Supabase client accessor
from app.db.client import supabase_client, utc_now, _utc_now

# Schema bootstrap (no-op shim after the Supabase cutover)
from app.db.schema import SCHEMA, init_db

# Briefs
from app.db.briefs import (
    get_brief_by_id,
    get_current_brief,
    invalidate_stale_briefs,
    save_brief,
)

# PRDs
from app.db.prds import (
    complete_prd,
    fail_prd,
    find_existing_prd,
    get_prd,
    get_prd_rendered,
    invalidate_orphan_generating_prds,
    invalidate_stale_prds,
    save_prd,
    start_prd,
)

# Evidence pages
from app.db.evidences import (
    complete_evidence,
    fail_evidence,
    find_existing_evidence,
    get_evidence,
    invalidate_orphan_generating_evidences,
    invalidate_stale_evidences,
    start_evidence,
)

# Asks (log + cache)
from app.db.asks import (
    complete_cached_ask,
    fail_cached_ask,
    find_cached_ask,
    invalidate_orphan_generating_cached_asks,
    invalidate_stale_cached_asks,
    log_ask,
    start_cached_ask,
)

# Datasets
from app.db.datasets import (
    dataset_exists,
    delete_dataset,
    get_dataset,
    insert_dataset,
    list_dataset_slugs,
    list_datasets,
)

# Connections (OAuth)
from app.db.connections import (
    delete_connection,
    get_connection,
    list_connections,
    patch_connection_config,
    update_connection_sync,
    update_connection_tokens,
    upsert_connection,
)

# Enterprise input sources
from app.db.input_sources import (
    delete_input_source,
    list_input_sources,
    upsert_input_source,
)

# Knowledge graph
from app.db.knowledge import (
    clear_entities,
    list_entities,
    list_relationships,
    upsert_entity,
    upsert_relationship,
)

# Pipeline runs
from app.db.pipeline_runs import (
    complete_run,
    create_run,
    fail_run,
    get_latest_run,
    list_runs,
    update_run_stage,
)

# Metric points (DS rolling aggregates)
from app.db.metric_points import (
    distinct_metrics,
    list_metric_points,
    upsert_metric_point,
)

from app.db.design_systems import mark_github_design_systems_stale

# GitHub App (webhook-driven)
from app.db.github import (
    delete_github_installation,
    get_github_installation,
    list_github_installations,
    list_open_pull_requests,
    upsert_github_installation,
    upsert_github_pull_request,
)

__all__ = [
    # client
    "supabase_client",
    "utc_now",
    "_utc_now",
    # schema
    "SCHEMA",
    "init_db",
    # briefs
    "get_brief_by_id",
    "get_current_brief",
    "invalidate_stale_briefs",
    "save_brief",
    # prds
    "complete_prd",
    "fail_prd",
    "find_existing_prd",
    "get_prd",
    "get_prd_rendered",
    "invalidate_orphan_generating_prds",
    "invalidate_stale_prds",
    "save_prd",
    "start_prd",
    # evidences
    "complete_evidence",
    "fail_evidence",
    "find_existing_evidence",
    "get_evidence",
    "invalidate_orphan_generating_evidences",
    "invalidate_stale_evidences",
    "start_evidence",
    # asks
    "complete_cached_ask",
    "fail_cached_ask",
    "find_cached_ask",
    "invalidate_orphan_generating_cached_asks",
    "invalidate_stale_cached_asks",
    "log_ask",
    "start_cached_ask",
    # datasets
    "dataset_exists",
    "delete_dataset",
    "get_dataset",
    "insert_dataset",
    "list_dataset_slugs",
    "list_datasets",
    # input sources
    "delete_input_source",
    "list_input_sources",
    "upsert_input_source",
    # connections
    "delete_connection",
    "get_connection",
    "list_connections",
    "patch_connection_config",
    "update_connection_sync",
    "update_connection_tokens",
    "upsert_connection",
    # metric points (DS rolling aggregates)
    "distinct_metrics",
    "list_metric_points",
    "upsert_metric_point",
    # design systems cache
    "mark_github_design_systems_stale",
    # github (webhook-driven)
    "delete_github_installation",
    "get_github_installation",
    "list_github_installations",
    "list_open_pull_requests",
    "upsert_github_installation",
    "upsert_github_pull_request",
]
