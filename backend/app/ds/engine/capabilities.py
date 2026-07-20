"""capabilities.py — v5.6 capability expansion (Phase-2 items 1 & 2 + battery gaps).

Seven components, each behind its own WS-E flag, all additive (flags off = v5.5):

  sil_vendor_adapters   Phase-2 #1 (prototype): vendor-aware ingestion for the top-10
                        analytics tools. Deterministic per-vendor dictionaries map
                        vendor-dialect columns (e.g. Mixpanel '$user_id', GA4
                        'eventDate') to roles when generic heuristics fail. The
                        production SIL swaps these dictionaries for an LLM
                        proposer + the SAME deterministic validation; the role
                        interface does not change.
  analysis_router       Phase-2 #2 (prototype): propose → dispose routing. propose()
                        inspects the canonical representation and emits analysis
                        proposals BEYOND the fixed battery; dispose() executes each
                        with deterministic code and the standard gates. Production
                        swaps the proposer for an LLM; the disposer (executors,
                        gates, tiers) never changes — an LLM proposal can only ever
                        cause a deterministic computation to run, never a number.
  trend_scan            weekly / monthly / quarterly / annual trends: per-period
                        aggregation, least-squares slope, >=25% net change over the
                        window, >=4 periods, entity-split sign consistency.
  auto_bucketing        raw numeric feature columns -> quartile bands -> routed
                        through the existing rate/flag scans (no one has to
                        pre-bucket 'duration' anymore).
  multi_numerator       every plausible numerator is scanned, not just the first
                        (Gross AND Net both get examined); dedup keeps the best
                        finding per (numerator, dimension, direction).
  cross_table           joins up to 3 tables on shared entity ids (same column name
                        OR >=50% value overlap); presence/level in table B vs
                        outcome in table A, Mann-Whitney + entity-split replication.
  lagged_effects        memory within the data: early-window behavior (first 25% of
                        the date range) vs late-window outcome (last 25%), for
                        ranges >= 8 weeks; entity-split replicated.
  niche_tier            small niches surface instead of vanishing: segments with
                        ratio >= 2.5x that FAIL the volume gates are reported as
                        explicitly-labeled small-sample HYPOTHESIS leads (capped 3)
                        — said, but never sold as MEASURED.

Nothing here mutates the v5.2 boolean pipeline or the v5.3/v5.5 scans. All findings
carry lineage fields (metric/dimension/denominator) so the WS-A tier cap governs
them exactly like every other claim.
"""
from __future__ import annotations
import itertools, re
import numpy as np, pandas as pd
from scipy import stats

# ─────────────────────────── SIL vendor adapters ───────────────────────────────
# Column-role dictionaries per vendor dialect (top-10 tools). Normalized-name → role.
_V = {
    "mixpanel":  {"$user_id": "id", "distinctid": "id", "mpdate": "date", "$mpapitimestamp": "date",
                  "revenuepurchaseusd": "num", "$revenue": "num", "$views": "den"},
    "amplitude": {"amplitudeid": "id", "deviceid": "id", "eventtime": "date", "clientuploadtime": "date",
                  "revenuetotal": "num"},
    "ga4":       {"clientid": "id", "userpseudoid": "id", "eventdate": "date",
                  "purchaserevenue": "num", "totalrevenueusd": "num", "sessions": "den",
                  "screenpageviews": "den"},
    "posthog":   {"distinctid": "id", "personid": "id", "timestamp": "date", "revenueamount": "num"},
    "statsig":   {"unitid": "id", "stableid": "id", "timeuts": "date", "valueearnings": "num"},
    "segment":   {"anonymousid": "id", "userid": "id", "receivedat": "date", "propertiesrevenue": "num"},
    "heap":      {"heapuserid": "id", "accountid": "id", "time": "date", "netrevenue": "num"},
    "optimizely":{"visitorid": "id", "timestampms": "date", "revenuecents": "num"},
    "pendo":     {"visitorid": "id", "accountid": "id", "day": "date", "numminutes": "den"},
    "mparticle": {"mpid": "id", "eventtimestamp": "date", "productrevenue": "num"},
}
_norm = lambda c: re.sub(r"[^a-z0-9$]", "", str(c).lower())


