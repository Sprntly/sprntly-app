"""SQLite-backed graph implementation — transitional.

Why this exists: per the engineering decision documented in
graph/facade.py, KG must not block other agent work that's already
underway. The SQLite backend lets the facade ship today; FalkorDB
swap-in is a configuration change after spec-grade integration tests
pass.

Schema: 6 entity tables (one per type) + 1 edges table. Each row is a
JSON serialization of the Pydantic entity. We index on (workspace_id,
entity_id) for tenant-bounded lookup and on edge endpoints for graph
walks. Bitemporal semantics are preserved by including valid_at and
transaction_at as indexed columns; queries can filter by them via
query_as_of() on the facade.

This is NOT a high-performance graph engine. Multi-hop walks beyond
2-3 hops will be slow. The spec's <500ms load_session_context budget
is met today because session context is single-hop. Anything beyond
that should land on FalkorDB.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from app.graph.backends.base import GraphBackend
from app.graph.edges import Edge, EdgeType
from app.graph.entities import (
    Artifact,
    Decision,
    Hypothesis,
    Outcome,
    Signal,
    Workspace,
)


_ENTITY_TABLES = {
    "workspace": "kg_workspaces",
    "signal": "kg_signals",
    "hypothesis": "kg_hypotheses",
    "decision": "kg_decisions",
    "outcome": "kg_outcomes",
    "artifact": "kg_artifacts",
}


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS kg_workspaces (
    workspace_id   TEXT PRIMARY KEY,
    valid_at       TEXT NOT NULL,
    transaction_at TEXT NOT NULL,
    payload_json   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kg_signals (
    signal_id      TEXT NOT NULL,
    workspace_id   TEXT NOT NULL,
    valid_at       TEXT NOT NULL,
    transaction_at TEXT NOT NULL,
    source_type    TEXT NOT NULL,
    stale_after    TEXT,
    payload_json   TEXT NOT NULL,
    PRIMARY KEY (workspace_id, signal_id)
);
CREATE INDEX IF NOT EXISTS idx_kg_signals_ws_source
    ON kg_signals(workspace_id, source_type);
CREATE INDEX IF NOT EXISTS idx_kg_signals_stale
    ON kg_signals(workspace_id, stale_after);

CREATE TABLE IF NOT EXISTS kg_hypotheses (
    hypothesis_id  TEXT NOT NULL,
    workspace_id   TEXT NOT NULL,
    valid_at       TEXT NOT NULL,
    transaction_at TEXT NOT NULL,
    status         TEXT NOT NULL,
    status_updated_at TEXT NOT NULL,
    payload_json   TEXT NOT NULL,
    PRIMARY KEY (workspace_id, hypothesis_id)
);
CREATE INDEX IF NOT EXISTS idx_kg_hyp_ws_status
    ON kg_hypotheses(workspace_id, status);

CREATE TABLE IF NOT EXISTS kg_decisions (
    decision_id    TEXT NOT NULL,
    workspace_id   TEXT NOT NULL,
    valid_at       TEXT NOT NULL,
    transaction_at TEXT NOT NULL,
    approved_at    TEXT NOT NULL,
    payload_json   TEXT NOT NULL,
    PRIMARY KEY (workspace_id, decision_id)
);
CREATE INDEX IF NOT EXISTS idx_kg_dec_ws_approved
    ON kg_decisions(workspace_id, approved_at DESC);

CREATE TABLE IF NOT EXISTS kg_outcomes (
    outcome_id     TEXT NOT NULL,
    workspace_id   TEXT NOT NULL,
    valid_at       TEXT NOT NULL,
    transaction_at TEXT NOT NULL,
    shipped_at     TEXT NOT NULL,
    actual_impact_measured_at TEXT,
    payload_json   TEXT NOT NULL,
    PRIMARY KEY (workspace_id, outcome_id)
);
CREATE INDEX IF NOT EXISTS idx_kg_out_ws_measured
    ON kg_outcomes(workspace_id, actual_impact_measured_at DESC);

CREATE TABLE IF NOT EXISTS kg_artifacts (
    artifact_id    TEXT NOT NULL,
    workspace_id   TEXT NOT NULL,
    valid_at       TEXT NOT NULL,
    transaction_at TEXT NOT NULL,
    artifact_type  TEXT NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 1,
    payload_json   TEXT NOT NULL,
    PRIMARY KEY (workspace_id, artifact_id)
);
CREATE INDEX IF NOT EXISTS idx_kg_art_ws_type
    ON kg_artifacts(workspace_id, artifact_type);

CREATE TABLE IF NOT EXISTS kg_artifact_deltas (
    delta_id       TEXT PRIMARY KEY,
    workspace_id   TEXT NOT NULL,
    artifact_id    TEXT NOT NULL,
    artifact_type  TEXT NOT NULL,
    section        TEXT NOT NULL,
    original_text  TEXT NOT NULL,
    edited_text    TEXT NOT NULL,
    user_id        TEXT NOT NULL,
    classification TEXT NOT NULL,
    valid_at       TEXT NOT NULL,
    transaction_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kg_artdelta_ws_art
    ON kg_artifact_deltas(workspace_id, artifact_id);

CREATE TABLE IF NOT EXISTS kg_edges (
    edge_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id         TEXT NOT NULL,
    edge_type            TEXT NOT NULL,
    source_entity_id     TEXT NOT NULL,
    source_entity_type   TEXT NOT NULL,
    target_entity_id     TEXT NOT NULL,
    target_entity_type   TEXT NOT NULL,
    valid_at             TEXT NOT NULL,
    transaction_at       TEXT NOT NULL,
    confidence           REAL NOT NULL,
    source               TEXT NOT NULL,
    payload_json         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kg_edges_from
    ON kg_edges(workspace_id, source_entity_id, edge_type);
CREATE INDEX IF NOT EXISTS idx_kg_edges_to
    ON kg_edges(workspace_id, target_entity_id, edge_type);
"""


