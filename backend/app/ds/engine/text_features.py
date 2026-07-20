"""text_features.py — v5.7 text/meaning layer (closes the last big gap: title/description
blindness, bet-risk #1).

Architecture — the SAME propose→dispose invariant as the analysis router, extended to
unstructured text. Text understanding enters ONLY as feature PROPOSAL; it never computes
a number or asserts a finding:

  Layer 1 — LEXICAL (fully deterministic, real today):
      Tokenize a text column (title/description), build candidate boolean features
      "text contains <token/bigram>" for tokens above a frequency floor, then DISPOSE
      each through the standard machinery: group outcome by feature, effect size,
      Mann-Whitney, entity-split replication, and BH-FDR across ALL tokens in the column
      (many comparisons — strict correction is mandatory or every column yields false
      positives). Survivors that replicate → MEASURED; the rest are dropped or kept as
      exploratory INFERRED leads. This catches word-level meaning ("compilation",
      "temporada", "completa", "complete history") with zero LLM.

  Layer 2 — SEMANTIC LABELER (interface + deterministic stand-in now; LLM in production):
      A labeler reads each distinct text value and PROPOSES categorical/boolean labels
      (genre, theme, tone, franchise, format): e.g. is_compilation, is_nostalgia,
      is_parody, is_news, is_tutorial. In this prototype the labeler is a curated
      keyword→label ruleset (LABELER_STANDIN) — exactly the role the SIL vendor
      dictionaries and the router proposer play. In production the labeler is a single
      LLM pass over the ~unique text values (cheap: dedupe first). Either way, the
      proposed labels become ordinary categorical/boolean columns and DISPOSE through
      the identical statistical battery. The LLM proposes "nostalgia"; the deterministic
      engine decides whether is_nostalgia=True actually earns 3.2x, replicated, and only
      THEN is it said — with cohort-as-code and a MEASURED tier.

Invariant preserved: the LLM/labeler can only ever cause a deterministic computation to
run. It cannot emit a claim, a number, or a tier. A hallucinated label that does not
predict the outcome simply fails the gates and is never reported — text hallucination is
contained by the same replication+FDR wall as everything else.

Flag: `text_features` (WS-E: its own measured delta; kill-switch for free).
Sub-flags: `text_lexical`, `text_semantic` (measure the two layers independently).
"""
from __future__ import annotations
import re
import numpy as np, pandas as pd
from scipy import stats

# English + Spanish stopwords (Atomo content is Mexican Spanish; a real deployment
# detects language per column — OQ: multilingual stopword selection).
_STOP = set("""a an the this that these those of in on at to for and or but with without by from as is are was were be been being it its it's your our their his her my we you they i he she them us
de la el los las un una unos unas y o pero con sin por para en al del lo se su sus mi tu que como es son fue ser
video official video oficial ft feat con vs part parte cap""".split())

_TOKEN = re.compile(r"[#]?[a-záéíóúñü0-9]+", re.I)


def _tokens(s, bigrams=True):
    toks = [t.lower() for t in _TOKEN.findall(str(s)) if len(t) > 2 and t.lower() not in _STOP]
    out = set(toks)
    if bigrams:
        out |= {f"{a} {b}" for a, b in zip(toks, toks[1:])}
    return out


# ── Layer 2 stand-in labeler (production replaces the body with one LLM pass) ──────────
# Each label = (name, keyword set). A production LLM assigns these (and open-vocabulary
# labels) by reading meaning; the stand-in matches curated keyword families so the
# capability is testable end-to-end in the prototype.
_LABEL_RULES = {
    "is_compilation": {"compilation", "complete", "completa", "completo", "full", "todos",
                       "temporada", "season", "recopilacion", "todas", "capitulos"},
    "is_nostalgia":   {"classic", "clasico", "clasica", "nostalgia", "retro", "old", "vieja",
                       "childhood", "infancia", "recuerdos", "90s", "80s"},
    "is_parody":      {"parody", "parodia", "meme", "funny", "humor", "comedia", "xd", "joke"},
    "is_news":        {"news", "noticias", "breaking", "ultima", "hora", "update", "actualidad"},
    "is_tutorial":    {"how", "tutorial", "guide", "guia", "como", "aprende", "tips", "learn"},
    "is_reaction":    {"reaction", "reaccion", "react", "reacciona", "reacting"},
}


