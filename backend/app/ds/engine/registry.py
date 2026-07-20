"""WS-A — Definition Registry (spec §2).

Per-workspace registry of versioned definitions (metric, event_semantic, cohort,
join_key) with the INFERRED / CONFIRMED / STALE / REJECTED state machine and the
load-bearing tier-cap rule (§2.4):

    A claim may only carry MEASURED if every definition in its lineage is
    CONFIRMED and non-stale. Otherwise it caps at INFERRED with an inline note.

Storage: append-only JSONL per workspace at <workspace>/.sprntly/registry.jsonl.
Each line is a full Definition snapshot; the latest line per definition_id is the
current state; history is reconstructable by replay (invariant 5: append-only).

Invariants enforced here (spec §8):
  2. Only humans confirm  — confirm()/reconfirm_same() take a user_id and are the
     ONLY paths to CONFIRMED. SIL/pipeline code must never call them.
  5. Append-only history  — no line is ever rewritten or deleted.
  7. Tenant isolation     — the registry file lives inside the workspace directory;
     no cross-workspace reads exist in this module.

Prototype notes for engineering:
  * `proposed_mapping.columns` is the file-based stand-in for events/properties:
    the set of source column names the definition binds to. In production this is
    the connector event/property mapping from spec §2.3.
  * propose_from_plain_english() is a deterministic SIL STAND-IN (lexical match +
    threshold extraction). The production SIL (Phase 2) replaces its internals;
    the registry API and state machine do not change.
  * Ambiguity handling (§2.5 step 3.4): when >1 candidate scores within MARGIN,
    status stays INFERRED with `ambiguous=True` and all candidates recorded —
    the caller must present a choice, never auto-pick.
"""
from __future__ import annotations
import json, os, time, uuid, re, hashlib

MARGIN = 0.15  # ambiguity margin on candidate scores (§2.5 step 4)

STATUSES = ("INFERRED", "CONFIRMED", "STALE", "REJECTED")


def _reg_path(workspace: str) -> str:
    d = os.path.join(workspace, ".sprntly")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "registry.jsonl")


def _append(workspace: str, rec: dict):
    rec = dict(rec, _written_at=time.time())
    with open(_reg_path(workspace), "a") as f:
        f.write(json.dumps(rec) + "\n")


def load(workspace: str) -> dict:
    """Current state: latest record per definition_id. Empty dict if no registry."""
    p = _reg_path(workspace)
    cur = {}
    if os.path.exists(p):
        with open(p) as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    cur[r["definition_id"]] = r
    return cur


def history(workspace: str, definition_id: str) -> list:
    p = _reg_path(workspace)
    out = []
    if os.path.exists(p):
        with open(p) as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    if r["definition_id"] == definition_id:
                        out.append(r)
    return out


# ── creation & SIL stand-in proposal ──────────────────────────────────────────

def add_plain_english(workspace: str, plain_english: str, kind: str = "metric") -> dict:
    """Onboarding step 1 (§2.5): store the user's verbatim statement, INFERRED,
    mapping empty until proposal."""
    rec = dict(
        definition_id=str(uuid.uuid4()), workspace_id=os.path.abspath(workspace),
        kind=kind, plain_english=plain_english, proposed_mapping=None,
        status="INFERRED", confidence=0.0, validation_snapshot=None,
        schema_fingerprint=None, confirmed_by=None, confirmed_at=None,
        version=1, ambiguous=False, candidates=[], rejected_mappings=[],
    )
    _append(workspace, rec)
    return rec


_THRESH = re.compile(r"(?:more than|over|greater than|>=?|at least)\s+(\d+(?:\.\d+)?)")


