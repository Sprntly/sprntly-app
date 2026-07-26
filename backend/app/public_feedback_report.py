"""Public-feedback report — structured data → fixed HTML template.

The `public-feedback-report` skill (v2) renders a report-style document: what
people are saying about the company on public platforms, a 24-month volume &
sentiment chart, the problems in the users' own words, new / stuck / fixed,
the feedback mix, platform and competitor cuts, product-only recommendations,
and the non-product section — plus a machine-readable metadata block the
skill's query mode answers follow-ups from. The reference standard is the
skill's `examples/strava-public-feedback-report.html` — this template is that
design, pinned.

Same architecture as app.voc_report: the model emits ONLY the report's data as
JSON (`SCHEMA`) and this module's deterministic template (`render_html`)
populates the pinned HTML/CSS — pixel-stable across runs, and XSS-safe: every
model-supplied string is HTML-escaped, and the report renders inside the
frontend's sandboxed, script-less iframe. The monthly chart is computed here
as inline SVG from the month rows — the model never draws.

Deliberate divergences from the reference file (which is a standalone page):
no print button and no PRD/backlog buttons (onclick is dead in the script-less
iframe — the real actions live outside it), and the coverage panel collapses
with <details>/<summary> instead of a JS toggle.
"""
from __future__ import annotations

import html
import json

