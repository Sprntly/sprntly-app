"""llm_labeler.py — v5.8 production semantic labeler (open-vocabulary).

This replaces the hardcoded keyword ruleset (text_features.LABELER_STANDIN) with a
labeler that DISCOVERS themes from the corpus instead of matching a fixed list — the
last mile that makes the text layer match what a live LLM does.

Two implementations behind ONE interface `label(unique_texts) -> {text: {theme: bool}}`:

  1. llm_label()  — REAL production path. One LLM pass proposes a small set of recurring
     themes actually present in the titles (open vocabulary — genres, formats, franchises,
     tones it observes), then a second pass tags each unique title against those themes.
     Deduping to unique texts keeps it cheap. Uses the Anthropic Messages API. This is the
     code that runs in production; it is exercised whenever ANTHROPIC_API_KEY is present.

  2. discover_label() — OPEN-VOCABULARY FALLBACK when no API key is available (e.g. this
     sandbox, offline CI). Genuinely open-vocab, NOT the hardcoded stand-in: it mines the
     corpus for recurrent multi-word phrase clusters and high-signal tokens, groups
     morphological/co-occurring variants, and emits those discovered clusters as themes.
     It will surface a "boss fight" or "underdog sports" theme if the corpus contains one,
     which the fixed keyword labeler never could.

CRITICAL — the invariant is unchanged: whichever labeler runs, it only PROPOSES boolean
theme features. The deterministic battery (text_features._emit / gates / BH-FDR /
entity-split replication) decides whether a theme actually predicts the outcome. A
hallucinated or spurious theme fails the gates and is never reported. The labeler cannot
emit a number, a claim, or a tier.

Selection: text_features.scan_text calls `active_labeler()`, which returns llm_label when
a key is present else discover_label — so production automatically uses the LLM and offline
runs stay deterministic and reproducible.
"""
from __future__ import annotations
import os, re, json, urllib.request, collections

_TOKEN = re.compile(r"[#]?[a-záéíóúñü0-9]+", re.I)
_STOP = set("""a an the this that these those of in on at to for and or but with without by from as is are was were be been being it its your our their his her my we you they i he she them us
de la el los las un una unos unas y o pero con sin por para en al del lo se su sus mi tu que como es son fue ser
video official oficial ft feat con vs part parte cap el la un del los las clip today new best top real""".split())


# ────────────────────────────── production LLM path ──────────────────────────────

def _api(messages, max_tokens=1500, model="claude-sonnet-4-6"):
    key = os.environ.get("ANTHROPIC_API_KEY")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({"model": model, "max_tokens": max_tokens, "messages": messages}).encode(),
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = json.loads(r.read())
    return "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text")