def vendor_roles(columns):
    """Return (vendor, {column → role}) for the best-matching vendor dictionary,
    or (None, {}) when no vendor dialect matches ≥2 columns."""
    best, best_map = None, {}
    for vendor, d in _V.items():
        m = {c: d[_norm(c)] for c in columns if _norm(c) in d}
        if len(m) > len(best_map):
            best, best_map = vendor, m
    return (best, best_map) if len(best_map) >= 2 else (None, {})


# ───────────────────────────── shared machinery ────────────────────────────────

def _halves(d, idc):
    import hashlib
    h = d[idc].map(lambda x: int(hashlib.md5(str(x).encode()).hexdigest(), 16) % 2 == 0)
    return d[h.values], d[~h.values]


def _num_candidates(measures):
    out = []
    for c in measures:
        k = _norm(c)
        if any(p in k for p in ("revenue", "earning", "payout", "rev")) and \
           "split" not in k and "fraction" not in k:
            out.append(c)
    return out


# ───────────────────────────── analysis router ─────────────────────────────────

def propose(can, flags):
    """Phase-2 #2 proposer (deterministic stand-in for the LLM). Emits proposals the
    fixed battery would not run. Every proposal names its executor kind + params +
    rationale; dispose() maps kind → deterministic executor. An LLM proposer emits
    the SAME schema — it can suggest computations, never perform them."""
    P = []
    for base, lt in can.get("long_tables", {}).items():
        cands = _num_candidates(lt["measures"])
        primary = cands[0] if cands else None
        if flags.get("multi_numerator") and len(cands) > 1:
            for extra in cands[1:3]:
                P.append(dict(kind="rate_extra_numerator", table=base, numerator=extra,
                              rationale=f"second plausible numerator '{extra}' never examined by the primary scan"))
        if flags.get("trend_scan") and lt.get("date") is not None and primary:
            P.append(dict(kind="trend", table=base, numerator=primary,
                          rationale="date grain present; gradual trends invisible to DOW/spike scans"))
        if flags.get("auto_bucketing") and primary:
            for c in lt["measures"]:
                k = _norm(c)
                if c != primary and not any(p in k for p in ("revenue", "earning", "payout", "rev",
                                                             "views", "watch", "impress", "session")):
                    P.append(dict(kind="auto_bucket", table=base, feature=c, numerator=primary,
                                  rationale=f"raw numeric '{c}' unusable by categorical scans without banding"))
        if flags.get("lagged_effects") and lt.get("date") is not None and primary:
            P.append(dict(kind="lagged", table=base, numerator=primary,
                          rationale="early-window behavior may predict late-window outcome"))
    if flags.get("cross_table"):
        P.append(dict(kind="cross_table", rationale="entities shared across tables never jointly analyzed"))
    return P


def dispose(can, proposals, flags):
    findings, leads = [], []
    for p in proposals:
        try:
            fn = _EXEC[p["kind"]]
        except KeyError:
            continue
        f, l = fn(can, p)
        findings += f; leads += l
    return findings, leads


# ───────────────────────────── executors (all gated) ───────────────────────────