_STYLE = """
:root{--ink:#1a1813;--ink2:#403c34;--soft:#8a857a;--line:#e8e6e0;--rule:#d8d5cd;--page:#fff;--bg:#fff;
--accent:#1f6f52;--red:#bb463c;--red-s:#fbecea;--amber:#a9762a;--amber-s:#f7efdd;--teal:#1f7a5a;--teal-s:#e9f3ed;--blue:#3a5a9a;--blue-s:#eaeef8;--purple:#8a6fae;--neutral:#7a756a;
--serif:'Newsreader',Georgia,serif;--sans:'Inter',system-ui,sans-serif;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font-family:var(--serif);line-height:1.6;-webkit-font-smoothing:antialiased;padding:24px 16px 64px}
.page{max-width:810px;margin:0 auto;background:var(--page);padding:34px 56px 54px}
.eyebrow{font-family:var(--sans);font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--accent);font-weight:700}
h1{font-weight:600;font-size:32px;line-height:1.15;margin:9px 0 6px}
.stamp{font-family:var(--sans);font-size:11.5px;color:var(--soft);margin-bottom:13px}
.runline{font-family:var(--sans);font-size:12.5px;color:var(--ink2);line-height:1.7;border-top:1px solid var(--rule);border-bottom:1px solid var(--rule);padding:13px 0}
.runline b{color:var(--ink)}
h2{font-weight:600;font-size:13px;font-family:var(--sans);letter-spacing:.12em;text-transform:uppercase;color:var(--accent);margin:36px 0 0;padding-bottom:7px;border-bottom:2px solid var(--accent);display:inline-block}
.sec{margin-top:8px}
.scopenote{font-family:var(--sans);font-size:11.5px;color:var(--soft);font-style:italic;margin-top:10px}
.tldr .src{font-style:italic;color:var(--ink2);font-size:15px;margin-top:15px}
.tldr .intro{font-size:16.5px;margin-top:12px}.tldr .intro b{font-weight:600}
.enum .item{font-size:16px;line-height:1.55;margin:14px 0}
.enum .hash{font-family:var(--sans);font-weight:700;color:var(--accent);font-size:14.5px;margin-right:6px}
.enum .item b{font-weight:600}
.enum .sub{font-family:var(--sans);display:block;font-size:12.5px;color:var(--ink2);margin-top:4px;line-height:1.6}
.enum .vq{display:block;font-size:14px;font-style:italic;color:var(--ink2);margin-top:5px;padding-left:16px;border-left:2px solid var(--line);margin-left:4px}
.tldr .close{font-size:16.5px;margin-top:16px;padding-top:14px;border-top:1px dashed var(--line)}.tldr .close b{font-weight:600;color:var(--accent)}
.chartwrap{margin-top:16px;border:1px solid var(--line);border-radius:10px;padding:16px 18px 10px;background:#fdfdfc}
.legend{font-family:var(--sans);display:flex;gap:18px;font-size:12px;color:var(--ink2);margin-bottom:6px;flex-wrap:wrap}
.legend span{display:inline-flex;align-items:center;gap:6px}.dot{width:14px;height:3px;border-radius:2px;display:inline-block}
.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:14px}
.kpi{font-family:var(--sans);border:1px solid var(--line);border-radius:9px;padding:11px 13px;background:#fff}
.kpi .l{font-size:10px;letter-spacing:.05em;text-transform:uppercase;color:var(--soft);font-weight:700}
.kpi .v{font-size:18px;font-weight:700;margin-top:3px}.kpi .v small{font-size:11.5px;font-weight:600;color:var(--soft)}
.down{color:var(--red)}.up{color:var(--teal)}
.chartnote{font-family:var(--sans);font-size:12px;color:var(--soft);margin-top:10px;font-style:italic}.chartnote b{color:var(--ink2)}
.tt,.plat,.cmp{font-family:var(--sans);width:100%;border-collapse:collapse;font-size:12.5px;margin-top:16px;border:1px solid var(--rule);border-radius:8px;overflow:hidden}
.tt th,.plat th,.cmp th{background:#f6f5f1;text-align:left;padding:9px 11px;font-size:10px;letter-spacing:.04em;text-transform:uppercase;color:var(--ink2);font-weight:700}
.tt td,.plat td,.cmp td{padding:11px;border-top:1px solid var(--line);vertical-align:top;color:var(--ink2)}
.tt td b,.plat td b,.cmp td b{color:var(--ink)}
.pq{font-family:var(--serif);font-size:14.5px;color:var(--ink);line-height:1.4}
.pgloss{display:block;font-family:var(--sans);font-size:11.5px;color:var(--soft);margin-top:3px}
.sent{font-weight:700;font-size:11px;padding:2px 7px;border-radius:5px;white-space:nowrap}
.neg{background:var(--red-s);color:var(--red)}.pos{background:var(--teal-s);color:var(--teal)}.mix{background:var(--amber-s);color:var(--amber)}.neu{background:#efeee9;color:var(--neutral)}
.tr-up{color:var(--red);font-weight:700}.tr-flat{color:var(--soft);font-weight:700}.tr-down{color:var(--teal);font-weight:700}
.tq{font-style:italic;color:var(--ink2);font-family:var(--serif);font-size:13px}.tq .pf{font-style:normal;font-family:var(--sans);font-size:10.5px;color:var(--soft);font-weight:600;display:block;margin-top:2px}
.split{display:grid;grid-template-columns:2fr 1fr;gap:16px;margin-top:18px;align-items:start}
.temporal{display:grid;grid-template-columns:1fr;gap:12px}
.tcol{border:1px solid var(--line);border-radius:10px;padding:14px 15px;border-top:3px solid var(--tc)}
.tcol.long{--tc:var(--red)}.tcol.new{--tc:var(--amber)}.tcol.gone{--tc:var(--teal)}
.tcol h4{font-family:var(--sans);font-size:11.5px;text-transform:uppercase;letter-spacing:.05em;font-weight:700;color:var(--tc);margin-bottom:8px}
.tcol .it{font-size:14px;color:var(--ink);margin:8px 0;line-height:1.4}.tcol .it .d{font-family:var(--sans);display:block;font-size:10.5px;color:var(--soft);margin-top:2px}
.mixbox{border:1px solid var(--rule);border-radius:10px;padding:15px 16px;background:#fbfbf9}
.mixbox h4{font-family:var(--sans);font-size:11.5px;text-transform:uppercase;letter-spacing:.05em;font-weight:700;color:var(--ink2);margin-bottom:4px}
.mixbox .den{font-family:var(--sans);font-size:10.5px;color:var(--soft);line-height:1.5}
.mixgroup{margin-top:15px}
.mixlab{font-family:var(--sans);font-size:11px;font-weight:700;color:var(--ink2);display:flex;justify-content:space-between;margin-bottom:5px}
.mixlab span{color:var(--soft);font-weight:600}
.bar{display:flex;height:11px;border-radius:6px;overflow:hidden;background:#efeee9}
.bar i{display:block;height:100%}
.b-pos{background:var(--teal)}.b-neg{background:var(--red)}.b-neu{background:#c9c6bd}
.b-prod{background:var(--accent)}.b-non{background:#b8b3a6}
.mixkey{font-family:var(--sans);font-size:10.5px;color:var(--soft);margin-top:6px;display:flex;gap:11px;flex-wrap:wrap}
.mixkey b{color:var(--ink2);font-weight:700}
.mixnote{font-family:var(--sans);font-size:11px;color:var(--soft);line-height:1.55;margin-top:14px;border-top:1px solid var(--line);padding-top:11px}
.mixnote b{color:var(--ink2)}
.compgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:13px;margin-top:16px}
.lovecard{border:1px solid var(--line);border-radius:10px;padding:14px 15px;border-top:3px solid var(--lc)}
.lovecard.us{--lc:var(--teal)}.lovecard.a{--lc:var(--blue)}.lovecard.b{--lc:var(--purple)}
.lovecard .nm{font-family:var(--sans);font-size:13px;font-weight:700;color:var(--lc);margin-bottom:7px}
.lovecard .li{font-size:13.5px;color:var(--ink2);padding:5px 0;border-top:1px solid var(--line)}.lovecard .li:first-of-type{border-top:none}.lovecard .li b{color:var(--ink)}
.aheadtag{font-weight:700;font-size:11px;padding:2px 7px;border-radius:5px;white-space:nowrap}
.a-us{background:var(--teal-s);color:var(--teal)}.a-a{background:var(--blue-s);color:var(--blue)}.a-b{background:#efeaf6;color:var(--purple)}.a-none{background:#efeee9;color:var(--neutral)}
.switch{font-family:var(--sans);font-size:12px;color:var(--ink2);background:var(--red-s);border:1px solid #eccfc9;border-radius:8px;padding:11px 14px;margin-top:14px;line-height:1.65}.switch b{color:var(--red)}
.callout{font-family:var(--sans);font-size:12.5px;color:var(--ink2);background:#eef3f0;border:1px solid #cfe0d7;border-radius:8px;padding:11px 14px;margin-top:14px;line-height:1.65}.callout b{color:var(--ink)}
.recintro{font-family:var(--sans);font-size:12.5px;color:var(--ink2);background:#eef3f0;border:1px solid #cfe0d7;border-radius:8px;padding:10px 13px;margin-top:6px;line-height:1.6}
.reccard{border:1px solid var(--line);border-radius:11px;padding:15px 18px;margin-top:12px;background:#fff;border-left:4px solid var(--accent)}
.reccard .rh{display:flex;align-items:baseline;gap:11px;flex-wrap:wrap}
.recnum2{font-family:var(--sans);width:25px;height:25px;border-radius:6px;background:var(--accent);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13px;flex-shrink:0}
.reccard .rt{font-size:16.5px;font-weight:600}
.prio{font-family:var(--sans);margin-left:auto;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;padding:3px 9px;border-radius:5px}
.prio.hi{background:var(--red-s);color:var(--red)}.prio.med{background:var(--amber-s);color:var(--amber)}
.reccard .rm{font-family:var(--sans);font-size:12.5px;color:var(--ink2);margin-top:7px;line-height:1.6}.reccard .rm b{color:var(--ink)}
.reccard .prob{font-family:var(--serif);font-size:14.5px;color:var(--ink);margin-top:6px;font-style:italic}
.reccard .moves{font-family:var(--sans);font-size:11.5px;color:var(--accent);font-weight:600;margin-top:6px}
.nonprod{margin-top:14px;border:1px solid var(--rule);border-radius:11px;padding:16px 19px;background:#fbfaf7}
.nonprod .nph{font-family:var(--sans);font-size:12.5px;color:var(--ink2);line-height:1.6}.nonprod .nph b{color:var(--ink)}
.npitem{display:flex;gap:13px;padding:12px 0;border-top:1px solid var(--line);margin-top:12px}
.npitem:first-of-type{margin-top:14px}
.nptag{font-family:var(--sans);font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;padding:3px 8px;border-radius:5px;background:#ede9e0;color:#6f6a5f;height:fit-content;white-space:nowrap;flex-shrink:0;margin-top:2px}
.npbody .npt{font-size:15.5px;font-weight:600;line-height:1.3}
.npbody .npm{font-family:var(--sans);font-size:12px;color:var(--ink2);margin-top:4px;line-height:1.6}
.npbody .npo{font-family:var(--sans);font-size:11px;color:var(--soft);font-weight:600;margin-top:4px}
.nextsteps{margin-top:20px;border:1px solid var(--rule);border-radius:11px;padding:16px 19px;background:#fbfbf9}
.nextsteps h4{font-family:var(--sans);font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--soft);font-weight:700}
.nextsteps p{font-family:var(--sans);font-size:13px;color:var(--ink2);margin-top:7px;line-height:1.6}.nextsteps p b{color:var(--ink)}
.integrity{font-family:var(--sans);font-size:11.5px;line-height:1.65;color:var(--soft);margin-top:14px}.integrity b{color:var(--ink2)}.integrity p{margin-top:9px}
.metabar{font-family:var(--sans);margin-top:34px;border-top:1px solid var(--rule);padding-top:14px}
.metabar summary{font-size:11.5px;font-weight:600;color:var(--soft);cursor:pointer;letter-spacing:.02em;list-style:none}
.metabar summary::-webkit-details-marker{display:none}
.metabar summary::before{content:"\\25B8";display:inline-block;margin-right:5px;transition:transform .18s}
.metabar[open] summary::before{transform:rotate(90deg)}
.metabar summary:hover{color:var(--accent)}
.brandmark{position:absolute;top:0;right:0;font-family:var(--sans);font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--soft);font-weight:700}
.brandmark b{color:var(--accent);letter-spacing:.06em}
.pagehead{position:relative;padding-top:4px}
.countstrip{display:flex;gap:0;margin-top:16px;border:1px solid var(--rule);border-radius:9px;overflow:hidden}
.cs{flex:1;font-family:var(--sans);padding:10px 13px;border-left:1px solid var(--line)}
.cs:first-child{border-left:none}
.cs .l{font-size:9.5px;letter-spacing:.05em;text-transform:uppercase;color:var(--soft);font-weight:700}
.cs .v{font-size:17px;font-weight:700;color:var(--ink);margin-top:2px}
.cs .v small{font-size:11px;font-weight:600;color:var(--soft)}
@media(max-width:680px){.split,.kpis{grid-template-columns:1fr}.page{padding:28px 22px}.countstrip{flex-wrap:wrap}.cs{min-width:45%}}
"""