def propose_from_plain_english(workspace: str, definition_id: str, available_columns: list) -> dict:
    """SIL STAND-IN (§2.5 step 3). Deterministic: lexical token overlap between the
    plain-English statement and column names, plus threshold extraction. Records
    ALL candidates; if the top two are within MARGIN, marks ambiguous and does not
    pick (§2.5 step 4 — never silently resolve a close call)."""
    cur = load(workspace)[definition_id]
    stem = lambda t: t[:-1] if t.endswith("s") and len(t) > 3 else t
    words = set(stem(t) for t in re.findall(r"[a-z]+", cur["plain_english"].lower()))
    scored = []
    for c in available_columns:
        ctoks = set((t[:-1] if t.endswith("s") and len(t) > 3 else t) for t in re.findall(r"[a-z]+", str(c).lower()))
        inter = words & ctoks
        if inter:
            scored.append((len(inter) / max(len(ctoks), 1), c))
    scored.sort(reverse=True)
    scored = [s for s in scored if [s[1]] not in cur.get("rejected_mappings", [])]
    if not scored:
        return cur  # nothing to propose; stays INFERRED with empty mapping
    m = _THRESH.search(cur["plain_english"].lower())
    threshold = float(m.group(1)) if m else None
    ambiguous = len(scored) > 1 and (scored[0][0] - scored[1][0]) < MARGIN
    top = scored[0]
    rec = dict(cur)
    rec["candidates"] = [dict(score=round(s, 3), columns=[c]) for s, c in scored[:4]]
    rec["ambiguous"] = bool(ambiguous)
    rec["confidence"] = round(top[0], 3)
    if not ambiguous:
        rec["proposed_mapping"] = dict(columns=[top[1]], threshold=threshold,
                                       aggregation="sum", time_grain="daily")
    _append(workspace, rec)
    return rec


def choose_candidate(workspace: str, definition_id: str, columns: list) -> dict:
    """User resolves an ambiguity by choosing a candidate (still INFERRED until
    confirm() — choice != confirmation)."""
    cur = load(workspace)[definition_id]
    rec = dict(cur, ambiguous=False,
               proposed_mapping=dict(columns=list(columns), threshold=None,
                                     aggregation="sum", time_grain="daily"))
    _append(workspace, rec)
    return rec


# ── the human gate (invariant 2: ONLY these functions reach CONFIRMED) ────────

def confirm(workspace: str, definition_id: str, user_id: str,
            validation_snapshot: dict | None = None,
            schema_fingerprint: str | None = None) -> dict:
    """Human confirms mapping AND number together (§2.5 step 4). The validation
    snapshot must be computed by the deterministic engine (see engine_snapshot()),
    never by an LLM (invariant 1)."""
    cur = load(workspace)[definition_id]
    if cur["status"] == "REJECTED":
        raise ValueError("cannot confirm a REJECTED record; re-propose first")
    if cur.get("ambiguous"):
        raise ValueError("ambiguous proposal: user must choose_candidate() first")
    if not cur.get("proposed_mapping"):
        raise ValueError("no mapping proposed yet")
    rec = dict(cur, status="CONFIRMED", confirmed_by=user_id, confirmed_at=time.time(),
               validation_snapshot=validation_snapshot,
               schema_fingerprint=schema_fingerprint)
    _append(workspace, rec)
    return rec


def edit(workspace: str, definition_id: str, new_mapping: dict) -> dict:
    """User edits → new INFERRED version (v+1), per the state machine."""
    cur = load(workspace)[definition_id]
    rec = dict(cur, status="INFERRED", proposed_mapping=new_mapping,
               version=cur["version"] + 1, confirmed_by=None, confirmed_at=None,
               validation_snapshot=None, ambiguous=False)
    _append(workspace, rec)
    return rec


def reject(workspace: str, definition_id: str) -> dict:
    """User rejects → REJECTED; the rejected mapping is recorded so an identical
    re-proposal is never made (§2.3 rules)."""
    cur = load(workspace)[definition_id]
    rej = list(cur.get("rejected_mappings", []))
    if cur.get("proposed_mapping"):
        rej.append(cur["proposed_mapping"]["columns"])
    rec = dict(cur, status="REJECTED", rejected_mappings=rej, proposed_mapping=None)
    _append(workspace, rec)
    return rec


