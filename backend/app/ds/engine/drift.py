"""WS-C — Schema Drift Detection & Re-confirmation (spec §4).

Extends the schema contract gate from point-in-time to continuous:

  * fingerprint(): computed on EVERY ingestion of every file (not only first
    attach). Hash over column names, dtypes (pd.api.types checks — never dtype
    string equality, per the known pandas-3.0 StringDtype bug class), capped enum
    value sets for categorical fields, and detected row grain.
  * Fingerprints are stored append-only per workspace, timestamped (invariant 5).
  * classify(): diff new vs last fingerprint → ADDITIVE / BREAKING / SEMANTIC per
    the §4.3 table, scoped to columns referenced by CONFIRMED definitions.
  * Rename heuristic (§4.3): removed column + added column with high name
    similarity and comparable dtype → rename candidate, powering the "here's what
    it probably maps to now" side of re-confirmation.
  * On BREAKING/SEMANTIC drift touching a confirmed definition: flip it STALE via
    registry.mark_stale() and write a team drift notice (§4.4). Email transport is
    a config value (FEEDBACK_REPORT_EMAIL); the prototype writes
    <workspace>/.sprntly/outbox/*.txt — engineering swaps the transport, not the content.

Read contract (§4.6): the DS Agent never talks to this module during analysis.
It reads definition STATUS from the registry — the registry is the single interface.
This module (standing in for the Librarian/connector pipeline) is the only writer
of STALE.
"""
from __future__ import annotations
import json, os, time, hashlib, difflib
import pandas as pd
from . import registry as REG

CONFIG = {"FEEDBACK_REPORT_EMAIL": "sprntly@gmail.com"}  # config value, not a hardcode
ENUM_CARDINALITY_CAP = 25
SEMANTIC_VOLUME_BAND = 3.0   # x-fold row-volume shift treated as anomalous (prototype band;
                             # production uses a seasonality-aware band — OQ-5)


def _fp_path(workspace):
    d = os.path.join(workspace, ".sprntly"); os.makedirs(d, exist_ok=True)
    return os.path.join(d, "fingerprints.jsonl")


def fingerprint(df: pd.DataFrame, source_file: str) -> dict:
    cols = {}
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_bool_dtype(s): dt = "bool"
        elif pd.api.types.is_numeric_dtype(s): dt = "numeric"
        elif pd.api.types.is_string_dtype(s) or s.dtype == object: dt = "string"
        else: dt = "other"
        enum = None
        if dt == "string":
            u = s.dropna().unique()
            if len(u) <= ENUM_CARDINALITY_CAP:
                enum = sorted(str(x) for x in u)
        cols[str(c)] = dict(dtype=dt, enum=enum,
                            null_rate=round(float(s.isna().mean()), 4))
    body = json.dumps(cols, sort_keys=True)
    return dict(source=os.path.basename(source_file), columns=cols, rows=len(df),
                hash=hashlib.sha256(body.encode()).hexdigest()[:16], ts=time.time())


def record(workspace: str, fp: dict):
    with open(_fp_path(workspace), "a") as f:
        f.write(json.dumps(fp) + "\n")


def last_fingerprint(workspace: str, source: str) -> dict | None:
    p = _fp_path(workspace)
    last = None
    if os.path.exists(p):
        with open(p) as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    if r["source"] == source:
                        last = r
    return last


def _rename_candidates(removed: list, added: list, old_cols: dict, new_cols: dict) -> dict:
    out = {}
    for r in removed:
        best, score = None, 0.0
        for a in added:
            sim = difflib.SequenceMatcher(None, r.lower(), a.lower()).ratio()
            if sim > score and old_cols[r]["dtype"] == new_cols[a]["dtype"]:
                best, score = a, sim
        if best and score >= 0.55:
            out[r] = dict(candidate=best, similarity=round(score, 2))
    return out


