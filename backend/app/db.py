"""SQLite store for the demo. One brief at a time per dataset; one Q&A history per session."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
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
    variant TEXT NOT NULL DEFAULT 'v1',
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
    variant TEXT NOT NULL DEFAULT 'v1',
    FOREIGN KEY (brief_id) REFERENCES briefs(id)
);

CREATE TABLE IF NOT EXISTS datasets (
    slug TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS connections (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active',
    google_email TEXT,
    scopes TEXT NOT NULL DEFAULT '',
    token_json_encrypted TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    last_sync_at TEXT,
    last_sync_error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
        # variant column added with the v2 sample-build; existing rows are v1.
        if "variant" not in cols:
            c.execute(
                "ALTER TABLE prds ADD COLUMN variant TEXT NOT NULL DEFAULT 'v1'"
            )
        # Same for evidences (template_version was added later).
        ev_cols = {row[1] for row in c.execute("PRAGMA table_info(evidences)").fetchall()}
        if ev_cols and "template_version" not in ev_cols:
            c.execute("ALTER TABLE evidences ADD COLUMN template_version INTEGER")
        # variant column added with the v2 sample-build; existing rows are v1.
        if ev_cols and "variant" not in ev_cols:
            c.execute(
                "ALTER TABLE evidences ADD COLUMN variant TEXT NOT NULL DEFAULT 'v1'"
            )


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
    variant: str = "v1",
) -> int:
    """Insert an empty PRD row in 'generating' state. Returns the new id.

    `variant` is kept as a kwarg (default 'v1' for backward-compat with
    the DB column) for cross-format isolation if we ever add a future
    format. Current callers pass 'v2'.
    """
    with conn() as c:
        cur = c.execute(
            "INSERT INTO prds (brief_id, insight_index, title, payload_md, status, template_version, variant) "
            "VALUES (?, ?, ?, '', 'generating', ?, ?)",
            (brief_id, insight_index, title, template_version, variant),
        )
        return cur.lastrowid


def invalidate_stale_prds(current_version: int, variant: str = "v1") -> int:
    """Mark any ready/generating PRD (of the given variant) whose
    `template_version` differs from `current_version` as 'invalidated'.
    Returns the number of rows.

    Variant-scoped so a bump on one PRD format doesn't invalidate rows
    of another. `find_existing_prd` only returns ready/generating rows,
    so invalidated PRDs are skipped — the next click regenerates them
    under the current prompt.
    """
    with conn() as c:
        cur = c.execute(
            "UPDATE prds SET status='invalidated' "
            "WHERE status IN ('ready', 'generating') "
            "  AND variant = ? "
            "  AND (template_version IS NULL OR template_version != ?)",
            (variant, current_version),
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
            "status, error, template_version, variant FROM prds WHERE id=?",
            (prd_id,),
        ).fetchone()
    return dict(row) if row else None


def find_existing_prd(
    brief_id: int, insight_index: int, variant: str = "v1"
) -> dict | None:
    """Return the most recent ready/generating PRD (of the given variant) for
    a (brief, insight). Variant-scoped so distinct PRD formats don't
    dedupe against each other.
    """
    with conn() as c:
        row = c.execute(
            "SELECT id, brief_id, insight_index, generated_at, title, payload_md, "
            "status, error, template_version, variant FROM prds "
            "WHERE brief_id=? AND insight_index=? AND variant=? "
            "  AND status IN ('ready','generating') "
            "ORDER BY id DESC LIMIT 1",
            (brief_id, insight_index, variant),
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
    variant: str = "v1",
) -> int:
    """Insert an empty evidence row in 'generating' state. Returns the new id."""
    with conn() as c:
        cur = c.execute(
            "INSERT INTO evidences (brief_id, insight_index, title, payload_md, status, template_version, variant) "
            "VALUES (?, ?, ?, '', 'generating', ?, ?)",
            (brief_id, insight_index, title, template_version, variant),
        )
        return cur.lastrowid


def invalidate_stale_evidences(current_version: int, variant: str = "v1") -> int:
    """Mark any ready/generating evidence (of the given variant) whose
    `template_version` differs from `current_version` as 'invalidated'.
    Returns the number of rows.

    Variant-scoped so a v1 template bump doesn't invalidate v2 rows and
    vice versa. `find_existing_evidence` only returns ready/generating
    rows, so invalidated docs are skipped — the next view triggers a
    fresh generation under the current prompt.
    """
    with conn() as c:
        cur = c.execute(
            "UPDATE evidences SET status='invalidated' "
            "WHERE status IN ('ready', 'generating') "
            "  AND variant = ? "
            "  AND (template_version IS NULL OR template_version != ?)",
            (variant, current_version),
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
            "status, error, template_version, variant FROM evidences WHERE id=?",
            (evidence_id,),
        ).fetchone()
    return dict(row) if row else None


def find_existing_evidence(
    brief_id: int, insight_index: int, variant: str = "v1"
) -> dict | None:
    """Return the most recent ready/generating evidence (of the given variant)
    for a (brief, insight). Variant-scoped so v1 and v2 generation paths
    don't dedupe against each other.
    """
    with conn() as c:
        row = c.execute(
            "SELECT id, brief_id, insight_index, generated_at, title, payload_md, "
            "status, error, template_version, variant FROM evidences "
            "WHERE brief_id=? AND insight_index=? AND variant=? "
            "  AND status IN ('ready','generating') "
            "ORDER BY id DESC LIMIT 1",
            (brief_id, insight_index, variant),
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


# ----- Datasets ---------------------------------------------------------------

def insert_dataset(slug: str, display_name: str) -> None:
    """Register a new dataset. Idempotent — silently ignores duplicates so the
    seed-on-startup path for `asurion` doesn't have to special-case re-runs.
    """
    with conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO datasets (slug, display_name) VALUES (?, ?)",
            (slug, display_name),
        )


def dataset_exists(slug: str) -> bool:
    with conn() as c:
        row = c.execute(
            "SELECT 1 FROM datasets WHERE slug=?", (slug,)
        ).fetchone()
    return row is not None


def get_dataset(slug: str) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT slug, display_name, created_at FROM datasets WHERE slug=?",
            (slug,),
        ).fetchone()
    return dict(row) if row else None


def list_datasets() -> list[dict]:
    """All datasets, newest first."""
    with conn() as c:
        rows = c.execute(
            "SELECT slug, display_name, created_at FROM datasets "
            "ORDER BY created_at DESC, slug ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def list_dataset_slugs() -> list[str]:
    with conn() as c:
        rows = c.execute("SELECT slug FROM datasets ORDER BY slug ASC").fetchall()
    return [r["slug"] for r in rows]


# ----- Connections (OAuth integrations) ---------------------------------------

def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def upsert_connection(
    *,
    provider: str,
    token_encrypted: str,
    scopes: str,
    google_email: str | None = None,
    config_json: str = "{}",
    status: str = "active",
) -> dict:
    now = _utc_now()
    with conn() as c:
        existing = c.execute(
            "SELECT id FROM connections WHERE provider=?", (provider,)
        ).fetchone()
        if existing:
            c.execute(
                "UPDATE connections SET status=?, google_email=?, scopes=?, "
                "token_json_encrypted=?, config_json=?, last_sync_error=NULL, updated_at=? "
                "WHERE provider=?",
                (
                    status,
                    google_email,
                    scopes,
                    token_encrypted,
                    config_json,
                    now,
                    provider,
                ),
            )
            row_id = existing["id"]
        else:
            row_id = uuid.uuid4().hex
            c.execute(
                "INSERT INTO connections "
                "(id, provider, status, google_email, scopes, token_json_encrypted, "
                "config_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row_id,
                    provider,
                    status,
                    google_email,
                    scopes,
                    token_encrypted,
                    config_json,
                    now,
                    now,
                ),
            )
    row = get_connection(provider)
    assert row is not None
    return row


def get_connection(provider: str) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT id, provider, status, google_email, scopes, token_json_encrypted, "
            "config_json, last_sync_at, last_sync_error, created_at, updated_at "
            "FROM connections WHERE provider=?",
            (provider,),
        ).fetchone()
    return dict(row) if row else None


def list_connections() -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT id, provider, status, google_email, scopes, token_json_encrypted, "
            "config_json, last_sync_at, last_sync_error, created_at, updated_at "
            "FROM connections ORDER BY provider ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def delete_connection(provider: str) -> bool:
    with conn() as c:
        cur = c.execute("DELETE FROM connections WHERE provider=?", (provider,))
        return (cur.rowcount or 0) > 0


def patch_connection_config(provider: str, config: dict) -> dict | None:
    """Merge keys into config_json. Returns the updated row."""
    row = get_connection(provider)
    if not row:
        return None
    existing: dict = {}
    try:
        existing = json.loads(row.get("config_json") or "{}")
    except (TypeError, ValueError):
        existing = {}
    existing.update(config)
    now = _utc_now()
    blob = json.dumps(existing)
    with conn() as c:
        c.execute(
            "UPDATE connections SET config_json=?, updated_at=? WHERE provider=?",
            (blob, now, provider),
        )
    return get_connection(provider)


def update_connection_tokens(provider: str, token_encrypted: str) -> None:
    now = _utc_now()
    with conn() as c:
        c.execute(
            "UPDATE connections SET token_json_encrypted=?, updated_at=? WHERE provider=?",
            (token_encrypted, now, provider),
        )


def update_connection_sync(
    provider: str,
    *,
    last_sync_at: str | None = None,
    last_sync_error: str | None = None,
) -> None:
    now = _utc_now()
    with conn() as c:
        c.execute(
            "UPDATE connections SET last_sync_at=?, last_sync_error=?, updated_at=? "
            "WHERE provider=?",
            (last_sync_at or now, last_sync_error, now, provider),
        )


def delete_dataset(slug: str) -> bool:
    """Remove a dataset row. Files on disk are left untouched (caller responsibility).

    Cascades: briefs, evidences, prds, cached_asks reference the slug as TEXT
    rather than FK, so they survive. This is intentional — a re-uploaded dataset
    under the same slug should not silently inherit old briefs (the corpus has
    changed), but we don't auto-purge either. Use a separate admin op if needed.
    """
    with conn() as c:
        cur = c.execute("DELETE FROM datasets WHERE slug=?", (slug,))
        return (cur.rowcount or 0) > 0
