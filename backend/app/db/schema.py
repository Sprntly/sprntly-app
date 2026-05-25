"""SQLite DDL + idempotent migrations.

Lives here instead of next to each domain submodule because
`executescript` runs everything in one transaction — splitting CREATE
statements per file would just complicate teardown. The schema is the
SQLite source of truth; Supabase has its own copy under
~/Sprntly/supabase/migrations/.
"""
from pathlib import Path

from app.config import settings
from app.db.client import conn

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
    -- Generic identifier for non-Google connectors: "alice@co.com" for
    -- Figma, "@octocat" for GitHub. Google still uses google_email.
    account_label TEXT,
    scopes TEXT NOT NULL DEFAULT '',
    token_json_encrypted TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    last_sync_at TEXT,
    last_sync_error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- GitHub App installations. One row per install_id. account_type is
-- 'User' or 'Organization'; repository_selection is 'all' or 'selected'.
CREATE TABLE IF NOT EXISTS github_installations (
    installation_id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL,
    account_login TEXT NOT NULL,
    account_type TEXT NOT NULL,
    repository_selection TEXT NOT NULL DEFAULT 'selected',
    suspended INTEGER NOT NULL DEFAULT 0,
    permissions_json TEXT NOT NULL DEFAULT '{}',
    events_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Open PRs tracked from webhook events.
CREATE TABLE IF NOT EXISTS github_pull_requests (
    installation_id INTEGER NOT NULL,
    repo_full_name TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open',
    is_draft INTEGER NOT NULL DEFAULT 0,
    author_login TEXT,
    head_ref TEXT,
    base_ref TEXT,
    html_url TEXT,
    body_excerpt TEXT,
    pr_created_at TEXT,
    pr_updated_at TEXT,
    last_event_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (repo_full_name, pr_number)
);
CREATE INDEX IF NOT EXISTS github_pull_requests_install
    ON github_pull_requests(installation_id, state);
"""


def init_db() -> None:
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    with conn() as c:
        c.executescript(SCHEMA)
        # Idempotent migrations for boxes that already have the prds table.
        cols = {row[1] for row in c.execute("PRAGMA table_info(prds)").fetchall()}
        if "status" not in cols:
            c.execute("ALTER TABLE prds ADD COLUMN status TEXT NOT NULL DEFAULT 'ready'")
        if "error" not in cols:
            c.execute("ALTER TABLE prds ADD COLUMN error TEXT")
        if "template_version" not in cols:
            c.execute("ALTER TABLE prds ADD COLUMN template_version INTEGER")
        if "variant" not in cols:
            c.execute(
                "ALTER TABLE prds ADD COLUMN variant TEXT NOT NULL DEFAULT 'v1'"
            )
        ev_cols = {row[1] for row in c.execute("PRAGMA table_info(evidences)").fetchall()}
        if ev_cols and "template_version" not in ev_cols:
            c.execute("ALTER TABLE evidences ADD COLUMN template_version INTEGER")
        if ev_cols and "variant" not in ev_cols:
            c.execute(
                "ALTER TABLE evidences ADD COLUMN variant TEXT NOT NULL DEFAULT 'v1'"
            )
        # Generic account label for non-Google connectors. Boxes that
        # pre-date this column get it added in place; old rows keep NULL.
        conn_cols = {row[1] for row in c.execute("PRAGMA table_info(connections)").fetchall()}
        if conn_cols and "account_label" not in conn_cols:
            c.execute("ALTER TABLE connections ADD COLUMN account_label TEXT")