def _exec_trend(can, p):
    lt = can["long_tables"][p["table"]]; num = p["numerator"]
    d = lt["df"].copy(); d[num] = pd.to_numeric(d[num], errors="coerce").fillna(0)
    dt = pd.to_datetime(d[lt["date"]].astype(str), format="%Y%m%d", errors="coerce")
    if dt.isna().mean() > 0.5: dt = pd.to_datetime(d[lt["date"]].astype(str), errors="coerce")
    if dt.isna().all(): return [], []
    span_days = (dt.max() - dt.min()).days
    grains = [("weekly", "W")]
    if span_days >= 120: grains.insert(0, ("monthly", "ME"))
    if span_days >= 540: grains.insert(0, ("quarterly", "QE"))
    if span_days >= 1460: grains.insert(0, ("annual", "YE"))
    out, lead = [], []
    A, B = _halves(d, lt["id"])
    dtA, dtB = dt.reindex(A.index), dt.reindex(B.index)
    for label, rule in grains:
        s = d.groupby(dt.dt.to_period({"W": "W", "ME": "M", "QE": "Q", "YE": "Y"}[rule]))[num].sum()
        s = s.iloc[1:-1] if len(s) > 5 else s          # trim partial edge periods
        if len(s) < 4 or s.sum() <= 0: continue
        x = np.arange(len(s)); slope, intercept = np.polyfit(x, s.values, 1)
        mean = s.mean()
        net = slope * (len(s) - 1) / max(mean, 1e-9)   # net change over window, as share of mean
        if abs(net) < 0.25: continue
        def half_slope(H, dth):
            hs = H.groupby(dth.dt.to_period({"W": "W", "ME": "M", "QE": "Q", "YE": "Y"}[rule]))[num].sum()
            hs = hs.iloc[1:-1] if len(hs) > 5 else hs
            if len(hs) < 4: return None
            return np.polyfit(np.arange(len(hs)), hs.values, 1)[0]
        sa, sb = half_slope(A, dtA), half_slope(B, dtB)
        if sa is None or sb is None or np.sign(sa) != np.sign(slope) or np.sign(sb) != np.sign(slope):
            continue
        direction = "rising" if slope > 0 else "declining"
        pct = 100 * slope / max(mean, 1e-9)
        out.append(dict(type="trend", evidence="MEASURED",
            claim=f"{label} '{num}' is {direction} ~{abs(pct):.1f}% per {label[:-2] if label!='annual' else 'year'} "
                  f"({net:+.0%} net over {len(s)} {label} periods) [{p['table']}]",
            cohort_code=f"df.groupby(pd.to_datetime(df['{lt['date']}']).dt.to_period('{rule[0]}'))['{num}'].sum()",
            stats=dict(slope=float(slope), net_change=round(float(net), 3), periods=int(len(s))),
            replication=f"entity-split slope-sign PASS ({sa:.3g}, {sb:.3g})",
            metric=num, table=p["table"]))
    return out[:2], lead


def _exec_auto_bucket(can, p):
    lt = can["long_tables"][p["table"]]; num, feat = p["numerator"], p["feature"]
    d = lt["df"].copy()
    d[num] = pd.to_numeric(d[num], errors="coerce").fillna(0)
    fv = pd.to_numeric(d[feat], errors="coerce")
    if fv.nunique() < 8 or fv.isna().mean() > 0.5: return [], []
    try:
        d["_band"] = pd.qcut(fv, 8, duplicates="drop").astype(str)
    except Exception:
        return [], []
    tot_n, tot_r = len(d), d[num].sum()
    if tot_r <= 0: return [], []
    overall = tot_r / tot_n
    g = d.groupby("_band").agg(n=(num, "size"), r=(num, "sum"))
    g = g[g["n"] >= max(0.03 * tot_n, 50)]
    g["ratio"] = (g["r"] / g["n"]) / overall
    out = []
    A, B = _halves(d, lt["id"])
    for band, row in pd.concat([g[g["ratio"] >= 1.7].nlargest(1, "ratio"),
                                g[g["ratio"] <= 0.6].nsmallest(1, "ratio")]).iterrows():
        reps = []
        for H in (A, B):
            hv = H[H["_band"] == band]
            if len(hv) < 20 or len(H) == 0: reps.append(None); continue
            ho = H[num].sum() / len(H)
            reps.append((hv[num].sum() / len(hv)) / max(ho, 1e-9))
        hi = row["ratio"] > 1
        if None in reps or not ((hi and min(reps) >= 1.25) or ((not hi) and max(reps) <= 0.8)):
            continue
        out.append(dict(type="numeric_band", evidence="MEASURED",
            claim=f"'{feat}' band {band}: per-row '{num}' runs {row['ratio']:.2g}x the table average "
                  f"(auto-bucketed octiles; n={row['n']:,.0f}) [{p['table']}]",
            cohort_code=f"pd.qcut(df['{feat}'],8) == '{band}'",
            stats=dict(ratio=round(float(row["ratio"]), 2), n=float(row["n"])),
            replication=f"entity-split PASS ({reps[0]:.2f}, {reps[1]:.2f})",
            metric=num, dimension=feat, table=p["table"]))
    return out, []


def _exec_extra_numerator(can, p):
    from . import ds_agent as DA
    lt = dict(can["long_tables"][p["table"]])
    # rerun the standard rate scan with the numerator FORCED to the extra candidate
    sub = dict(can); sub = {k: can[k] for k in can}
    one = {p["table"]: dict(lt, measures=[p["numerator"]] + [m for m in lt["measures"] if m != p["numerator"]])}
    tmp = dict(can, long_tables=one, _reg=None)
    fnds = DA.rate_dimension_scan(tmp, ambiguity_guard=False)
    for f in fnds:
        f["claim"] += f"  [secondary numerator '{p['numerator']}' — multi-numerator scan]"
    return fnds[:3], []