# ── WS-C interface: Librarian/pipeline stale-ify; humans re-confirm ───────────

def mark_stale(workspace: str, definition_id: str, drift_note: str,
               alias_columns: list | None = None) -> dict:
    """alias_columns: rename candidates from WS-C — recorded so the tier cap keeps
    binding to the concept even though the source column name changed (§4.5:
    'affected analyses will be flagged' must survive a rename)."""
    cur = load(workspace)[definition_id]
    if cur["status"] != "CONFIRMED":
        return cur  # only confirmed definitions go stale
    rec = dict(cur, status="STALE", drift_note=drift_note,
               stale_alias_columns=list(alias_columns or []))
    _append(workspace, rec)
    return rec


def reconfirm_same(workspace: str, definition_id: str, user_id: str,
                   updated_columns: list | None = None,
                   schema_fingerprint: str | None = None) -> dict:
    """WS-C §4.5 outcome 1: 'same' — back to CONFIRMED at the SAME version, mapping
    updated to the new names (pure rename), fresh fingerprint slice."""
    cur = load(workspace)[definition_id]
    pm = dict(cur["proposed_mapping"] or {})
    if updated_columns:
        pm["columns"] = list(updated_columns)
    rec = dict(cur, status="CONFIRMED", proposed_mapping=pm, drift_note=None,
               confirmed_by=user_id, confirmed_at=time.time(),
               schema_fingerprint=schema_fingerprint)
    _append(workspace, rec)
    return rec
# WS-C §4.5 outcome 2 ('changed') = edit() then confirm(): full re-proposal path.


# ── deterministic validation snapshot (invariant 1) ───────────────────────────

def engine_snapshot(df, mapping: dict) -> dict:
    """Compute the confirmation-sheet number with the deterministic engine.
    df: the ingested table containing mapping['columns']."""
    import pandas as pd
    cols = [c for c in mapping.get("columns", []) if c in df.columns]
    if not cols:
        return dict(computed_value=None, note="mapped columns absent",
                    data_quality_flags=["columns-missing"])
    s = pd.to_numeric(df[cols[0]], errors="coerce")
    flags = []
    if s.isna().mean() > 0.2: flags.append(f"null-rate {s.isna().mean():.0%}")
    if len(s.dropna()) == 0: flags.append("no-numeric-values")
    thr = mapping.get("threshold")
    val = float(s[s > thr].count()) if thr is not None else float(s.sum())
    return dict(computed_value=val, window="full-file", computed_at=time.time(),
                data_quality_flags=flags)


# ── the tier-cap rule (§2.4) — called by the agent at narration time ──────────

def tier_cap_for_columns(reg_state: dict, columns_used: set) -> tuple[str | None, str | None]:
    """Given the current registry state and the set of source columns a finding
    depends on, return (cap, note). cap None = no cap. A finding caps at INFERRED
    if ANY definition whose mapping intersects its columns is not CONFIRMED, or is
    STALE. Definitions with no column overlap impose nothing.

    Lineage matching in the prototype is column-name intersection; production
    replaces this with explicit definition_id references in cohort-as-code."""
    worst = None
    for d in reg_state.values():
        pm = d.get("proposed_mapping") or {}
        cols = set(pm.get("columns", [])) | set(d.get("stale_alias_columns", []))
        if not cols or not (cols & columns_used):
            # a definition awaiting proposal still governs its plain-English concept,
            # but with no mapping we cannot bind it to a finding: no cap. Documented
            # as OPEN QUESTION OQ-3.
            continue
        if d["status"] == "STALE":
            return ("INFERRED",
                    f"definition '{d['plain_english']}' (v{d['version']}) is STALE: "
                    f"{d.get('drift_note','schema drift detected')} — re-confirm to restore MEASURED")
        if d["status"] in ("INFERRED", "REJECTED"):
            worst = ("INFERRED",
                     f"definition '{d['plain_english']}' (v{d['version']}) is {d['status']} "
                     f"(unconfirmed) — confirm it to upgrade this claim")
    return worst or (None, None)
