"""SQLite store for the demo. One brief at a time per dataset; one Q&A history per session."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS briefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset TEXT NOT NULL,
    generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    week_label TEXT,
    payload_json TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS briefs_dataset_current
    ON briefs(dataset, is_current);

CREATE TABLE IF NOT EXISTS prds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id INTEGER NOT NULL,
    insight_index INTEGER NOT NULL,
    generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    title TEXT NOT NULL,
    payload_md TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ready',
    error TEXT,
    template_version INTEGER,
    FOREIGN KEY (brief_id) REFERENCES briefs(id)
);

CREATE TABLE IF NOT EXISTS ask_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    citations_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cached_asks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset TEXT NOT NULL,
    question TEXT NOT NULL,
    response_json TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'generating',
    error TEXT,
    cache_version INTEGER,
    generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS cached_asks_dataset_question
    ON cached_asks(dataset, question, status);

CREATE TABLE IF NOT EXISTS evidences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id INTEGER NOT NULL,
    insight_index INTEGER NOT NULL,
    generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    title TEXT NOT NULL,
    payload_md TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'generating',
    error TEXT,
    template_version INTEGER,
    FOREIGN KEY (brief_id) REFERENCES briefs(id)
);
"""


def init_db() -> None:
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    with conn() as c:
        c.executescript(SCHEMA)
        # Idempotent migrations for boxes that already have the prds table
        cols = {row[1] for row in c.execute("PRAGMA table_info(prds)").fetchall()}
        if "status" not in cols:
            c.execute("ALTER TABLE prds ADD COLUMN status TEXT NOT NULL DEFAULT 'ready'")
        if "error" not in cols:
            c.execute("ALTER TABLE prds ADD COLUMN error TEXT")
        if "template_version" not in cols:
            c.execute("ALTER TABLE prds ADD COLUMN template_version INTEGER")
        # Same for evidences (template_version was added later).
        ev_cols = {row[1] for row in c.execute("PRAGMA table_info(evidences)").fetchall()}
        if ev_cols and "template_version" not in ev_cols:
            c.execute("ALTER TABLE evidences ADD COLUMN template_version INTEGER")


@contextmanager
def conn():
    c = sqlite3.connect(settings.db_path)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def get_current_brief(dataset: str = "asurion") -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT id, dataset, generated_at, week_label, payload_json "
            "FROM briefs WHERE dataset=? AND is_current=1 "
            "ORDER BY generated_at DESC LIMIT 1",
            (dataset,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "dataset": row["dataset"],
        "generated_at": row["generated_at"],
        "week_label": row["week_label"],
        **json.loads(row["payload_json"]),
    }


def get_brief_by_id(brief_id: int) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT id, dataset, generated_at, week_label, payload_json "
            "FROM briefs WHERE id=?",
            (brief_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "dataset": row["dataset"],
        "generated_at": row["generated_at"],
        "week_label": row["week_label"],
        **json.loads(row["payload_json"]),
    }


def save_brief(
    dataset: str,
    week_label: str,
    payload: dict,
    schema_version: int | None = None,
) -> int:
    if schema_version is not None:
        payload = {**payload, "_schema_version": schema_version}
    with conn() as c:
        c.execute(
            "UPDATE briefs SET is_current=0 WHERE dataset=?", (dataset,)
        )
        cur = c.execute(
            "INSERT INTO briefs (dataset, week_label, payload_json, is_current) "
            "VALUES (?, ?, ?, 1)",
            (dataset, week_label, json.dumps(payload)),
        )
        return cur.lastrowid


def invalidate_stale_briefs(current_version: int) -> int:
    """Demote any `is_current=1` brief whose `_schema_version` differs from
    `current_version`. Returns the number of rows invalidated.

    Called on service startup so a schema bump triggers auto-regeneration
    without manual /v1/brief/regenerate calls or DB surgery.
    """
    invalidated = 0
    with conn() as c:
        rows = c.execute(
            "SELECT id, payload_json FROM briefs WHERE is_current=1"
        ).fetchall()
        stale_ids: list[int] = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, ValueError):
                payload = {}
            if payload.get("_schema_version") != current_version:
                stale_ids.append(row["id"])
        if stale_ids:
            placeholders = ",".join("?" for _ in stale_ids)
            c.execute(
                f"UPDATE briefs SET is_current=0 WHERE id IN ({placeholders})",
                stale_ids,
            )
            invalidated = len(stale_ids)
    return invalidated


def save_prd(brief_id: int, insight_index: int, title: str, md: str) -> int:
    """Insert a complete PRD (sync flow). Status='ready'."""
    with conn() as c:
        cur = c.execute(
            "INSERT INTO prds (brief_id, insight_index, title, payload_md, status) "
            "VALUES (?, ?, ?, ?, 'ready')",
            (brief_id, insight_index, title, md),
        )
        return cur.lastrowid


def start_prd(
    brief_id: int,
    insight_index: int,
    title: str,
    template_version: int | None = None,
) -> int:
    """Insert an empty PRD row in 'generating' state. Returns the new id."""
    with conn() as c:
        cur = c.execute(
            "INSERT INTO prds (brief_id, insight_index, title, payload_md, status, template_version) "
            "VALUES (?, ?, ?, '', 'generating', ?)",
            (brief_id, insight_index, title, template_version),
        )
        return cur.lastrowid