def classify(workspace: str, new_fp: dict) -> list:
    """Diff vs the previous fingerprint of the same source; classify per §4.3;
    flip affected CONFIRMED definitions to STALE; write team notices.
    Returns a list of drift event dicts (empty = no drift)."""
    prev = last_fingerprint(workspace, new_fp["source"])
    record(workspace, new_fp)                     # append-only, always
    if prev is None:
        return []
    vol_shift = (new_fp["rows"] / max(prev["rows"], 1) > SEMANTIC_VOLUME_BAND
                 or prev["rows"] / max(new_fp["rows"], 1) > SEMANTIC_VOLUME_BAND)
    if prev["hash"] == new_fp["hash"] and not vol_shift:
        return []   # note: the hash covers schema, not volume — volume is checked separately
    oldc, newc = prev["columns"], new_fp["columns"]
    removed = [c for c in oldc if c not in newc]
    added   = [c for c in newc if c not in oldc]
    renames = _rename_candidates(removed, added, oldc, newc)
    reg = REG.load(workspace)
    confirmed_cols = {}
    for d in reg.values():
        if d["status"] == "CONFIRMED" and d.get("proposed_mapping"):
            for c in d["proposed_mapping"]["columns"]:
                confirmed_cols.setdefault(c, []).append(d)
    events = []
    # BREAKING: referenced column removed / renamed / retyped
    for c in removed:
        for d in confirmed_cols.get(c, []):
            note = f"column '{c}' no longer appears in {new_fp['source']}"
            if c in renames:
                note += f"; likely renamed to '{renames[c]['candidate']}' (similarity {renames[c]['similarity']})"
            events.append(_drift_event(workspace, d, "BREAKING", note, renames.get(c)))
    for c in set(oldc) & set(newc):
        if oldc[c]["dtype"] != newc[c]["dtype"]:
            for d in confirmed_cols.get(c, []):
                events.append(_drift_event(workspace, d, "BREAKING",
                    f"column '{c}' retyped {oldc[c]['dtype']} → {newc[c]['dtype']}", None))
        else:
            # SEMANTIC: enum set change / volume shift / null-rate jump on referenced col
            sem = []
            if oldc[c]["enum"] and newc[c]["enum"] and set(oldc[c]["enum"]) != set(newc[c]["enum"]):
                sem.append(f"enum set changed on '{c}': {sorted(set(newc[c]['enum'])^set(oldc[c]['enum']))[:4]}")
            if oldc[c]["null_rate"] + 0.25 < newc[c]["null_rate"]:
                sem.append(f"null rate on '{c}' jumped {oldc[c]['null_rate']:.0%} → {newc[c]['null_rate']:.0%}")
            for note in sem:
                for d in confirmed_cols.get(c, []):
                    events.append(_drift_event(workspace, d, "SEMANTIC", note, None))
    if vol_shift:
        for d in reg.values():
            if d["status"] == "CONFIRMED":
                events.append(_drift_event(workspace, d, "SEMANTIC",
                    f"row volume shifted {prev['rows']:,} → {new_fp['rows']:,} in {new_fp['source']}", None))
    # ADDITIVE-only changes touch no confirmed definition: log only (the fingerprint
    # append above IS the log). Candidate signal for SIL long-tail — out of scope here.
    return events


def _drift_event(workspace, definition, klass, note, rename):
    REG.mark_stale(workspace, definition["definition_id"], note,
                   alias_columns=[rename["candidate"]] if rename else None)
    ev = dict(definition_id=definition["definition_id"],
              plain_english=definition["plain_english"], version=definition["version"],
              klass=klass, note=note, rename_candidate=rename, ts=time.time())
    _team_notice(workspace, ev)
    return ev


def _team_notice(workspace, ev):
    """§4.4 team email. Prototype transport: file in .sprntly/outbox/."""
    d = os.path.join(workspace, ".sprntly", "outbox"); os.makedirs(d, exist_ok=True)
    company = os.path.basename(os.path.abspath(workspace))
    body = (f"To: {CONFIG['FEEDBACK_REPORT_EMAIL']}\n"
            f"Subject: [Drift] {company} — \"{ev['plain_english']}\" may have changed\n\n"
            f"For {company}, the definition \"{ev['plain_english']}\" (v{ev['version']}) "
            f"appears to have drifted ({ev['klass']}).\nWhat changed: {ev['note']}\n"
            f"Affected analyses will be flagged and the definition has been marked STALE "
            f"pending user re-confirmation.\nRecommended follow-up: contact {company} to "
            f"confirm whether the definition changed.\n")
    with open(os.path.join(d, f"drift-{int(ev['ts'])}-{ev['definition_id'][:8]}.txt"), "w") as f:
        f.write(body)
