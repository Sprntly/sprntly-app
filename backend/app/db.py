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
    payload_md TEXT NOT NULL,
    FOREIGN KEY (brief_id) REFERENCES briefs(id)
);

CREATE TABLE IF NOT EXISTS ask_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    citations_json TEXT NOT NULL
);
"""


def init_db() -> None:
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    with conn() as c:
        c.executescript(SCHEMA)


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


def save_brief(dataset: str, week_label: str, payload: dict) -> int:
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


def save_prd(brief_id: int, insight_index: int, title: str, md: str) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO prds (brief_id, insight_index, title, payload_md) "
            "VALUES (?, ?, ?, ?)",
            (brief_id, insight_index, title, md),
        )
        return cur.lastrowid


def get_prd(prd_id: int) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT id, brief_id, insight_index, generated_at, title, payload_md "
            "FROM prds WHERE id=?",
            (prd_id,),
        ).fetchone()
    return dict(row) if row else None


def log_ask(question: str, answer: str, citations: list) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO ask_log (question, answer, citations_json) VALUES (?, ?, ?)",
            (question, answer, json.dumps(citations)),
        )