def invalidate_stale_prds(current_version: int) -> int:
    """Mark any ready/generating PRD whose `template_version` differs from
    `current_version` as 'invalidated'. Returns the number of rows.

    `find_existing_prd` only returns ready/generating rows, so invalidated
    PRDs are skipped — the next click regenerates them under the current
    prompt.
    """
    with conn() as c:
        cur = c.execute(
            "UPDATE prds SET status='invalidated' "
            "WHERE status IN ('ready', 'generating') "
            "  AND (template_version IS NULL OR template_version != ?)",
            (current_version,),
        )
        return cur.rowcount or 0


def invalidate_orphan_generating_prds() -> int:
    """Mark every status='generating' PRD as 'invalidated'.

    Call this from lifespan startup: any in-flight row is by definition
    orphaned (the worker thread that was generating it died with the
    previous process). Leaving them stuck causes user clicks to dedupe to
    a row that will never complete; this clears them so the next warming
    tick regenerates fresh.
    """
    with conn() as c:
        cur = c.execute(
            "UPDATE prds SET status='invalidated' WHERE status='generating'"
        )
        return cur.rowcount or 0


def complete_prd(prd_id: int, title: str, md: str) -> None:
    with conn() as c:
        c.execute(
            "UPDATE prds SET title=?, payload_md=?, status='ready', error=NULL "
            "WHERE id=?",
            (title, md, prd_id),
        )


def fail_prd(prd_id: int, error: str) -> None:
    with conn() as c:
        c.execute(
            "UPDATE prds SET status='failed', error=? WHERE id=?",
            (error[:500], prd_id),
        )


def get_prd(prd_id: int) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT id, brief_id, insight_index, generated_at, title, payload_md, "
            "status, error, template_version FROM prds WHERE id=?",
            (prd_id,),
        ).fetchone()
    return dict(row) if row else None


def find_existing_prd(brief_id: int, insight_index: int) -> dict | None:
    """Return the most recent ready/generating PRD for a (brief, insight)."""
    with conn() as c:
        row = c.execute(
            "SELECT id, brief_id, insight_index, generated_at, title, payload_md, "
            "status, error, template_version FROM prds "
            "WHERE brief_id=? AND insight_index=? AND status IN ('ready','generating') "
            "ORDER BY id DESC LIMIT 1",
            (brief_id, insight_index),
        ).fetchone()
    return dict(row) if row else None


def log_ask(question: str, answer: str, citations: list) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO ask_log (question, answer, citations_json) VALUES (?, ?, ?)",
            (question, answer, json.dumps(citations)),
        )


# ----- Evidence pages ---------------------------------------------------------

def start_evidence(
    brief_id: int,
    insight_index: int,
    title: str,
    template_version: int | None = None,
) -> int:
    """Insert an empty evidence row in 'generating' state. Returns the new id."""
    with conn() as c:
        cur = c.execute(
            "INSERT INTO evidences (brief_id, insight_index, title, payload_md, status, template_version) "
            "VALUES (?, ?, ?, '', 'generating', ?)",
            (brief_id, insight_index, title, template_version),
        )
        return cur.lastrowid


def invalidate_stale_evidences(current_version: int) -> int:
    """Mark any ready/generating evidence whose `template_version` differs
    from `current_version` as 'invalidated'. Returns the number of rows.

    `find_existing_evidence` only returns ready/generating rows, so
    invalidated docs are skipped — the next view triggers a fresh
    generation under the current prompt.
    """
    with conn() as c:
        cur = c.execute(
            "UPDATE evidences SET status='invalidated' "
            "WHERE status IN ('ready', 'generating') "
            "  AND (template_version IS NULL OR template_version != ?)",
            (current_version,),
        )
        return cur.rowcount or 0


def invalidate_orphan_generating_evidences() -> int:
    """Mark every status='generating' evidence row as 'invalidated'.

    Same rationale as invalidate_orphan_generating_prds — on startup, any
    in-flight generation is orphaned because the worker thread died with
    the previous process. Without this, a user clicking "View evidence"
    on an insight whose previous warming crashed mid-generation polls
    forever.
    """
    with conn() as c:
        cur = c.execute(
            "UPDATE evidences SET status='invalidated' WHERE status='generating'"
        )
        return cur.rowcount or 0


def complete_evidence(evidence_id: int, title: str, md: str) -> None:
    with conn() as c:
        c.execute(
            "UPDATE evidences SET title=?, payload_md=?, status='ready', error=NULL "
            "WHERE id=?",
            (title, md, evidence_id),
        )


def fail_evidence(evidence_id: int, error: str) -> None:
    with conn() as c:
        c.execute(
            "UPDATE evidences SET status='failed', error=? WHERE id=?",
            (error[:500], evidence_id),
        )


def get_evidence(evidence_id: int) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT id, brief_id, insight_index, generated_at, title, payload_md, "
            "status, error, template_version FROM evidences WHERE id=?",
            (evidence_id,),
        ).fetchone()
    return dict(row) if row else None


def find_existing_evidence(brief_id: int, insight_index: int) -> dict | None:
    """Return the most recent ready/generating evidence for a (brief, insight)."""
    with conn() as c:
        row = c.execute(
            "SELECT id, brief_id, insight_index, generated_at, title, payload_md, "
            "status, error, template_version FROM evidences "
            "WHERE brief_id=? AND insight_index=? AND status IN ('ready','generating') "
            "ORDER BY id DESC LIMIT 1",
            (brief_id, insight_index),
        ).fetchone()
    return dict(row) if row else None


# ----- Cached Ask responses ---------------------------------------------------

def _normalize_q(q: str) -> str:
    """Normalize a question for cache keying: strip + collapse whitespace.

    Exact-text match keyed on this normalized form. The predefined prompts
    list is constant, so this hits cleanly without any fuzzy match.
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
    """Return the most recent ready/generating cached Ask for a question."""
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
