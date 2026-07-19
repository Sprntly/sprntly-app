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
    complete_prd_2part,
    fail_prd,
    find_existing_prd,
    get_prd,
    get_prd_rendered,
    invalidate_orphan_generating_prds,
    invalidate_stale_prds,
    prd_source_hash,
    save_prd,
    set_prd_impl_spec,
    start_prd,
)

# Evidence pages
from app.db.evidences import (
    complete_evidence,
    fail_evidence,
    find_existing_evidence,
    find_latest_failed_evidence,
    get_evidence,
    invalidate_orphan_generating_evidences,
    invalidate_stale_evidences,
    start_evidence,
)

# Asks (log + cache)
from app.db.asks import (
    cancel_ask_job,
    complete_ask_job,
    complete_cached_ask,
    fail_ask_job,
    fail_cached_ask,
    find_cached_ask,
    get_ask_job,
    invalidate_orphan_generating_cached_asks,
    invalidate_stale_cached_asks,
    is_ask_cancelled,
    log_ask,
    start_ask_job,
    start_cached_ask,
)

# Website-analysis jobs (onboarding, fire-and-forget)
from app.db.website_analysis import (
    complete_analysis_job,
    fail_analysis_job,
    get_analysis_job,
    start_analysis_job,
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
    delete_slack_connection,
    get_connection,
    get_slack_connection,
    list_all_active_connections,
    list_connections,
    list_slack_connections,
    list_slack_connections_by_team,
    patch_connection_config,
    patch_slack_connection_config,
    set_connection_health,
    update_connection_sync,
    update_connection_tokens,
    update_slack_connection_sync,
    upsert_connection,
    upsert_slack_connection,
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
    find_github_installation_for_repo,
    get_github_installation,
    get_github_installation_for_company,
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
    "complete_prd_2part",
    "fail_prd",
    "find_existing_prd",
    "get_prd",
    "get_prd_rendered",
    "invalidate_orphan_generating_prds",
    "invalidate_stale_prds",
    "prd_source_hash",
    "save_prd",
    "set_prd_impl_spec",
    "start_prd",
    # evidences
    "complete_evidence",
    "fail_evidence",
    "find_existing_evidence",
    "find_latest_failed_evidence",
    "get_evidence",
    "invalidate_orphan_generating_evidences",
    "invalidate_stale_evidences",
    "start_evidence",
    # asks
    "cancel_ask_job",
    "complete_ask_job",
    "complete_cached_ask",
    "fail_ask_job",
    "fail_cached_ask",
    "find_cached_ask",
    "get_ask_job",
    "invalidate_orphan_generating_cached_asks",
    "invalidate_stale_cached_asks",
    "is_ask_cancelled",
    "log_ask",
    "start_ask_job",
    "start_cached_ask",
    # website-analysis jobs
    "complete_analysis_job",
    "fail_analysis_job",
    "get_analysis_job",
    "start_analysis_job",
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
    "delete_slack_connection",
    "get_connection",
    "get_slack_connection",
    "list_all_active_connections",
    "list_connections",
    "list_slack_connections",
    "list_slack_connections_by_team",
    "patch_connection_config",
    "patch_slack_connection_config",
    "set_connection_health",
    "update_connection_sync",
    "update_connection_tokens",
    "update_slack_connection_sync",
    "upsert_connection",
    "upsert_slack_connection",
    # metric points (DS rolling aggregates)
    "distinct_metrics",
    "list_metric_points",
    "upsert_metric_point",
    # design systems cache
    "mark_github_design_systems_stale",
    # github (webhook-driven)
    "delete_github_installation",
    "find_github_installation_for_repo",
    "get_github_installation",
    "get_github_installation_for_company",
    "list_github_installations",
    "list_open_pull_requests",
    "upsert_github_installation",
    "upsert_github_pull_request",
]