def _exec_lagged(can, p):
    lt = can["long_tables"][p["table"]]; num = p["numerator"]
    d = lt["df"].copy(); d[num] = pd.to_numeric(d[num], errors="coerce").fillna(0)
    dt = pd.to_datetime(d[lt["date"]].astype(str), format="%Y%m%d", errors="coerce")
    if dt.isna().mean() > 0.5: dt = pd.to_datetime(d[lt["date"]].astype(str), errors="coerce")
    if dt.isna().all(): return [], []
    lo, hi = dt.min(), dt.max()
    if (hi - lo).days < 56: return [], []            # memory needs >= 8 weeks
    q1 = lo + (hi - lo) * 0.25; q4 = lo + (hi - lo) * 0.75
    early = d[dt <= q1].groupby(lt["id"]).size().rename("early_n")
    late = d[dt >= q4].groupby(lt["id"]) [num].sum().rename("late_r")
    j = pd.concat([early, late], axis=1).fillna(0)
    if len(j) < 40: return [], []
    j["early_hi"] = j["early_n"] >= j["early_n"].median()
    a, b = j.loc[j["early_hi"], "late_r"], j.loc[~j["early_hi"], "late_r"]
    if min(len(a), len(b)) < 20 or b.mean() <= 0: return [], []
    ratio = a.mean() / b.mean()
    if not (ratio >= 1.7 or ratio <= 0.6): return [], []
    try: pv = stats.mannwhitneyu(a, b).pvalue
    except Exception: return [], []
    if pv > 0.01: return [], []
    import hashlib
    h = j.index.map(lambda x: int(hashlib.md5(str(x).encode()).hexdigest(), 16) % 2 == 0)
    ja, jb = j[h], j[~h]
    def hr(H):
        x, y = H.loc[H["early_hi"], "late_r"], H.loc[~H["early_hi"], "late_r"]
        return (x.mean() / y.mean()) if len(x) >= 10 and len(y) >= 10 and y.mean() > 0 else None
    ra, rb = hr(ja), hr(jb)
    hi = ratio > 1
    # both halves must show a CONVINCING effect (weaker half >= 1.5x / <= 0.67x). Lagged
    # effects dilute gently under split-half, so a loose 1.25 bar lets half-only effects
    # (benchmark L3) leak; the tighter bar keeps whole-population effects, drops the rest.
    if ra is None or rb is None or (hi and min(ra, rb) < 1.5) or ((not hi) and max(ra, rb) > 0.67):
        return [], []
    f = dict(type="lagged_effect", evidence="MEASURED",
        claim=f"entities in the top half of early-window activity (first quarter of the range) earn "
              f"{ratio:.1f}x '{num}' in the final quarter (MW p={pv:.1e}) [{p['table']}]",
        cohort_code=f"early activity >= median, first 25% of date range  # table: {p['table']}",
        stats=dict(ratio=round(float(ratio), 2), p=float(pv)),
        replication=f"entity-split PASS ({ra:.2f}, {rb:.2f})", metric=num, table=p["table"])
    return [f], []