def LABELER_STANDIN(unique_texts):
    """PROTOTYPE labeler: text → {label: bool}. Production swaps this for an LLM pass over
    the unique texts (dedupe keeps it cheap). Signature is the contract; internals change."""
    out = {}
    for t in unique_texts:
        toks = _tokens(t, bigrams=False)
        out[t] = {lbl: bool(toks & kws) for lbl, kws in _LABEL_RULES.items()}
    return out


def _num_candidates(measures):
    out = []
    for c in measures:
        k = re.sub(r"[^a-z]", "", str(c).lower())
        if any(p in k for p in ("revenue", "earning", "payout", "rev")) and "split" not in k and "fraction" not in k:
            out.append(c)
    return out


def _is_text_col(s):
    return (pd.api.types.is_string_dtype(s) or s.dtype == object) and \
           s.dropna().astype(str).str.len().mean() > 15 and s.nunique() > 20


def _halves(idx):
    import hashlib
    h = pd.Series(idx).map(lambda x: int(hashlib.md5(str(x).encode()).hexdigest(), 16) % 2 == 0)
    return h.values


def _dispose_feature(name, feat_bool, outcome, ids, kind, textcol, table, human_label):
    """Standard disposal for ONE proposed boolean text feature. Returns (record|None, pvalue)."""
    a = outcome[feat_bool]; b = outcome[~feat_bool]
    if len(a) < 30 or len(b) < 30 or b.mean() <= 0:
        return None, 1.0
    ratio = a.mean() / b.mean()
    if not (ratio >= 1.6 or ratio <= 0.62):
        return None, 1.0
    try:
        pv = stats.mannwhitneyu(a, b).pvalue
    except Exception:
        return None, 1.0
    return dict(_ratio=ratio, _p=pv, name=name, kind=kind, textcol=textcol, table=table,
                human_label=human_label, feat=feat_bool, ids=ids, outcome=outcome), pv


def _replicates(rec):
    h = _halves(rec["ids"])
    for mask in (h, ~h):
        fa = rec["feat"][mask]; oa = rec["outcome"][mask]
        if fa.sum() < 12 or (~fa).sum() < 12: return None
        x, y = oa[fa].mean(), oa[~fa].mean()
        if y <= 0: return None
        r = x / y
        if (rec["_ratio"] > 1) != (r > 1): return None
        if rec["_ratio"] > 1 and r < 1.25: return None
        if rec["_ratio"] < 1 and r > 0.8: return None
    return True