# ── JSON schema for the report DATA (the model returns this, never HTML) ──────


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required,
            "additionalProperties": False}


def _arr(items: dict) -> dict:
    return {"type": "array", "items": items}


_S = {"type": "string"}
_I = {"type": "integer", "minimum": 0}
_MOOD = {"type": "string", "enum": ["pos", "neg", "mix", "neu"]}
_KPI = _obj({"label": _S, "value": _S, "detail": _S,
             "tone": {"type": "string", "enum": ["up", "down", "flat"]}},
            ["label", "value", "detail", "tone"])
_DATED = _obj({"text": _S, "detail": _S}, ["text", "detail"])
_LOVE = _obj({"title": _S, "detail": _S}, ["title", "detail"])

SCHEMA: dict = _obj(
    {
        "eyebrow": _S,          # "Public feedback · January – July 2026"
        "prepared": _S,         # "Prepared 21 July 2026 · first edition, …"
        "covers": _S,           # runline: what this covers
        "looked": _S,           # runline: where we looked
        "answering": _S,        # runline: what we were trying to answer
        "counts": _obj({
            "collected": _I, "product": _I, "non_product": _I,
            "sources": _I, "leaving": _I,
        }, ["collected", "product", "non_product", "sources", "leaving"]),
        "tldr_source_line": _S,  # italic line: what's in / kept separate
        "tldr_intro": _S,
        "tldr": _arr(_obj({      # five points; #4 = leaving over, #5 = brand new
            "headline": _S,      # bolded, in the user's voice
            "sub": _S,           # plain gloss underneath
            "quote": _S,         # one real quote ("" ok)
            "quote_attr": _S,    # "Trustpilot, 24 Jan 2026" ("" ok)
        }, ["headline", "sub", "quote", "quote_attr"])),
        "first_move": _S,        # "What we'd do first: …" close
        "months": _arr(_obj({    # oldest→newest, up to 24; all-zero = coverage gap
            "label": _S,         # short axis label, e.g. "Aug 24" ("" = unlabeled)
            "pos": _I, "neu": _I, "neg": _I,
            "event": _S,         # annotation, e.g. "API terms change" ("" ok)
        }, ["label", "pos", "neu", "neg", "event"])),
        "chart_kpis": _arr(_KPI),     # busiest month / best month / last 3 months
        "chart_note": _S,             # prose read of the spikes ("" ok)
        "chart_callout": _S,          # coverage caution ("" ok)
        "ratings_kpis": _arr(_KPI),   # external ratings with conflicts shown
        "ratings_callout": _S,        # why the ratings disagree ("" ok)
        "problems": _arr(_obj({
            "voice": _S,         # the problem in the user's words
            "gloss": _S,         # what we'd be fixing · owner
            "how_often": _S,     # rank band: "Most often" / "High" / …
            "mood": _MOOD,
            "mood_label": _S,
            "direction": {"type": "string", "enum": ["up", "flat", "down"]},
            "direction_label": _S,   # "▲ new, rising" / "► chronic" / …
            "quote": _S,
            "quote_attr": _S,
        }, ["voice", "gloss", "how_often", "mood", "mood_label",
            "direction", "direction_label", "quote", "quote_attr"])),
        "new_items": _arr(_DATED),    # didn't exist before this period
        "stuck_items": _arr(_DATED),  # raised for a long time, still live
        "fixed_items": _arr(_DATED),  # people have stopped raising it
        "mix": _obj({                 # sentiment splits, over collected records
            "product_pos": _I, "product_neu": _I, "product_neg": _I,
            "non_pos": _I, "non_neu": _I, "non_neg": _I,
            "note": _S,               # things to keep in mind ("" ok)
        }, ["product_pos", "product_neu", "product_neg",
            "non_pos", "non_neu", "non_neg", "note"]),
        "progress_callout": _S,       # proof-of-progress note ("" ok)
        "platforms": _arr(_obj({
            "name": _S, "audience": _S, "mood": _MOOD, "mood_label": _S,
            "what": _S,
        }, ["name", "audience", "mood", "mood_label", "what"])),
        "platforms_note": _S,         # why the audiences differ ("" ok)
        "compare_title": _S,          # "How we compare vs X & Y" ("" = section omitted)
        "why_these": _S,              # which competitors and why
        "us_name": _S,                # display name for the company's own card
        "us_loves": _arr(_LOVE),
        "rivals": _arr(_obj({         # 0–2 rivals
            "name": _S, "loves": _arr(_LOVE),
        }, ["name", "loves"])),
        "dimensions": _arr(_obj({     # win/lose table on product dimensions
            "name": _S,
            "us": _S, "us_mood": _MOOD,
            "cells": _arr(_obj({"text": _S, "mood": _MOOD}, ["text", "mood"])),
            "ahead": {"type": "string", "enum": ["us", "a", "b", "none"]},
            "ahead_label": _S,
        }, ["name", "us", "us_mood", "cells", "ahead", "ahead_label"])),
        "switching": _S,              # who said they're leaving, exactly ("" ok)
        "recs_intro": _S,
        "recommendations": _arr(_obj({
            "title": _S,
            "prio_label": _S,        # "High impact" / "Cheap win"
            "prio": {"type": "string", "enum": ["hi", "med"]},
            "problem_line": _S,      # the user-facing problem it leads with
            "why": _S,
            "moves": _S,
        }, ["title", "prio_label", "prio", "problem_line", "why", "moves"])),
        "non_product_intro": _S,      # the true proportion, stated plainly
        "non_product_items": _arr(_obj({
            "tag": _S, "title": _S, "detail": _S, "owner_line": _S,
        }, ["tag", "title", "detail", "owner_line"])),
        "next_steps": _S,             # what to move on first
        "integrity": _arr(_obj({      # collapsed coverage note paragraphs
            "title": _S, "text": _S,
        }, ["title", "text"])),
        "metadata": {"type": "object", "additionalProperties": True},
    },
    ["eyebrow", "prepared", "covers", "looked", "answering", "counts",
     "tldr_source_line", "tldr_intro", "tldr", "first_move", "months",
     "chart_kpis", "chart_note", "chart_callout", "ratings_kpis",
     "ratings_callout", "problems", "new_items", "stuck_items", "fixed_items",
     "mix", "progress_callout", "platforms", "platforms_note", "compare_title",
     "why_these", "us_name", "us_loves", "rivals", "dimensions", "switching",
     "recs_intro", "recommendations", "non_product_intro", "non_product_items",
     "next_steps", "integrity", "metadata"],
)

