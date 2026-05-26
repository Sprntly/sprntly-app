"""Ask logging + cached Ask responses.

ask_log:    append-only history of every /v1/ask call.
cached_asks: pre-computed answers keyed by (dataset, question), feeds
             the warmer + answers cache hits in O(1).
"""
import json

from app.db.client import conn


# ─────────────────────── ask_log (append-only) ───────────────────────


def log_ask(question: str, answer: str, citations: list) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO ask_log (question, answer, citations_json) VALUES (?, ?, ?)",
            (question, answer, json.dumps(citations)),
        )


# ─────────────────────── cached_asks ───────────────────────


def _normalize_q(q: str) -> str:
    """Normalize a question for cache keying: strip + collapse whitespace.

    Exact-text match keyed on this normalized form. The predefined
    prompts list is constant, so this hits cleanly without any fuzzy
    matching.
    """
    return " ".join((q or "").strip().split())


def start_cached_ask(
    dataset: str, question: str, cache_version: int | None = None
) -> int:
    """Insert a stub cache row in 'generating' state; return new id."""
    with conn() as c:
        cur = c.execute(
            "INSERT INTO cached_asks (dataset, question, response_json, status, cache_version) "
            "VALUES (?, ?, '', 'generating', ?)",
            (dataset, _normalize_q(question), cache_version),
        )
        return cur.lastrowid


def complete_cached_ask(cache_id: int, response_json: str) -> None:
    with conn() as c:
        c.execute(
            "UPDATE cached_asks SET response_json=?, status='ready', error=NULL "
            "WHERE id=?",
            (response_json, cache_id),
        )


def fail_cached_ask(cache_id: int, error: str) -> None:
    with conn() as c:
        c.execute(
            "UPDATE cached_asks SET status='failed', error=? WHERE id=?",
            (error[:500], cache_id),
        )


def find_cached_ask(dataset: str, question: str) -> dict | None:
    """Most recent ready/generating cached Ask for a question."""
    with conn() as c:
        row = c.execute(
            "SELECT id, dataset, question, response_json, status, error, "
            "cache_version, generated_at FROM cached_asks "
            "WHERE dataset=? AND question=? AND status IN ('ready', 'generating') "
            "ORDER BY id DESC LIMIT 1",
            (dataset, _normalize_q(question)),
        ).fetchone()
    return dict(row) if row else None


def invalidate_stale_cached_asks(current_version: int) -> int:
    """Demote cached asks whose cache_version != current_version."""
    with conn() as c:
        cur = c.execute(
            "UPDATE cached_asks SET status='invalidated' "
            "WHERE status IN ('ready', 'generating') "
            "  AND (cache_version IS NULL OR cache_version != ?)",
            (current_version,),
        )
        return cur.rowcount or 0


def invalidate_orphan_generating_cached_asks() -> int:
    """Mark every status='generating' cached ask as 'invalidated'.

    Worker threads die with the previous process; without this, a hit on
    such a row would dedupe to a doc that will never complete.
    """
    with conn() as c:
        cur = c.execute(
            "UPDATE cached_asks SET status='invalidated' WHERE status='generating'"
        )
        return cur.rowcount or 0