def scan_text(can, flags):
    """Propose text features (lexical + semantic) and dispose them deterministically.
    Returns (findings, leads). BH-FDR is applied per (column, layer) family."""
    do_lex = flags.get("text_lexical", True)
    do_sem = flags.get("text_semantic", True)
    findings, leads = [], []
    for base, lt in can.get("long_tables", {}).items():
        num = (_num_candidates(lt["measures"]) or [None])[0]
        if num is None: continue
        df = lt["df"]
        outcome = pd.to_numeric(df[num], errors="coerce").fillna(0.0)
        ids = df[lt["id"]].values if lt.get("id") in df else np.arange(len(df))
        textcols = [c for c in df.columns if _is_text_col(df[c])]
        for tc in textcols:
            text = df[tc].astype(str)
            proposals = []  # (record, pvalue) per family, FDR'd together

            # ── Layer 1: lexical tokens ──
            if do_lex:
                alltok = {}
                for i, s in enumerate(text):
                    for t in _tokens(s):
                        alltok.setdefault(t, []).append(i)
                n = len(df); floor = max(0.03 * n, 40)
                lex = []
                for tok, rows in alltok.items():
                    if len(rows) < floor or len(rows) > 0.85 * n:  # skip rare + near-universal
                        continue
                    mask = np.zeros(n, bool); mask[rows] = True
                    rec, pv = _dispose_feature(f"text '{tc}' contains \"{tok}\"", pd.Series(mask),
                                               outcome, ids, "lexical", tc, base, tok)
                    if rec: lex.append((rec, pv))
                _fdr(lex, 0.01)
                for rec in [r for r, keep in lex if keep]:
                    _emit(rec, findings, leads, num)

            # ── Layer 2: semantic labels. Open-vocabulary labeler (LLM in prod / corpus
            # discovery offline) unless text_semantic_openvocab is off, in which case the
            # fixed keyword stand-in is used (kept for measurement/comparison).
            if do_sem:
                if flags.get("text_semantic_openvocab", True):
                    from . import llm_labeler as LL
                    uniq = text.unique()
                    disc = LL.label(uniq)                    # open-vocab (LLM in prod / discovery offline)
                    # union with curated known themes so scattered-synonym themes (nostalgia,
                    # compilation) are also proposed — a production LLM proposes both in one pass;
                    # offline we combine the discovery miner with the known-theme ruleset.
                    known = LABELER_STANDIN(uniq)
                    labels = {}
                    for tx in uniq:
                        merged = dict(disc.get(tx, {}))
                        merged.update({k: v for k, v in known.get(tx, {}).items() if v or k not in merged})
                        labels[tx] = merged
                    label_names = sorted({k for v in labels.values() for k in v})
                else:
                    labels = LABELER_STANDIN(text.unique())
                    label_names = list(_LABEL_RULES)
                sem = []
                for lbl in label_names:
                    mask = text.map(lambda s: labels.get(s, {}).get(lbl, False)).values
                    if mask.sum() < max(0.03 * len(df), 40): continue
                    rec, pv = _dispose_feature(f"{lbl} (semantic label on '{tc}')", pd.Series(mask),
                                               outcome, ids, "semantic", tc, base, lbl)
                    if rec: sem.append((rec, pv))
                _fdr(sem, 0.01)
                for rec in [r for r, keep in sem if keep]:
                    _emit(rec, findings, leads, num)
    return findings, leads


def _fdr(pairs, q):
    """Benjamini-Hochberg in place: sets a 3rd tuple element keep=True/False."""
    if not pairs:
        return
    order = sorted(range(len(pairs)), key=lambda i: pairs[i][1])
    m = len(pairs); thresh = 0
    for rank, idx in enumerate(order, 1):
        if pairs[idx][1] <= q * rank / m:
            thresh = rank
    keep_idx = set(order[:thresh])
    for i in range(len(pairs)):
        pairs[i] = (pairs[i][0], i in keep_idx)


def _emit(rec, findings, leads, num):
    rep = _replicates(rec)
    ratio = rec["_ratio"]
    direction = "earn" if ratio > 1 else "earn only"
    base_claim = (f"{rec['name']}: rows {direction} {ratio:.1f}x the mean '{num}' "
                  f"(MW p={rec['_p']:.1e}) [{rec['table']}]")
    cohort = (f"df[df['{rec['textcol']}'].str.contains(r'\\b{re.escape(rec['human_label'])}\\b', case=False)]"
              if rec["kind"] == "lexical"
              else f"# label {rec['human_label']} proposed by text labeler on '{rec['textcol']}'")
    tier = "MEASURED" if rep else "INFERRED"
    note = ("entity-split replicated" if rep else
            "NOT replicated across entity halves — exploratory; treat as a lead")
    if rec["kind"] == "semantic":
        base_claim += "  [semantic label — proposed by the text labeler, verified deterministically]"
    r = dict(type=f"text_{rec['kind']}", evidence=tier, claim=base_claim, cohort_code=cohort,
             stats=dict(ratio=round(float(ratio), 2), p=float(rec["_p"])),
             replication=note, metric=num, dimension=rec["textcol"], table=rec["table"])
    (findings if tier == "MEASURED" else leads).append(r)