def _exec_cross_table(can, p):
    tabs = list(can.get("long_tables", {}).items())[:3]     # up to 3 tables
    out, leads = [], []
    for (nA, A), (nB, B) in itertools.combinations(tabs, 2):
        numA = (_num_candidates(A["measures"]) or [None])[0]
        if numA is None: continue
        dA = A["df"]; rA = pd.to_numeric(dA[numA], errors="coerce").fillna(0)
        aggA = pd.DataFrame({"r": rA}).groupby(dA[A["id"]].astype(str)).sum()
        idsB = set(B["df"][B["id"]].astype(str))
        # join key: same id column name OR >=50% value overlap
        overlap = len(set(aggA.index) & idsB) / max(min(len(aggA), len(idsB)), 1)
        if A["id"] != B["id"] and overlap < 0.5: continue
        aggA["in_B"] = aggA.index.isin(idsB)
        a, b = aggA.loc[aggA["in_B"], "r"], aggA.loc[~aggA["in_B"], "r"]
        if min(len(a), len(b)) < 15 or b.mean() <= 0: continue
        ratio = a.mean() / b.mean()
        if not (ratio >= 1.7 or ratio <= 0.6): continue
        try: pv = stats.mannwhitneyu(a, b).pvalue
        except Exception: continue
        if pv > 0.01: continue
        import hashlib
        h = aggA.index.map(lambda x: int(hashlib.md5(str(x).encode()).hexdigest(), 16) % 2 == 0)
        def hr(H):
            x, y = H.loc[H["in_B"], "r"], H.loc[~H["in_B"], "r"]
            return (x.mean() / y.mean()) if len(x) >= 8 and len(y) >= 8 and y.mean() > 0 else None
        ra, rb = hr(aggA[h]), hr(aggA[~h])
        if ra is None or rb is None or (ratio > 1) != (ra > 1) or (ratio > 1) != (rb > 1):
            continue
        out.append(dict(type="cross_table", evidence="MEASURED",
            claim=f"entities also present in {nB} earn {ratio:.1f}x '{numA}' in {nA} "
                  f"(MW p={pv:.1e}; join on {A['id']}, {overlap:.0%} overlap)",
            cohort_code=f"df_a[df_a['{A['id']}'].isin(df_b['{B['id']}'])]",
            stats=dict(ratio=round(float(ratio), 2), p=float(pv), overlap=round(float(overlap), 2)),
            replication=f"entity-split PASS ({ra:.2f}, {rb:.2f})", metric=numA, table=nA))
    return out[:3], leads


_EXEC = {"trend": _exec_trend, "auto_bucket": _exec_auto_bucket,
         "rate_extra_numerator": _exec_extra_numerator,
         "lagged": _exec_lagged, "cross_table": _exec_cross_table}


# ───────────────────────────── niche tier (gap #6) ─────────────────────────────

def niche_scan(can):
    """Small niches: ratio >= 2.5x segments that FAIL the volume gate of the main
    rate scan are surfaced as explicitly-labeled HYPOTHESIS leads. Said — never
    sold: the label, the sample size, and the reason it isn't MEASURED are inline."""
    leads = []
    for base, lt in can.get("long_tables", {}).items():
        cands = _num_candidates(lt["measures"])
        if not cands: continue
        num = cands[0]
        d = lt["df"].copy(); d[num] = pd.to_numeric(d[num], errors="coerce").fillna(0)
        den = next((c for c in lt["measures"] if c != num and
                    any(p in _norm(c) for p in ("views", "watch", "impress", "session"))), None)
        dv = pd.to_numeric(d[den], errors="coerce").fillna(0) if den else pd.Series(1.0, index=d.index)
        tot_r, tot_d = d[num].sum(), dv.sum()
        if tot_r <= 0 or tot_d <= 0: continue
        overall = tot_r / tot_d
        gate = max(0.005 * tot_d, 1000) if den else 30
        floor = max(0.0004 * tot_d, 40) if den else 8
        for dim in [c for c in d.columns if (pd.api.types.is_string_dtype(d[c]) or d[c].dtype == object)
                    and c != lt["id"] and 2 <= d[c].nunique() <= 250 and not _norm(c).endswith("id")]:
            g = pd.DataFrame({"r": d[num], "d": dv, "k": d[dim]}).groupby("k").sum()
            g = g[(g["d"] < gate) & (g["d"] >= floor)]
            if g.empty: continue
            g["ratio"] = (g["r"] / g["d"].clip(lower=1e-9)) / overall
            for val, row in g[g["ratio"] >= 2.5].nlargest(2, "ratio").iterrows():
                leads.append(dict(type="small_niche", evidence="HYPOTHESIS",
                    claim=f"SMALL NICHE (below verification volume): {dim}={val} runs {row['ratio']:.1f}x "
                          f"the '{num}' rate on only {row['d']:,.0f} units — too small to verify, "
                          f"large enough to look at [{base}]",
                    cohort_code=f"df[df['{dim}']=='{val}']",
                    stats=dict(ratio=round(float(row["ratio"]), 1), denom=float(row["d"])),
                    replication="NOT replicated — sample below the volume gate by design; "
                                "collect more data or accept as a qualitative lead",
                    metric=num, dimension=dim, table=base,
                    _score=float(row["ratio"] * np.log10(max(row["d"], 10)))))
    leads.sort(key=lambda f: -f.pop("_score", 0))
    return leads[:3]