def llm_label(unique_texts, max_themes=12):
    """REAL production labeler. Two LLM passes over the deduped unique texts:
    (1) propose the recurring themes present; (2) tag each text against them.
    Returns {text: {theme: bool}}. Raises if no API key (caller selects via active_labeler)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("no ANTHROPIC_API_KEY — use discover_label()")
    texts = list(unique_texts)
    sample = texts[:400]                       # propose themes from a sample (cheap, sufficient)
    propose_prompt = (
        "You are a data-labeling assistant. Below are titles/descriptions from one dataset. "
        f"Identify up to {max_themes} recurring THEMES that group these items — genres, formats, "
        "franchises, tones, or topics you actually observe. Return ONLY a JSON array of short "
        "snake_case theme names (e.g. [\"is_compilation\",\"is_boss_fight\",\"is_underdog_sports\"]). "
        "No prose.\n\nITEMS:\n" + "\n".join(f"- {t}" for t in sample))
    themes = []
    try:
        raw = _api([{"role": "user", "content": propose_prompt}], max_tokens=400)
        themes = [t for t in json.loads(raw[raw.index("["): raw.rindex("]") + 1]) if re.match(r"[a-z_]+$", t)]
    except Exception:
        themes = []
    if not themes:
        return discover_label(unique_texts)
    # tag each unique text against the proposed themes, in batches
    out = {}
    for i in range(0, len(texts), 60):
        batch = texts[i:i + 60]
        tag_prompt = (
            "For each item, return which of these themes apply. Themes: " + json.dumps(themes) +
            "\nReturn ONLY a JSON array, one object per item in order, each {\"i\":<index>,"
            "\"themes\":[...]} listing the applicable theme names.\n\nITEMS:\n" +
            "\n".join(f"{j}. {t}" for j, t in enumerate(batch)))
        try:
            raw = _api([{"role": "user", "content": tag_prompt}], max_tokens=2000)
            arr = json.loads(raw[raw.index("["): raw.rindex("]") + 1])
            for obj in arr:
                idx = obj.get("i")
                if isinstance(idx, int) and 0 <= idx < len(batch):
                    ap = set(obj.get("themes", []))
                    out[batch[idx]] = {th: (th in ap) for th in themes}
        except Exception:
            for t in batch:
                out.setdefault(t, {th: False for th in themes})
    for t in texts:
        out.setdefault(t, {th: False for th in themes})
    return out


# ─────────────────────── open-vocabulary discovery fallback ───────────────────────

def _toks(s):
    return [t.lower() for t in _TOKEN.findall(str(s)) if len(t) > 2 and t.lower() not in _STOP]


def discover_label(unique_texts, max_themes=14, min_support=0.02):
    """Open-vocabulary theme discovery WITHOUT an LLM. Mines recurrent phrases and
    high-signal tokens from the corpus itself and emits them as themes — so it surfaces
    whatever themes the data actually contains (a 'boss_fight' cluster, a 'temporada'
    cluster), not a fixed list. This is the offline stand-in for the LLM proposer; it is
    genuinely open-vocab (themes come from the data), unlike the old keyword ruleset."""
    texts = list(unique_texts)
    n = len(texts)
    if n == 0:
        return {}
    floor = max(int(min_support * n), 8)
    tok_docs = collections.Counter()
    bg_docs = collections.Counter()
    for t in texts:
        toks = _toks(t)
        for x in set(toks):
            tok_docs[x] += 1
        for a, b in set(zip(toks, toks[1:])):
            bg_docs[f"{a} {b}"] += 1
    # candidate theme seeds: frequent-but-not-universal phrases first, then tokens
    seeds = []
    for phrase, c in bg_docs.most_common():
        if floor <= c <= 0.8 * n:
            seeds.append(phrase)
    for tok, c in tok_docs.most_common():
        if floor <= c <= 0.6 * n and not any(tok in s.split() for s in seeds):
            seeds.append(tok)
    # merge seeds that co-occur heavily into a single theme (morphological/synonym grouping)
    chosen, used = [], set()
    for s in seeds:
        if s in used:
            continue
        key = s.split()[0][:5]                 # crude stem-group key
        variants = [z for z in seeds if z not in used and (z.split()[0][:5] == key or z == s)]
        used.update(variants)
        name = "theme_" + re.sub(r"[^a-z0-9]+", "_", s.strip())[:24]
        chosen.append((name, set(variants)))
        if len(chosen) >= max_themes:
            break
    out = {}
    for t in texts:
        toks = set(_toks(t)); joined = " " + " ".join(_toks(t)) + " "
        labels = {}
        for name, variants in chosen:
            hit = any((" " + v + " ") in joined or v in toks for v in variants)
            labels[name] = bool(hit)
        out[t] = labels
    return out


# ─────────────────────────────────── selector ────────────────────────────────────

def active_labeler():
    """Sprntly-vendored: always the offline open-vocabulary discovery labeler.

    The upstream prototype selected llm_label() when ANTHROPIC_API_KEY was set,
    but that path calls the Anthropic API directly with the raw env key and a
    hardcoded model — bypassing the LLM gateway (per-company BYOK key routing,
    telemetry, model tiering). Until the labeler is routed through
    app.graph.gateway.llm_call with an enterprise_id, the deterministic
    discovery fallback is the only labeler allowed to run in the backend.
    """
    return discover_label


def label(unique_texts):
    return active_labeler()(list(unique_texts))
