"""WS-E §6.5 — Feature flags / ablation switches.

Every gap-closure component ships with a flag that cleanly disables it: with the
flag off, the system behaves as if the component was never built (no half-initialized
state). Flags exist for measurement (leave-one-out ablation) and double as production
kill-switches. Production runs everything ON; the v5.3 baseline is everything OFF.

Flags are read from (in priority order): explicit dict passed to run(), environment
variables (SPRNTLY_FLAG_<NAME>=0/1), then defaults below.
"""
import os

# Default = True (production posture). Ablation harness passes explicit dicts.
DEFAULTS = {
    # ── gap-closure workstreams (this spec) ──
    "ws_a_registry":   True,   # WS-A: Definition Registry + tier-cap rule
    "ws_b_feedback":   True,   # WS-B: feedback capture + nightly report (no inference effect expected)
    "ws_c_drift":      True,   # WS-C: schema fingerprinting + drift classification + staleness
    "ambiguity_guard": True,   # WS-A analysis-time guard: never silently pick between near-tied metric candidates
    "registry_metric_selection": True,  # OQ-2: CONFIRMED/STALE definition mappings drive numerator selection before name heuristics
    "question_layer":  True,   # v5.5: query-driven interface (qa.ask) over pre-verified findings
    # ── v5.6 capability expansion (Phase-2 #1, #2 + battery gaps) ──
    "sil_vendor_adapters": True,  # Phase-2 #1 prototype: vendor-dialect column-role dictionaries (top-10 tools)
    "analysis_router":     True,  # Phase-2 #2 prototype: propose→dispose routing beyond the fixed battery
    "trend_scan":          True,  # weekly/monthly/quarterly/annual trends (via router)
    "auto_bucketing":      True,  # raw numeric features → quartile bands → existing scans (via router)
    "multi_numerator":     True,  # scan every plausible numerator, not just the first (via router)
    "cross_table":         True,  # join up to 3 tables on shared entity ids (via router)
    "lagged_effects":      True,  # early-window behavior → late-window outcome (via router)
    "niche_tier":          True,  # small niches surfaced as labeled HYPOTHESIS leads
    "text_features":       True,  # v5.7 master switch: text/meaning layer
    "text_lexical":        True,  # v5.7 Layer 1: deterministic token/bigram signal
    "text_semantic":       True,  # v5.7 Layer 2: semantic labeler
    "text_semantic_openvocab": True,  # v5.8: open-vocab labeler (LLM/discovery) vs fixed keyword stand-in
    # ── v5.3 additive battery components (flagged retroactively for leave-one-out) ──
    "rate_scan":       True,   # rate-by-dimension primitive
    "dow_scan":        True,   # day-of-week scan (both directions)
    "spike_scan":      True,   # single-entity temporal spikes
    "cont_goals":      True,   # continuous-goal scans (Mann-Whitney + BH-FDR)
    "ops_alerts":      True,   # operational alerts channel
    "manifest":        True,   # representation manifest + coverage notes
    # ── v5.2 core is NOT flaggable: it is the system under test's floor, never ablated ──
}

def resolve(overrides=None):
    f = dict(DEFAULTS)
    for k in f:
        env = os.environ.get(f"SPRNTLY_FLAG_{k.upper()}")
        if env is not None:
            f[k] = env not in ("0", "false", "False")
    if overrides:
        f.update(overrides)
    return f
