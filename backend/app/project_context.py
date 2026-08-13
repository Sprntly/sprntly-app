"""Individual project chat — bounded context assembly (build spec AD-P8).

A project-scoped `/v1/ask` turn (`routes/ask.py`, `project_id` set) folds in
the project's own memory rather than the enterprise knowledge graph:
`project_memory_summary.summary_md` + the top-N `project_memory_entries`
(recency-ordered) + the caller's `profiles.role` (their own job
designation, AD-P5 — aliased `job_role` here to avoid colliding with the
permission `role` a caller's company/workspace membership also carries).

This reuses the bounded-assembly PATTERN `graph/retrieval.py::retrieve_context`
already uses for KG context — approximate the block's size in tokens
(chars-per-token, no tokenizer dependency) and stop adding pieces once a
budget is spent — but reads ONLY `project_memory_summary` /
`project_memory_entries` / `profiles.role`. It never queries
`kg_entity`/`kg_signal`/`kg_relationship`: those are enterprise-scoped
(RLS, pgvector indexes, per-company) and project memory is deliberately a
separate, smaller store (AD-P8; resolves build-spec open-question #4).

This module does the ASSEMBLY, not the swallowing: `assemble_project_context`
may raise on a genuine read error, and the caller (`routes/ask.py`) is what
applies the best-effort fallback (AD-P7 posture — a folding failure must
degrade to "no project block", never block the answer).
"""
from __future__ import annotations

from app.db.client import require_client
from app.db.project_memory_entries import get_summary, list_entries

# Rough chars-per-token, same approximation `graph/retrieval.py` uses (no
# tokenizer dependency — the cap is a soft guardrail, not exact accounting).
_CHARS_PER_TOKEN = 4

# Default token budget for the whole project-context block. Sits well under
# the answer call's max_tokens, the same relationship
# `graph.retrieval.DEFAULT_TOKEN_BUDGET` (2200) has to its own caller.
DEFAULT_TOKEN_BUDGET = 1200

# How many of the most-recently-updated memory entries to consider before
# the budget cap even applies — a further bound on top of the byte cap so a
# project with hundreds of entries doesn't pay to enumerate all of them.
TOP_N_ENTRIES = 20


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _caller_job_role(user_id: str) -> str | None:
    """The caller's own job designation (`profiles.role`, AD-P5) — best-effort
    at the DB layer like every other single-row profile read in this
    codebase; returns None on no row rather than raising, so a caller with
    no profile row yet still gets a project block (summary + entries only)."""
    rows = (
        require_client()
        .table("profiles")
        .select("role")
        .eq("id", user_id)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0].get("role") if rows else None


def assemble_project_context(
    project_id: int,
    user_id: str,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> str:
    """A bounded project-context block: memory summary + top-N entries
    (recency-ordered) + the caller's job_role, capped to `token_budget`.

    Returns "" when the project has no summary/entries/resolvable job_role
    (an empty/new project) — never None, so callers can treat the result as
    directly foldable. Pieces are added summary-first, then entries
    newest-first, then job_role last; once a piece would push the running
    total over budget it (and everything after it) is left out — the
    over-budget CASE truncates the entry set, it does not drop the whole
    block (build spec AC5)."""
    parts: list[str] = []
    used = 0

    summary = get_summary(project_id) or {}
    summary_md = (summary.get("summary_md") or "").strip()
    if summary_md:
        piece = f"Project memory summary:\n{summary_md}"
        cost = _approx_tokens(piece)
        if used + cost <= token_budget:
            parts.append(piece)
            used += cost

    entry_lines: list[str] = []
    for entry in list_entries(project_id)[:TOP_N_ENTRIES]:
        body = (entry.get("body") or "").strip()
        if not body:
            continue
        line = f"- {body}"
        cost = _approx_tokens(line)
        if used + cost > token_budget:
            break
        entry_lines.append(line)
        used += cost
    if entry_lines:
        parts.append(
            "Project memory entries (most recent first):\n" + "\n".join(entry_lines)
        )

    job_role = _caller_job_role(user_id)
    if job_role:
        piece = f"The caller's role: {job_role}"
        cost = _approx_tokens(piece)
        if used + cost <= token_budget:
            parts.append(piece)
            used += cost

    return "\n\n".join(parts)