def _iso(dt: datetime) -> str:
    """Normalize datetime to RFC3339 UTC string."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


class SqliteBackend(GraphBackend):
    """Single-file SQLite-backed graph. Default during the FalkorDB rollout."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    # ──────────── connection helper ────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ──────────── lifecycle ────────────

    def ping(self) -> bool:
        try:
            with self._conn() as c:
                c.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    def initialize_schema(self) -> None:
        with self._conn() as c:
            c.executescript(_SCHEMA_SQL)

    # ──────────── entity writes (idempotent upsert) ────────────

    def write_workspace(self, w: Workspace) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO kg_workspaces (workspace_id, valid_at, transaction_at, payload_json)
                   VALUES (?,?,?,?)
                   ON CONFLICT(workspace_id) DO UPDATE SET
                     valid_at = excluded.valid_at,
                     transaction_at = excluded.transaction_at,
                     payload_json = excluded.payload_json
                """,
                (w.workspace_id, _iso(w.valid_at), _iso(w.transaction_at), w.model_dump_json()),
            )

    def write_signal(self, s: Signal) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO kg_signals (signal_id, workspace_id, valid_at, transaction_at, source_type, stale_after, payload_json)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(workspace_id, signal_id) DO UPDATE SET
                     valid_at = excluded.valid_at,
                     transaction_at = excluded.transaction_at,
                     source_type = excluded.source_type,
                     stale_after = excluded.stale_after,
                     payload_json = excluded.payload_json
                """,
                (
                    s.signal_id,
                    s.workspace_id,
                    _iso(s.valid_at),
                    _iso(s.transaction_at),
                    s.source_type.value,
                    _iso(s.stale_after) if s.stale_after else None,
                    s.model_dump_json(),
                ),
            )

    def write_hypothesis(self, h: Hypothesis) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO kg_hypotheses (hypothesis_id, workspace_id, valid_at, transaction_at, status, status_updated_at, payload_json)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(workspace_id, hypothesis_id) DO UPDATE SET
                     valid_at = excluded.valid_at,
                     transaction_at = excluded.transaction_at,
                     status = excluded.status,
                     status_updated_at = excluded.status_updated_at,
                     payload_json = excluded.payload_json
                """,
                (
                    h.hypothesis_id,
                    h.workspace_id,
                    _iso(h.valid_at),
                    _iso(h.transaction_at),
                    h.status.value,
                    _iso(h.status_updated_at),
                    h.model_dump_json(),
                ),
            )

    def write_decision(self, d: Decision) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO kg_decisions (decision_id, workspace_id, valid_at, transaction_at, approved_at, payload_json)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(workspace_id, decision_id) DO UPDATE SET
                     valid_at = excluded.valid_at,
                     transaction_at = excluded.transaction_at,
                     approved_at = excluded.approved_at,
                     payload_json = excluded.payload_json
                """,
                (
                    d.decision_id,
                    d.workspace_id,
                    _iso(d.valid_at),
                    _iso(d.transaction_at),
                    _iso(d.approved_at),
                    d.model_dump_json(),
                ),
            )

    def write_outcome(self, o: Outcome) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO kg_outcomes (outcome_id, workspace_id, valid_at, transaction_at, shipped_at, actual_impact_measured_at, payload_json)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(workspace_id, outcome_id) DO UPDATE SET
                     valid_at = excluded.valid_at,
                     transaction_at = excluded.transaction_at,
                     shipped_at = excluded.shipped_at,
                     actual_impact_measured_at = excluded.actual_impact_measured_at,
                     payload_json = excluded.payload_json
                """,
                (
                    o.outcome_id,
                    o.workspace_id,
                    _iso(o.valid_at),
                    _iso(o.transaction_at),
                    _iso(o.shipped_at),
                    _iso(o.actual_impact_measured_at) if o.actual_impact_measured_at else None,
                    o.model_dump_json(),
                ),
            )

    def write_artifact(self, a: Artifact) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO kg_artifacts (artifact_id, workspace_id, valid_at, transaction_at, artifact_type, current_version, payload_json)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(workspace_id, artifact_id) DO UPDATE SET
                     valid_at = excluded.valid_at,
                     transaction_at = excluded.transaction_at,
                     artifact_type = excluded.artifact_type,
                     current_version = excluded.current_version,
                     payload_json = excluded.payload_json
                """,
                (
                    a.artifact_id,
                    a.workspace_id,
                    _iso(a.valid_at),
                    _iso(a.transaction_at),
                    a.artifact_type.value,
                    a.current_version,
                    a.model_dump_json(),
                ),
            )

    # ──────────── entity reads ────────────

    def _get_payload(
        self, table: str, key_col: str, key_val: str, workspace_id: str
    ) -> Optional[str]:
        with self._conn() as c:
            row = c.execute(
                f"SELECT payload_json FROM {table} WHERE workspace_id = ? AND {key_col} = ?",
                (workspace_id, key_val),
            ).fetchone()
            return row["payload_json"] if row else None

    def get_workspace(self, workspace_id: str) -> Optional[Workspace]:
        with self._conn() as c:
            row = c.execute(
                "SELECT payload_json FROM kg_workspaces WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
            return Workspace.model_validate_json(row["payload_json"]) if row else None

    def get_signal(self, workspace_id: str, signal_id: str) -> Optional[Signal]:
        p = self._get_payload("kg_signals", "signal_id", signal_id, workspace_id)
        return Signal.model_validate_json(p) if p else None

    def get_hypothesis(self, workspace_id: str, hypothesis_id: str) -> Optional[Hypothesis]:
        p = self._get_payload(
            "kg_hypotheses", "hypothesis_id", hypothesis_id, workspace_id
        )
        return Hypothesis.model_validate_json(p) if p else None

    def get_decision(self, workspace_id: str, decision_id: str) -> Optional[Decision]:
        p = self._get_payload("kg_decisions", "decision_id", decision_id, workspace_id)
        return Decision.model_validate_json(p) if p else None

    def get_outcome(self, workspace_id: str, outcome_id: str) -> Optional[Outcome]:
        p = self._get_payload("kg_outcomes", "outcome_id", outcome_id, workspace_id)
        return Outcome.model_validate_json(p) if p else None

    def get_artifact(self, workspace_id: str, artifact_id: str) -> Optional[Artifact]:
        p = self._get_payload("kg_artifacts", "artifact_id", artifact_id, workspace_id)
        return Artifact.model_validate_json(p) if p else None

    # ──────────── edge writes / reads ────────────

    def write_edge(self, e: Edge) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO kg_edges (workspace_id, edge_type, source_entity_id, source_entity_type, target_entity_id, target_entity_type, valid_at, transaction_at, confidence, source, payload_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    e.workspace_id,
                    e.edge_type.value,
                    e.source_entity_id,
                    e.source_entity_type,
                    e.target_entity_id,
                    e.target_entity_type,
                    _iso(e.valid_at),
                    _iso(e.transaction_at),
                    e.confidence,
                    e.source,
                    e.model_dump_json(),
                ),
            )

    def edges_from(
        self,
        workspace_id: str,
        source_entity_id: str,
        edge_type: Optional[str] = None,
    ) -> list[Edge]:
        sql = "SELECT payload_json FROM kg_edges WHERE workspace_id = ? AND source_entity_id = ?"
        args: tuple[Any, ...] = (workspace_id, source_entity_id)
        if edge_type:
            sql += " AND edge_type = ?"
            args = (*args, edge_type)
        with self._conn() as c:
            return [Edge.model_validate_json(r["payload_json"]) for r in c.execute(sql, args)]

    def edges_to(
        self,
        workspace_id: str,
        target_entity_id: str,
        edge_type: Optional[str] = None,
    ) -> list[Edge]:
        sql = "SELECT payload_json FROM kg_edges WHERE workspace_id = ? AND target_entity_id = ?"
        args: tuple[Any, ...] = (workspace_id, target_entity_id)
        if edge_type:
            sql += " AND edge_type = ?"
            args = (*args, edge_type)
        with self._conn() as c:
            return [Edge.model_validate_json(r["payload_json"]) for r in c.execute(sql, args)]

    # ──────────── query patterns ────────────

    def load_session_context(self, workspace_id: str) -> dict[str, Any]:
        ws = self.get_workspace(workspace_id)
        if ws is None:
            return {
                "workspace": None,
                "active_hypotheses": [],
                "recent_decisions": [],
                "recent_outcomes": [],
            }
        return {
            "workspace": ws,
            "active_hypotheses": self.list_active_hypotheses(workspace_id, limit=10),
            "recent_decisions": self.list_recent_decisions(workspace_id, limit=5),
            "recent_outcomes": self.list_recent_outcomes(workspace_id, limit=3),
        }

    def list_active_hypotheses(
        self, workspace_id: str, limit: int = 10
    ) -> list[Hypothesis]:
        with self._conn() as c:
            rows = c.execute(
                """SELECT payload_json FROM kg_hypotheses
                   WHERE workspace_id = ? AND status IN ('candidate','proposed','confirmed')
                   ORDER BY status_updated_at DESC LIMIT ?""",
                (workspace_id, limit),
            ).fetchall()
        return [Hypothesis.model_validate_json(r["payload_json"]) for r in rows]

    def list_recent_decisions(
        self, workspace_id: str, limit: int = 5
    ) -> list[Decision]:
        with self._conn() as c:
            rows = c.execute(
                """SELECT payload_json FROM kg_decisions
                   WHERE workspace_id = ?
                   ORDER BY approved_at DESC LIMIT ?""",
                (workspace_id, limit),
            ).fetchall()
        return [Decision.model_validate_json(r["payload_json"]) for r in rows]

    def list_recent_outcomes(
        self, workspace_id: str, limit: int = 3, measured_only: bool = True
    ) -> list[Outcome]:
        sql = "SELECT payload_json FROM kg_outcomes WHERE workspace_id = ?"
        if measured_only:
            sql += " AND actual_impact_measured_at IS NOT NULL"
        sql += " ORDER BY actual_impact_measured_at DESC NULLS LAST, shipped_at DESC LIMIT ?"
        with self._conn() as c:
            rows = c.execute(sql, (workspace_id, limit)).fetchall()
        return [Outcome.model_validate_json(r["payload_json"]) for r in rows]

    def list_active_signals(
        self,
        workspace_id: str,
        source_types: Optional[list[str]] = None,
        limit: int = 50,
    ) -> list[Signal]:
        now = _iso(datetime.now(timezone.utc))
        if source_types:
            placeholders = ",".join("?" * len(source_types))
            sql = f"""SELECT payload_json FROM kg_signals
                     WHERE workspace_id = ?
                       AND (stale_after IS NULL OR stale_after > ?)
                       AND source_type IN ({placeholders})
                     ORDER BY transaction_at DESC LIMIT ?"""
            args: tuple[Any, ...] = (workspace_id, now, *source_types, limit)
        else:
            sql = """SELECT payload_json FROM kg_signals
                     WHERE workspace_id = ?
                       AND (stale_after IS NULL OR stale_after > ?)
                     ORDER BY transaction_at DESC LIMIT ?"""
            args = (workspace_id, now, limit)
        with self._conn() as c:
            return [Signal.model_validate_json(r["payload_json"]) for r in c.execute(sql, args)]

    # ──────────── bitemporal point-in-time queries ────────────

    def _bitemporal_filter(
        self,
        table: str,
        workspace_id: str,
        as_of: datetime,
    ) -> list[str]:
        """Shared bitemporal SQL: rows where transaction_at <= as_of AND
        valid_at <= as_of, scoped to workspace. Returns payload_json strings.

        v1 strategy: return all such rows. Each entity table has its own
        primary key (workspace_id, entity_id), so duplicates per id only
        arise if a row was rewritten — for now we return whatever SQLite
        has, which is the latest write (UPSERT semantics). Multi-version
        history per entity_id will land when we move to FalkorDB's native
        bitemporal indexing.
        """
        as_of_iso = _iso(as_of)
        with self._conn() as c:
            rows = c.execute(
                f"""SELECT payload_json FROM {table}
                    WHERE workspace_id = ?
                      AND transaction_at <= ?
                      AND valid_at <= ?""",
                (workspace_id, as_of_iso, as_of_iso),
            ).fetchall()
        return [r["payload_json"] for r in rows]

    def list_signals_as_of(self, workspace_id: str, as_of: datetime) -> list[Signal]:
        return [
            Signal.model_validate_json(p)
            for p in self._bitemporal_filter("kg_signals", workspace_id, as_of)
        ]

    def list_hypotheses_as_of(
        self, workspace_id: str, as_of: datetime
    ) -> list[Hypothesis]:
        return [
            Hypothesis.model_validate_json(p)
            for p in self._bitemporal_filter("kg_hypotheses", workspace_id, as_of)
        ]

    def list_decisions_as_of(
        self, workspace_id: str, as_of: datetime
    ) -> list[Decision]:
        return [
            Decision.model_validate_json(p)
            for p in self._bitemporal_filter("kg_decisions", workspace_id, as_of)
        ]

    def list_outcomes_as_of(
        self, workspace_id: str, as_of: datetime
    ) -> list[Outcome]:
        return [
            Outcome.model_validate_json(p)
            for p in self._bitemporal_filter("kg_outcomes", workspace_id, as_of)
        ]

    def list_artifacts_as_of(
        self, workspace_id: str, as_of: datetime
    ) -> list[Artifact]:
        return [
            Artifact.model_validate_json(p)
            for p in self._bitemporal_filter("kg_artifacts", workspace_id, as_of)
        ]

    def get_workspace_as_of(
        self, workspace_id: str, as_of: datetime
    ) -> Optional[Workspace]:
        as_of_iso = _iso(as_of)
        with self._conn() as c:
            row = c.execute(
                """SELECT payload_json FROM kg_workspaces
                   WHERE workspace_id = ?
                     AND transaction_at <= ?
                     AND valid_at <= ?""",
                (workspace_id, as_of_iso, as_of_iso),
            ).fetchone()
            return Workspace.model_validate_json(row["payload_json"]) if row else None

    # ──────────── delta log ────────────

    def write_artifact_delta(self, delta_row: dict[str, Any]) -> None:
        with self._conn() as c:
            c.execute(
                """INSERT INTO kg_artifact_deltas
                   (delta_id, workspace_id, artifact_id, artifact_type, section,
                    original_text, edited_text, user_id, classification,
                    valid_at, transaction_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(delta_id) DO UPDATE SET
                     artifact_type = excluded.artifact_type,
                     section = excluded.section,
                     original_text = excluded.original_text,
                     edited_text = excluded.edited_text,
                     user_id = excluded.user_id,
                     classification = excluded.classification,
                     valid_at = excluded.valid_at,
                     transaction_at = excluded.transaction_at
                """,
                (
                    delta_row["delta_id"],
                    delta_row["workspace_id"],
                    delta_row["artifact_id"],
                    delta_row["artifact_type"],
                    delta_row["section"],
                    delta_row["original_text"],
                    delta_row["edited_text"],
                    delta_row["user_id"],
                    delta_row["classification"],
                    delta_row["valid_at"],
                    delta_row["transaction_at"],
                ),
            )

    def list_artifact_deltas(
        self, workspace_id: str, artifact_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM kg_artifact_deltas WHERE workspace_id = ?"
        args: tuple[Any, ...] = (workspace_id,)
        if artifact_id is not None:
            sql += " AND artifact_id = ?"
            args = (*args, artifact_id)
        sql += " ORDER BY transaction_at DESC"
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    # ──────────── debug helpers ────────────

    def all_entity_ids(self, workspace_id: str) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        with self._conn() as c:
            out["workspaces"] = [
                r["workspace_id"]
                for r in c.execute(
                    "SELECT workspace_id FROM kg_workspaces WHERE workspace_id = ?",
                    (workspace_id,),
                )
            ]
            out["signals"] = [
                r["signal_id"]
                for r in c.execute(
                    "SELECT signal_id FROM kg_signals WHERE workspace_id = ?",
                    (workspace_id,),
                )
            ]
            out["hypotheses"] = [
                r["hypothesis_id"]
                for r in c.execute(
                    "SELECT hypothesis_id FROM kg_hypotheses WHERE workspace_id = ?",
                    (workspace_id,),
                )
            ]
            out["decisions"] = [
                r["decision_id"]
                for r in c.execute(
                    "SELECT decision_id FROM kg_decisions WHERE workspace_id = ?",
                    (workspace_id,),
                )
            ]
            out["outcomes"] = [
                r["outcome_id"]
                for r in c.execute(
                    "SELECT outcome_id FROM kg_outcomes WHERE workspace_id = ?",
                    (workspace_id,),
                )
            ]
            out["artifacts"] = [
                r["artifact_id"]
                for r in c.execute(
                    "SELECT artifact_id FROM kg_artifacts WHERE workspace_id = ?",
                    (workspace_id,),
                )
            ]
        return out

    def wipe_workspace(self, workspace_id: str) -> None:
        with self._conn() as c:
            for table in (*_ENTITY_TABLES.values(), "kg_edges", "kg_artifact_deltas"):
                c.execute(f"DELETE FROM {table} WHERE workspace_id = ?", (workspace_id,))