_SENT_COLORS = {"pos": "#1f7a5a", "neu": "#d9a441", "neg": "#bb463c"}
_RIVAL_CLASSES = ("a", "b")


# ── Rendering (deterministic; all model text HTML-escaped) ────────────────────

def _e(s) -> str:
    """HTML-escape any model-supplied value (None → '')."""
    return html.escape(str(s)) if s is not None else ""


def _i(v) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def _pct(part: int, whole: int) -> int:
    return round(100 * part / whole) if whole else 0


def _month_chart(months: list[dict]) -> str:
    """24-month stacked volume/sentiment bars as inline SVG, computed from the
    month rows (never model-drawn). All-zero months render as flat gap lines.
    Returns "" when there are no months at all."""
    months = months[-24:]
    if not months:
        return ""
    totals = [_i(m.get("pos")) + _i(m.get("neu")) + _i(m.get("neg")) for m in months]
    vmax = max(totals + [1])
    gaps = sum(1 for t in totals if t == 0)
    left, right, top, base = 52.0, 648.0, 30.0, 176.0
    span = right - left
    slot = span / len(months)
    bar_w = min(15.5, slot * 0.62)
    y_scale = (base - top) / vmax

    parts: list[str] = []
    # y gridlines: 0, half, max
    for val in {0, vmax // 2, vmax}:
        y = base - val * y_scale
        parts.append(f'<line x1="{left:.0f}" y1="{y:.1f}" x2="{right:.0f}" y2="{y:.1f}" stroke="#eeece6"/>')
        parts.append(f'<text x="{left - 8:.0f}" y="{y + 3:.1f}" text-anchor="end" font-size="9" fill="#9a958a">{val}</text>')
    for idx, m in enumerate(months):
        cx = left + slot * idx + slot / 2
        x = cx - bar_w / 2
        total = totals[idx]
        if m.get("event"):
            # Clamp the label so a verbose annotation can't overflow the
            # 660-unit viewBox or collide with its neighbours.
            event = str(m.get("event"))[:28]
            parts.append(
                f'<line x1="{cx:.1f}" y1="22" x2="{cx:.1f}" y2="{base:.0f}" '
                'stroke="#c8c3b6" stroke-width="1" stroke-dasharray="2 3"/>'
                f'<text x="{cx:.1f}" y="19" text-anchor="middle" font-size="8.5" '
                f'fill="#8a857a" font-weight="600">{_e(event)}</text>'
            )
        if total == 0:
            parts.append(
                f'<line x1="{x:.1f}" y1="{base + 1:.0f}" x2="{x + bar_w:.1f}" '
                f'y2="{base + 1:.0f}" stroke="#d8d5cd" stroke-width="2.5"/>'
            )
        else:
            y = base
            for key in ("pos", "neu", "neg"):
                h = _i(m.get(key)) * y_scale
                if h <= 0:
                    continue
                y -= h
                parts.append(
                    f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
                    f'height="{h:.1f}" fill="{_SENT_COLORS[key]}"/>'
                )
            parts.append(
                f'<text x="{cx:.1f}" y="{y - 4:.1f}" text-anchor="middle" '
                f'font-size="8.5" fill="#8a857a" font-weight="600">{total}</text>'
            )
        if m.get("label"):
            parts.append(
                f'<text x="{cx:.1f}" y="192" text-anchor="middle" font-size="9.5" '
                f'fill="#6f6a5f">{_e(m.get("label"))}</text>'
            )
    parts.append(f'<line x1="{left:.0f}" y1="{base:.0f}" x2="{right:.0f}" y2="{base:.0f}" stroke="#d8d5cd"/>')
    if gaps:
        parts.append(
            f'<text x="{left:.0f}" y="210" font-size="9" fill="#a5a096">— flat '
            f"line = no feedback collected that month ({gaps} of the "
            f"{len(months)} months)</text>"
        )
    legend = (
        '<div class="legend">'
        + "".join(
            f'<span><span class="dot" style="background:{_SENT_COLORS[k]}"></span>{lbl}</span>'
            for k, lbl in (("pos", "Positive"), ("neu", "Neutral"), ("neg", "Negative"))
        )
        + "</div>"
    )
    return (
        f'<div class="chartwrap">{legend}'
        '<svg viewBox="0 0 660 220" width="100%" xmlns="http://www.w3.org/2000/svg" '
        'font-family="Inter,sans-serif" role="img" '
        'aria-label="Monthly public feedback volume by sentiment">'
        + "".join(parts)
        + "</svg></div>"
    )


def _kpi(k: dict) -> str:
    tone = {"up": " up", "down": " down"}.get(str(k.get("tone")), "")
    return (
        f'<div class="kpi"><div class="l">{_e(k.get("label"))}</div>'
        f'<div class="v{tone}">{_e(k.get("value"))} '
        f'<small>{_e(k.get("detail"))}</small></div></div>'
    )


def _kpis(items: list[dict]) -> str:
    return f'<div class="kpis">{"".join(_kpi(k) for k in items)}</div>' if items else ""


def _tldr_item(i: int, t: dict) -> str:
    quote = ""
    if t.get("quote"):
        attr = f" — {_e(t.get('quote_attr'))}" if t.get("quote_attr") else ""
        quote = f'<span class="vq">"{_e(t.get("quote"))}"{attr}</span>'
    return (
        f'<div class="item"><span class="hash">#{i}</span><b>{_e(t.get("headline"))}</b>'
        f'<span class="sub">{_e(t.get("sub"))}</span>{quote}</div>'
    )


def _mood_chip(mood: str, label: str) -> str:
    cls = mood if mood in ("pos", "neg", "mix", "neu") else "neu"
    return f'<span class="sent {cls}">{_e(label)}</span>'


def _problem_row(p: dict) -> str:
    direction = p.get("direction") if p.get("direction") in ("up", "flat", "down") else "flat"
    quote_cell = f'"{_e(p.get("quote"))}"' if p.get("quote") else ""
    attr = f'<span class="pf">{_e(p.get("quote_attr"))}</span>' if p.get("quote_attr") else ""
    return (
        f'<tr><td><span class="pq">"{_e(p.get("voice"))}"</span>'
        f'<span class="pgloss">{_e(p.get("gloss"))}</span></td>'
        f'<td>{_e(p.get("how_often"))}</td>'
        f"<td>{_mood_chip(str(p.get('mood')), p.get('mood_label'))}</td>"
        f'<td class="tr-{direction}">{_e(p.get("direction_label"))}</td>'
        f'<td class="tq">{quote_cell}{attr}</td></tr>'
    )


def _tcol(cls: str, heading: str, items: list[dict]) -> str:
    if not items:
        return ""
    rows = "".join(
        f'<div class="it">{_e(it.get("text"))}<span class="d">{_e(it.get("detail"))}</span></div>'
        for it in items
    )
    return f'<div class="tcol {cls}"><h4>{heading}</h4>{rows}</div>'


def _mix_bar(segments: list[tuple[str, int]], total: int) -> str:
    bars = "".join(
        f'<i class="{cls}" style="width:{_pct(n, total)}%"></i>'
        for cls, n in segments if n > 0
    )
    return f'<div class="bar">{bars}</div>'


def _mix_box(counts: dict, mix: dict) -> str:
    collected = _i(counts.get("collected"))
    product = _i(counts.get("product"))
    non = _i(counts.get("non_product"))
    ppos, pneu, pneg = (_i(mix.get(k)) for k in ("product_pos", "product_neu", "product_neg"))
    npos, nneu, nneg = (_i(mix.get(k)) for k in ("non_pos", "non_neu", "non_neg"))
    note = f'<div class="mixnote">{_e(mix.get("note"))}</div>' if mix.get("note") else ""
    return (
        '<div class="mixbox"><h4>What the feedback is about</h4>'
        f'<div class="den">Out of the <b>{collected} pieces of feedback</b> we '
        "found. These are percentages of what we collected — not of your "
        "users.</div>"
        '<div class="mixgroup">'
        f'<div class="mixlab">Can a product team fix it? <span>{collected} posts</span></div>'
        f'{_mix_bar([("b-prod", product), ("b-non", non)], collected)}'
        f'<div class="mixkey"><span><b>{_pct(product, collected)}%</b> yes · {product}</span>'
        f'<span><b>{_pct(non, collected)}%</b> no · {non}</span></div></div>'
        '<div class="mixgroup">'
        f'<div class="mixlab">How people felt — product issues <span>{product} posts</span></div>'
        f'{_mix_bar([("b-pos", ppos), ("b-neu", pneu), ("b-neg", pneg)], product)}'
        f'<div class="mixkey"><span><b>{_pct(ppos, product)}%</b> positive</span>'
        f'<span><b>{_pct(pneu, product)}%</b> neutral</span>'
        f'<span><b>{_pct(pneg, product)}%</b> negative</span></div></div>'
        '<div class="mixgroup">'
        f'<div class="mixlab">How people felt — everything else <span>{non} posts</span></div>'
        f'{_mix_bar([("b-pos", npos), ("b-neu", nneu), ("b-neg", nneg)], non)}'
        f'<div class="mixkey"><span><b>{_pct(npos, non)}%</b> positive</span>'
        f'<span><b>{_pct(nneu, non)}%</b> neutral</span>'
        f'<span><b>{_pct(nneg, non)}%</b> negative</span></div></div>'
        f"{note}</div>"
    )


def _lovecard(cls: str, name: str, loves: list[dict]) -> str:
    rows = "".join(
        f'<div class="li"><b>{_e(l.get("title"))}</b> — {_e(l.get("detail"))}</div>'
        for l in loves
    )
    return f'<div class="lovecard {cls}"><div class="nm">{_e(name)}</div>{rows}</div>'


def _dimension_row(d: dict, n_rivals: int) -> str:
    cells = list(d.get("cells") or [])[:n_rivals]
    while len(cells) < n_rivals:
        cells.append({"text": "—", "mood": "neu"})
    rival_tds = "".join(
        f"<td>{_mood_chip(str(c.get('mood')), c.get('text'))}</td>" for c in cells
    )
    ahead = d.get("ahead") if d.get("ahead") in ("us", "a", "b", "none") else "none"
    return (
        f'<tr><td><b>{_e(d.get("name"))}</b></td>'
        f"<td>{_mood_chip(str(d.get('us_mood')), d.get('us'))}</td>"
        f"{rival_tds}"
        f'<td><span class="aheadtag a-{ahead}">{_e(d.get("ahead_label"))}</span></td></tr>'
    )


def _rec_card(i: int, r: dict) -> str:
    # No CTAs inside the report: it renders in a script-less sandboxed iframe
    # where nothing is clickable — the real PRD/backlog actions live outside it.
    prio = "hi" if r.get("prio") == "hi" else "med"
    return (
        '<div class="reccard"><div class="rh">'
        f'<span class="recnum2">{i}</span><span class="rt">{_e(r.get("title"))}</span>'
        f'<span class="prio {prio}">{_e(r.get("prio_label"))}</span></div>'
        f'<div class="prob">"{_e(r.get("problem_line"))}"</div>'
        f'<div class="rm"><b>Why:</b> {_e(r.get("why"))}</div>'
        f'<div class="moves">Moves: {_e(r.get("moves"))}</div></div>'
    )


def _np_item(it: dict) -> str:
    return (
        f'<div class="npitem"><span class="nptag">{_e(it.get("tag"))}</span>'
        f'<div class="npbody"><div class="npt">{_e(it.get("title"))}</div>'
        f'<div class="npm">{_e(it.get("detail"))}</div>'
        f'<div class="npo">{_e(it.get("owner_line"))}</div></div></div>'
    )


def _metadata_script(metadata: dict) -> str:
    """The machine-readable rollup as an inert JSON block. `<` is escaped to
    \\u003c so model-supplied strings can never close the script element."""
    payload = json.dumps(metadata or {}, ensure_ascii=False).replace("<", "\\u003c")
    return f'<script type="application/json" id="report-metadata">{payload}</script>'


def render_html(data: dict) -> str:
    """Populate the pinned public-feedback template from the model's report
    data. Returns a self-contained HTML document."""
    counts = data.get("counts") or {}
    collected = _i(counts.get("collected"))
    product = _i(counts.get("product"))
    non = _i(counts.get("non_product"))

    tldr = "".join(_tldr_item(i + 1, t) for i, t in enumerate(data.get("tldr") or []))
    chart = _month_chart(list(data.get("months") or []))
    chart_note = (
        f'<p class="chartnote">{_e(data.get("chart_note"))}</p>'
        if data.get("chart_note") else ""
    )
    chart_callout = (
        f'<div class="callout">{_e(data.get("chart_callout"))}</div>'
        if data.get("chart_callout") else ""
    )
    ratings = ""
    if data.get("ratings_kpis"):
        ratings_callout = (
            f'<div class="callout">{_e(data.get("ratings_callout"))}</div>'
            if data.get("ratings_callout") else ""
        )
        ratings = (
            "<h2>How our public ratings look</h2>"
            f'<div class="sec">{_kpis(list(data.get("ratings_kpis") or []))}'
            f"{ratings_callout}</div>"
        )
    problems = "".join(_problem_row(p) for p in (data.get("problems") or []))
    temporal = (
        _tcol("new", "🆕 New — didn't exist before this period",
              list(data.get("new_items") or []))
        + _tcol("long", "⏳ Still unresolved — raised for a long time",
                list(data.get("stuck_items") or []))
        + _tcol("gone", "✅ Looks fixed — people have stopped bringing it up",
                list(data.get("fixed_items") or []))
    )
    progress = (
        f'<div class="callout">{_e(data.get("progress_callout"))}</div>'
        if data.get("progress_callout") else ""
    )
    platforms = "".join(
        f'<tr><td><b>{_e(p.get("name"))}</b></td><td>{_e(p.get("audience"))}</td>'
        f"<td>{_mood_chip(str(p.get('mood')), p.get('mood_label'))}</td>"
        f'<td>{_e(p.get("what"))}</td></tr>'
        for p in (data.get("platforms") or [])
    )
    platforms_note = (
        f'<p class="scopenote">{_e(data.get("platforms_note"))}</p>'
        if data.get("platforms_note") else ""
    )

    compare = ""
    if data.get("compare_title"):
        rivals = list(data.get("rivals") or [])[:2]
        lovecards = _lovecard("us", data.get("us_name") or "Us",
                              list(data.get("us_loves") or []))
        rival_ths = ""
        for idx, rv in enumerate(rivals):
            lovecards += _lovecard(_RIVAL_CLASSES[idx], rv.get("name"),
                                   list(rv.get("loves") or []))
            rival_ths += f"<th>{_e(rv.get('name'))}</th>"
        dims = "".join(_dimension_row(d, len(rivals))
                       for d in (data.get("dimensions") or []))
        dim_table = (
            f'<table class="cmp"><tr><th>Dimension</th>'
            f'<th>{_e(data.get("us_name") or "Us")}</th>{rival_ths}'
            f"<th>Who's ahead</th></tr>{dims}</table>"
            if dims else ""
        )
        switching = (
            f'<div class="switch"><b>⚠ Who said they\'re leaving.</b> '
            f'{_e(data.get("switching"))}</div>'
            if data.get("switching") else ""
        )
        compare = (
            f"<h2>{_e(data.get('compare_title'))}</h2>"
            '<div class="sec">'
            f'<p class="scopenote">{_e(data.get("why_these"))}</p>'
            f'<div class="compgrid">{lovecards}</div>'
            f"{dim_table}{switching}</div>"
        )

    recs = "".join(_rec_card(i + 1, r)
                   for i, r in enumerate(data.get("recommendations") or []))
    np_items = "".join(_np_item(it) for it in (data.get("non_product_items") or []))
    integrity = "".join(
        f'<p><b>{_e(p.get("title"))}</b> {_e(p.get("text"))}</p>'
        for p in (data.get("integrity") or [])
    )

    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f"<title>Public Feedback Report — {_e(data.get('eyebrow'))}</title>\n"
        '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '<link href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,ital,wght@6..72,0,400;6..72,0,500;6..72,0,600;6..72,0,700;6..72,1,400&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">\n'
        f"<style>{_STYLE}</style>\n</head>\n<body>\n"
        '<div class="page">\n'
        '<div class="pagehead">'
        '<div class="brandmark">Generated by <b>Sprntly</b></div>'
        f'<div class="eyebrow">{_e(data.get("eyebrow"))}</div>'
        "<h1>What people are saying about us online</h1>"
        f'<div class="stamp">{_e(data.get("prepared"))}</div></div>\n'
        f'<div class="runline"><b>What this covers:</b> {_e(data.get("covers"))}<br>'
        f'<b>Where we looked:</b> {_e(data.get("looked"))}<br>'
        f'<b>What we were trying to answer:</b> {_e(data.get("answering"))}</div>\n'
        '<div class="countstrip">'
        f'<div class="cs"><div class="l">Feedback collected</div><div class="v">{collected} <small>posts</small></div></div>'
        f'<div class="cs"><div class="l">Product team can fix</div><div class="v">{product} <small>{_pct(product, collected)}%</small></div></div>'
        f'<div class="cs"><div class="l">Someone else owns</div><div class="v">{non} <small>{_pct(non, collected)}%</small></div></div>'
        f'<div class="cs"><div class="l">Sources checked</div><div class="v">{_i(counts.get("sources"))} <small>platforms</small></div></div>'
        f'<div class="cs"><div class="l">Said they\'re leaving</div><div class="v">{_i(counts.get("leaving"))} <small>explicitly</small></div></div>'
        "</div>\n"
        "<h2>TL;DR</h2>\n"
        '<div class="sec tldr">'
        f'<p class="src">{_e(data.get("tldr_source_line"))}</p>'
        f'<p class="intro">{_e(data.get("tldr_intro"))}</p>'
        f'<div class="enum">{tldr}</div>'
        f'<p class="close"><b>What we\'d do first:</b> {_e(data.get("first_move"))}</p>'
        "</div>\n"
        "<h2>How feedback has looked over time</h2>\n"
        '<div class="sec">'
        '<p class="scopenote">Each bar is the feedback we collected that month, '
        "coloured by how people felt. Bars are counts of posts we found — not a "
        "measure of how many people feel this way.</p>"
        f"{chart}{_kpis(list(data.get('chart_kpis') or []))}{chart_note}{chart_callout}</div>\n"
        f"{ratings}\n"
        "<h2>The problems people are running into</h2>\n"
        '<div class="sec">'
        '<p class="scopenote">Written the way people describe it themselves. The '
        "grey line underneath translates it into what we'd actually be fixing, "
        "and who owns it.</p>"
        '<table class="tt"><tr><th style="width:44%">Problem, in the user\'s words</th>'
        "<th>How often</th><th>Mood</th><th>Direction</th><th>Someone said</th></tr>"
        f"{problems}</table>"
        '<p class="scopenote">"How often" ranks these against each other within '
        "what we found. We can't turn it into a percentage of users, because we "
        "have no way of knowing how many people never posted at all.</p></div>\n"
        "<h2>What's new, what's stuck, what's fixed</h2>\n"
        '<div class="sec"><div class="split">'
        f'<div class="temporal">{temporal}</div>'
        f"{_mix_box(counts, data.get('mix') or {})}"
        f"</div>{progress}</div>\n"
        "<h2>By platform</h2>\n"
        '<div class="sec"><table class="plat">'
        "<tr><th>Platform</th><th>Audience</th><th>Sentiment</th><th>What shows up there</th></tr>"
        f"{platforms}</table>{platforms_note}</div>\n"
        f"{compare}\n"
        "<h2>Recommendations</h2>\n"
        '<div class="sec">'
        f'<p class="recintro">{_e(data.get("recs_intro"))}</p>{recs}</div>\n'
        "<h2>Also worth a look — not for the product team</h2>\n"
        '<div class="sec"><div class="nonprod">'
        f'<p class="nph">{_e(data.get("non_product_intro"))}</p>{np_items}</div></div>\n'
        '<div class="nextsteps"><h4>Next steps</h4>'
        f'<p>{_e(data.get("next_steps"))}</p>'
        "<p>To address these, use <b>Sprntly</b> to turn them into a PRD, or add "
        "them straight to your backlog. Items that aren't for the product team "
        "go to the teams that own them instead.</p></div>\n"
        '<details class="metabar">'
        "<summary>Where this came from, and how much to trust it</summary>"
        f'<div class="integrity">{integrity}</div></details>\n'
        "</div>\n"
        f"{_metadata_script(data.get('metadata') or {})}\n"
        "</body>\n</html>"
    )
