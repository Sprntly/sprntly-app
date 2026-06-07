#!/usr/bin/env python3
"""
score.py — deterministic prioritization scoring for RICE, WSJF, VoC, North-Star.
Reads a JSON list of items from a file or stdin; prints a ranked table.

RICE  item keys: name, reach, impact, confidence(0-1 or %), effort   -> (reach*impact*confidence)/effort
WSJF  item keys: name, user_value, time_criticality, risk_reduction, job_size
                                                                     -> (uv+tc+rr)/job_size
VOC (problems, not features) item keys (each 0-1 unless noted):  [VoC Volume & Severity]
   name, impact, severity, strategic_fit, confidence, trend(modifier, default 1.0)
                                            -> impact*severity*strategic_fit*confidence*trend
   impact = converged reach (companies/accounts affected + analytics reach + churn + sales signal)
   confidence = data-quality multiplier (LOW it if only one signal or sampled/open-web)
NORTHSTAR (rank by modeled impact on the single North Star metric) item keys:
   name, ns_impact(the modeled NS-metric value, e.g. $ revenue), ns_low, ns_high(optional range)
                                            -> ranks by ns_impact (show the range + how it was modeled)

TWO MODES (choose with --mode):

  --mode plain   (default)  Rank by the framework score only. Any goal data on items is IGNORED.
                            Use when there is no goal to prioritize toward.

  --mode goal               Rank by framework score adjusted for goal-alignment. Use when
                            prioritizing toward a goal (Sprntly: a North Star + secondary metrics).
                            Items express goal-alignment in ONE of two ways:
                              (a) single goal:    "goal_fit": "high" | "med" | "low" | 0..1
                              (b) NS + secondary: "metric_fit": {"<metric>":"high", "<metric2>":"low", ...}
                                  Designate the North Star with --north-star "<metric>"; it gets
                                  --ns-weight (default 0.7), the secondary metrics split the rest.
                            Mapping: high=1.0, med=0.6, low=0.25, none/off=0.1 (or pass a 0-1 number).
                            --goal-weight W (default 1.0) blends the factor toward 1.0
                              (W=1 full effect, W=0 effectively plain). The framework score is
                              multiplied by the resulting 0-1 goal factor, so off-goal items sink.
                            Output shows BOTH the raw framework score and the goal-adjusted score,
                            plus each item's fit, so the ranking stays transparent and debatable.

Usage:
  python3 score.py --method rice items.json                          # plain
  python3 score.py --method rice --mode goal --north-star activation items.json   # NS + secondary
"""
import sys, json, argparse

def norm_conf(c):
    if c is None: return 1.0
    return c/100.0 if c > 1 else c

def score_item(method, it):
    if method == "rice":
        return (it["reach"] * it["impact"] * norm_conf(it.get("confidence", 1))) / max(it["effort"], 1e-9)
    if method == "wsjf":
        cod = it.get("user_value",0) + it.get("time_criticality",0) + it.get("risk_reduction",0)
        return cod / max(it.get("job_size",1), 1e-9)
    if method == "voc":
        # VoC Volume & Severity: all factors 0..1 except trend (a modifier, default 1.0)
        return (it["impact"] * it["severity"] * it.get("strategic_fit",1.0)
                * it.get("confidence",1.0) * it.get("trend",1.0))
    if method == "northstar":
        # rank purely by the modeled impact on the single North Star metric
        return it["ns_impact"]
    raise ValueError(f"unknown method {method}")

GOAL_FIT = {"high": 1.0, "med": 0.6, "medium": 0.6, "low": 0.25, "none": 0.1, "off": 0.1}

def fit_value(v):
    """Coerce a fit ('high'/'med'/'low' or 0-1 number) to a 0-1 float, or None if unrecognized."""
    if v is None: return None
    if isinstance(v, (int, float)):
        if v < 0: return 0.0
        return float(v) if v <= 1 else 1.0
    return GOAL_FIT.get(str(v).lower())

def goal_factor(it, ns_name, ns_weight, goal_weight):
    """Return (factor in 0-1, human-readable fit description or None) for --mode goal."""
    if goal_weight <= 0:
        return 1.0, None
    mf = it.get("metric_fit")
    if isinstance(mf, dict) and mf:
        # weighted average across metrics; North Star (if named & present) gets ns_weight
        keys = list(mf.keys())
        weights = {}
        if ns_name and ns_name in mf:
            weights[ns_name] = ns_weight
            others = [k for k in keys if k != ns_name]
            ow = (1 - ns_weight) / len(others) if others else 0.0
            for k in others: weights[k] = ow
        else:
            for k in keys: weights[k] = 1.0 / len(keys)
        num = den = 0.0; desc = []
        for k, v in mf.items():
            fv = fit_value(v)
            if fv is None: continue
            w = weights.get(k, 0.0)
            num += fv * w; den += w
            star = "*" if k == ns_name else ""
            desc.append(f"{k}{star}={v}")
        base = (num / den) if den > 0 else 1.0
        return base * goal_weight + (1 - goal_weight), ";".join(desc)
    gf = it.get("goal_fit")
    if gf is None:
        return 1.0, None  # neutral: no fit info given
    base = fit_value(gf)
    if base is None:
        return 1.0, str(gf)
    return base * goal_weight + (1 - goal_weight), gf

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", required=True, choices=["rice","wsjf","voc","northstar"])
    ap.add_argument("--mode", choices=["plain","goal"], default="plain",
                    help="plain = framework only (ignores goal data); goal = goal-aware ranking")
    ap.add_argument("--north-star", default=None, help="(goal mode) which metric_fit key is the North Star")
    ap.add_argument("--ns-weight", type=float, default=0.7, help="(goal mode) weight on the North Star metric")
    ap.add_argument("--goal-weight", type=float, default=1.0, help="(goal mode) 0=ignore goal, 1=full effect")
    ap.add_argument("path", nargs="?", default="-")
    a = ap.parse_args()
    raw = sys.stdin.read() if a.path == "-" else open(a.path, encoding="utf-8").read()
    items = json.loads(raw)

    scored = []
    for it in items:
        try:
            base = score_item(a.method, it)
            if a.mode == "goal":
                factor, fit = goal_factor(it, a.north_star, a.ns_weight, a.goal_weight)
                scored.append((it.get("name","?"), round(base*factor,3), fit, round(base,3)))
            else:
                scored.append((it.get("name","?"), round(base,3), None, round(base,3)))
        except KeyError as e:
            scored.append((it.get("name","?"), f"ERR missing {e}", None, None))

    ranked = sorted([s for s in scored if isinstance(s[1],(int,float))], key=lambda x:-x[1])
    errs   = [s for s in scored if not isinstance(s[1],(int,float))]

    if a.mode == "goal":
        ns = f", north-star={a.north_star}, ns-weight={a.ns_weight}" if a.north_star else ""
        print(f"# {a.method.upper()} ranking — GOAL mode (goal-weight={a.goal_weight}{ns})")
        for i,(n,s,fit,base) in enumerate(ranked,1):
            print(f"{i:>2}. {n:<26} {s:<10} (raw={base}, fit={fit})")
    else:
        print(f"# {a.method.upper()} ranking — PLAIN mode (no goal)")
        for i,(n,s,fit,base) in enumerate(ranked,1):
            print(f"{i:>2}. {n:<26} {s}")
    for n,s,fit,base in errs:
        print(f"    {n:<26} {s}")

if __name__ == "__main__":
    main()
