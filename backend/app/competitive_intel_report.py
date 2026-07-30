"""Competitive-intelligence report — structured data → fixed HTML template.

The `competitive-intelligence-review` skill (v3) delivers "a single
self-contained HTML document" in a fixed section order: opening prose, the
radar twice, the three benchmarks (scale, market position, feature), the
per-competitor launch log, the threat scan, sentiment with the
who-sells-against-it column, the Review-mode strategic layer, one consolidated
ranked recommendation set, sources by competitor, and a thin meta line. The
design reference is the skill's `examples/01-facebook-ads.html` — this template
is that design, pinned.

Same architecture as app.public_feedback_report and app.voc_report: the model
emits ONLY the report's data as JSON (`SCHEMA`) and this module's deterministic
template (`render_html`) populates the pinned HTML/CSS. Pixel-stable across
runs, XSS-safe (every model-supplied string is HTML-escaped), and script-less —
the frontend shows it in a sandboxed iframe with no `allow-scripts`, so the two
radars are computed HERE as inline SVG. The model never draws.

Two rules this renderer enforces rather than trusts:

  * **No unsourced number reaches the page.** Every quantitative field arrives
    as {value, source, date, tier}; `_fact` renders the value only when it
    carries BOTH a named source and a valid tier, and renders the word
    "unknown" otherwise. A bare number with no tier chip is unreachable by
    construction — which is the whole point of the skill's integrity guardrail.
  * **Mis-shaped output degrades, never raises.** Every list is filtered to the
    shape it is documented to hold and every lookup goes through `_e`/`_rows`,
    so a model that returns a string where an object was specified loses that
    row instead of 500-ing the chat (the `'str' has no attribute 'get'` lesson).

Deliberate divergences from the reference file (a standalone page): no print or
PRD/backlog buttons (onclick is dead in the script-less iframe — the real
actions live outside it), and the machine-readable rollup rides an inert
`application/json` block for the skill's query mode, as in public_feedback_report.
"""
from __future__ import annotations

import html
import json
import logging
import math

logger = logging.getLogger(__name__)

# ── Pinned design (examples/01-facebook-ads.html) ────────────────────────────

_STYLE = """
@import url('https://fonts.googleapis.com/css2?family=Spectral:wght@500;600&family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');
:root{--green:#1A6B47;--ink:#1F241F;--sub:#5B615B;--page:#FFFFFF;--desk:#F7F7F5;--rule:#E3E1D8;
--happy:#E7F1EA;--happy-t:#1A6B47;--edge:#FBF0DC;--edge-t:#8A5A12;--fail:#F9E7E4;--fail-t:#9C3223;--cool:#E9EEF4;--cool-t:#2E4A6B;}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--desk);font-family:'Inter',sans-serif;color:var(--ink);font-size:15px;line-height:1.62;padding:24px 16px 80px}
.frame{max-width:960px;margin:0 auto}
.page{background:var(--page);border:1px solid #DDDBD3;border-radius:2px;box-shadow:0 1px 2px rgba(31,36,31,.06),0 14px 36px rgba(31,36,31,.08);padding:58px 62px 68px;position:relative}
h1{font:600 35px/1.13 'Spectral',serif;letter-spacing:-.012em;margin-bottom:16px}
.opener{font:400 17.5px/1.55 'Spectral',serif;color:#3A403A;margin-bottom:22px}
.opener b{font-weight:600;color:var(--ink)}
.eyebrow{font:600 10.5px/1 'Inter',sans-serif;letter-spacing:.14em;text-transform:uppercase;color:var(--green);margin:42px 0 10px;padding-top:20px;border-top:1px solid var(--rule)}
.eyeburb{font-size:13px;color:var(--sub);margin:-4px 0 12px}
h2{font:600 21px/1.25 'Spectral',serif;margin:26px 0 3px}
h3{font:600 11px/1 'Inter',sans-serif;letter-spacing:.11em;text-transform:uppercase;color:var(--sub);margin:20px 0 7px}
p{margin-bottom:10px}
.t{font:600 8.5px/1 'IBM Plex Mono',monospace;padding:3px 4px;border-radius:3px;vertical-align:2px;margin-left:3px;letter-spacing:.03em}
.t.h{background:var(--happy);color:var(--happy-t)} .t.s{background:var(--edge);color:var(--edge-t)} .t.i{background:var(--cool);color:var(--cool-t)} .t.v{background:#EFE4F0;color:#6B3572}
table{width:100%;border-collapse:collapse;font-size:12.5px;background:#fff;border:1px solid var(--rule);border-radius:6px;overflow:hidden;margin:11px 0 6px}
th{font:600 9.5px/1.3 'Inter',sans-serif;letter-spacing:.09em;text-transform:uppercase;color:var(--sub);text-align:left;padding:9px;border-bottom:1px solid var(--rule);background:#FCFBF8;vertical-align:bottom}
td{padding:9px;border-bottom:1px solid #F0EEE7;vertical-align:top}
tr:last-child td{border-bottom:none}
td.us,th.us{background:#F4F9F6}
.cls{display:inline-block;font:600 8.5px/1 'IBM Plex Mono',monospace;padding:4px 6px;border-radius:4px;letter-spacing:.04em;white-space:nowrap}
.cls.new{background:var(--happy);color:var(--happy-t)} .cls.par{background:#EFEDE6;color:var(--sub)} .cls.dep{background:var(--fail);color:var(--fail-t)} .cls.mkt{background:var(--cool);color:var(--cool-t)} .cls.beta{background:var(--edge);color:var(--edge-t)}
.sev{display:inline-block;font:600 9px/1 'IBM Plex Mono',monospace;padding:4px 6px;border-radius:4px;white-space:nowrap}
.sev.rm{background:var(--fail);color:var(--fail-t)} .sev.rs{background:var(--edge);color:var(--edge-t)} .sev.dn{background:#EFEDE6;color:var(--sub)}
.def{font:600 9px/1 'IBM Plex Mono',monospace;padding:4px 6px;border-radius:4px;background:var(--fail);color:var(--fail-t)}
.def.some{background:var(--edge);color:var(--edge-t)} .def.yes{background:var(--happy);color:var(--happy-t)}
.blank{border:1px dashed #C9C5B6;border-radius:6px;padding:12px 15px;margin:11px 0;font-size:12.5px;color:var(--sub);background:#FBFAF7}
.blank .k{font:600 9.5px/1 'IBM Plex Mono',monospace;letter-spacing:.09em;text-transform:uppercase;color:#9A968A;display:block;margin-bottom:6px}
.callout{border:1px solid var(--rule);border-left:3px solid var(--green);background:#FCFBF8;border-radius:0 6px 6px 0;padding:14px 17px;margin:13px 0}
.callout.warn{border-left-color:var(--fail-t)}
.callout .lbl{font:600 10px/1 'IBM Plex Mono',monospace;letter-spacing:.1em;text-transform:uppercase;color:var(--green);display:block;margin-bottom:6px}
.callout.warn .lbl{color:var(--fail-t)}
.q{font-family:'Spectral',serif;font-style:italic;font-size:15px;line-height:1.55;border-left:2px solid #D8D5C9;padding:2px 0 2px 14px;margin:10px 0;color:#3A403A}
.q .attr{display:block;font:400 11px/1.5 'IBM Plex Mono',monospace;font-style:normal;color:var(--sub);margin-top:5px}
.radarwrap{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin:16px 0 4px}
.rc{border:1px solid var(--rule);border-radius:8px;background:#FCFBF8;padding:14px 12px 8px}
.rc .cap{font:600 10px/1 'IBM Plex Mono',monospace;letter-spacing:.1em;text-transform:uppercase;color:var(--green);text-align:center;margin-bottom:4px}
.rec{border:1px solid var(--rule);border-radius:8px;margin:14px 0;overflow:hidden}
.rec .hd{background:#F4F9F6;border-bottom:1px solid var(--rule);padding:12px 16px}
.rec .num{font:600 10px/1 'IBM Plex Mono',monospace;color:var(--green);letter-spacing:.1em;display:block;margin-bottom:5px}
.rec .ttl{font:600 17px/1.3 'Inter',sans-serif}
.rec .bd{padding:14px 16px}
.rec .row{display:grid;grid-template-columns:100px 1fr;gap:12px;padding:7px 0;border-bottom:1px solid #F2F0EA;font-size:13px}
.rec .row:last-child{border-bottom:none}
.rec .rk{font:600 9.5px/1.4 'IBM Plex Mono',monospace;letter-spacing:.06em;text-transform:uppercase;color:var(--sub)}
.srcgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px;margin:12px 0}
.srcbox{border:1px solid var(--rule);border-radius:6px;background:#FCFBF8;padding:11px 13px;font-size:11.5px;line-height:1.55;color:var(--sub)}
.srcbox b{display:block;font:600 9.5px/1 'IBM Plex Mono',monospace;letter-spacing:.09em;text-transform:uppercase;color:var(--green);margin-bottom:6px}
ul{margin:0 0 10px 18px} li{margin-bottom:5px}
ul.tight li{margin-bottom:3px}
.legend{font:400 11px/1.6 'IBM Plex Mono',monospace;color:var(--sub);margin-top:5px}
.src{font-size:11.5px;color:var(--sub)}
.unk{font:600 9.5px/1 'IBM Plex Mono',monospace;color:#9A968A;letter-spacing:.04em}
.status{display:inline-block;font:600 9.5px/1 'IBM Plex Mono',monospace;padding:4px 6px;border-radius:4px;letter-spacing:.04em;white-space:nowrap}
.status.ts{background:#EFEDE6;color:var(--sub)} .status.co{background:var(--edge);color:var(--edge-t)} .status.on{background:var(--fail);color:var(--fail-t)} .status.gp{background:var(--happy);color:var(--happy-t)}
.carry{font:400 12.5px/1.6 'Inter',sans-serif;color:var(--sub)}
.carry .st{font:600 9px/1 'IBM Plex Mono',monospace;padding:3px 5px;border-radius:3px;background:#EFEDE6;color:var(--sub);margin-right:6px;text-transform:uppercase;letter-spacing:.06em}
.carry .st.done{background:var(--happy);color:var(--happy-t)} .carry .st.dropped{background:var(--fail);color:var(--fail-t)} .carry .st.progress{background:var(--edge);color:var(--edge-t)}
.brandmark{position:absolute;top:16px;right:20px;font:600 10.5px/1 'Inter',sans-serif;letter-spacing:.1em;text-transform:uppercase;color:var(--sub)}
.brandmark b{color:var(--green);letter-spacing:.06em}
.metaline{font:400 11px/1.7 'IBM Plex Mono',monospace;color:#9A968A;border-top:1px solid var(--rule);margin-top:34px;padding-top:12px}
@media(max-width:820px){.page{padding:32px 20px 42px}.radarwrap{grid-template-columns:1fr}.rec .row{grid-template-columns:1fr}}
.pos{display:grid;grid-template-columns:108px repeat(3,1fr);grid-auto-rows:minmax(88px,auto);gap:4px;margin:14px 0 0;font-size:11.5px}
.pos .axl{display:flex;align-items:center;justify-content:flex-end;padding-right:9px;font:600 9.5px/1.3 'Inter',sans-serif;letter-spacing:.06em;text-transform:uppercase;color:var(--sub);text-align:right}
.c{border:1px solid var(--rule);border-radius:5px;padding:9px;background:#FCFBF8}
.c.me{background:#EDF5F0;border-color:#BCD9C8}
.c b{display:block;font:600 12px/1.3 'Inter',sans-serif;margin-bottom:3px}
.c span{color:var(--sub);font-size:11px;line-height:1.4;display:block}
.xax{display:grid;grid-template-columns:108px repeat(3,1fr);gap:4px;font:600 9.5px/1 'Inter',sans-serif;letter-spacing:.06em;text-transform:uppercase;color:var(--sub);margin-bottom:14px}
.xax div{text-align:center;padding-top:5px}
.y{color:var(--happy-t);font-weight:600}.n{color:#C4C0B2}.pt{color:var(--edge-t);font-weight:600}
@media(max-width:820px){.pos,.xax{grid-template-columns:1fr}.pos .axl{justify-content:flex-start;text-align:left;padding:6px 0 0}}
"""


# ── JSON schema for the report DATA (the model returns this, never HTML) ──────

def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required,
            "additionalProperties": False}


def _arr(items: dict) -> dict:
    return {"type": "array", "items": items}


_S = {"type": "string"}
_B = {"type": "boolean"}
_N = {"type": "number"}

# 🅗 hard · 🅢 soft · 🅘 inferred · 🅥 vendor-reported. Vendor-reported is a
# separate axis from confidence (it measures incentive), which is why it is a
# tier value here and also renderable alongside another chip.
TIERS = ("h", "s", "i", "v")
_TIER = {"type": "string", "enum": list(TIERS)}

# EVERY quantitative field in this report arrives in this shape. That is the
# schema-level half of the skill's "every quantitative claim needs a real, named
# source and date" guardrail; `_fact` below is the render-level half, refusing to
# print a value whose source or tier is missing.
_FACT = _obj({
    "value": _S,     # "$55.0B" · "+74%" · "4.2" · "" when not disclosed
    "source": _S,    # the named source. "" means unsourced → renders "unknown"
    "date": _S,      # observation date or reporting period
    "tier": _TIER,
}, ["value", "source", "date", "tier"])

_LEAD = _obj({"lead": _S, "text": _S}, ["lead", "text"])

_CELL = _obj({
    "mark": {"type": "string", "enum": ["yes", "partial", "no"]},
    "emphasis": _B,          # bold the mark (the row that belongs to one company)
}, ["mark", "emphasis"])

_RADAR = _obj({
    "caption": _S,           # "Against the scale players" / "Against the specialists"
    "dimensions": _arr(_S),  # 6–8 deciding dimensions
    "series": _arr(_obj({
        "name": _S, "is_us": _B,
        "scores": _arr(_N),  # 0–5 per dimension, same order
    }, ["name", "is_us", "scores"])),
}, ["caption", "dimensions", "series"])

SCHEMA: dict = _obj(
    {
        "title": _S,              # "Where <company> stands"
        # 1. Opening — the findings that matter, in prose. No metadata banner,
        # no audience label, no cadence note (the skill forbids all three).
        "opening": _arr(_LEAD),

        # 2. Radar, run twice (scale players / specialists).
        "radars": _arr(_RADAR),
        "radar_read": _arr(_LEAD),

        # 3. Scale benchmark.
        "scale_rows": _arr(_obj({
            "name": _S, "is_us": _B,
            "revenue": _FACT, "growth": _FACT,
            "differentiator": _S, "takes_from_us": _S,
        }, ["name", "is_us", "revenue", "growth", "differentiator",
            "takes_from_us"])),
        "scale_note": _S,         # range / non-disclosure note ("" ok)
        "scale_read": _S,

        # 4. Market position benchmark — two-axis map, us marked.
        "position_x_labels": _arr(_S),     # 3 column labels, low→high
        "position_rows": _arr(_obj({
            "label": _S,                   # row (y) label, high→low
            "cells": _arr(_obj({"name": _S, "note": _S, "is_us": _B},
                               ["name", "note", "is_us"])),
        }, ["label", "cells"])),
        "position_read": _S,

        # 5. Feature benchmark — capability by capability, table-stakes status.
        "feature_competitors": _arr(_S),   # column order, excluding us
        "feature_rows": _arr(_obj({
            "capability": _S, "emphasis": _B,
            "us": _CELL,
            "cells": _arr(_CELL),          # one per feature_competitors entry
            "status": _S,                  # "Table stakes" / "Reddit only" / …
            "status_class": {"type": "string",
                             "enum": ["table-stakes", "contested", "only", "gap"]},
        }, ["capability", "emphasis", "us", "cells", "status", "status_class"])),
        "feature_read": _S,                # the closing count

        # 6. Launch log — per competitor, dated, classified, with the pattern.
        "launch_log": _arr(_obj({
            "competitor": _S,
            "entries": _arr(_obj({
                "date": _S, "what": _S,
                "classification": {"type": "string", "enum": [
                    "net-new", "parity", "deprecation", "beta", "market"]},
                "tier": _TIER, "vendor_reported": _B,
            }, ["date", "what", "classification", "tier", "vendor_reported"])),
            "pattern": _S,                 # what the pattern says
            "nothing_shipped": _B,         # silence IS a finding
            "window_checked": _S,          # the window we actually checked
        }, ["competitor", "entries", "pattern", "nothing_shipped",
            "window_checked"])),

        # 7. Threat scan — severity × timing × defence. "None" where true.
        "threats": _arr(_obj({
            "threat": _S,
            "severity": {"type": "string",
                         "enum": ["dents", "reshapes", "removes"]},
            "timing": {"type": "string", "enum": ["now", "this-year", "watch"]},
            "defence": {"type": "string",
                        "enum": ["named", "in-flight", "none"]},
            "defence_label": _S,
            "detail": _S,
            "figures": _arr(_obj({"label": _S, "fact": _FACT},
                                 ["label", "fact"])),
        }, ["threat", "severity", "timing", "defence", "defence_label",
            "detail", "figures"])),
        "threat_callout": _obj({"label": _S, "paragraphs": _arr(_S)},
                               ["label", "paragraphs"]),

        # 8. Sentiment — theirs and ours on the same axes, plus the column that
        # makes the section worth reading (who sells against each of our themes).
        "sentiment_rows": _arr(_obj({
            "name": _S, "is_us": _B,
            "rating": _FACT, "review_volume": _FACT,
            "direction": _S,               # vs prior run ("" when baseline)
            "themes": _S,
        }, ["name", "is_us", "rating", "review_volume", "direction", "themes"])),
        "competitor_praise": _arr(_obj({"name": _S, "theme": _S, "tier": _TIER},
                                       ["name", "theme", "tier"])),
        "our_quotes": _arr(_obj({"quote": _S, "attribution": _S, "tier": _TIER},
                                ["quote", "attribution", "tier"])),
        "our_themes": _arr(_obj({
            "theme": _S, "description": _S, "who_sells_against_it": _S,
            "tier": _TIER,
        }, ["theme", "description", "who_sells_against_it", "tier"])),
        "sentiment_read": _S,
        "not_sourced": _S,                 # the open-pulls box ("" = omitted)

        # 9. Review mode only — arena · 9-box · product & pricing · momentum ·
        # money · organisational signals. [] in Scan mode.
        "review_sections": _arr(_obj({"title": _S, "paragraphs": _arr(_S)},
                                     ["title", "paragraphs"])),

        # 10. Recommendations — ONE consolidated ranked set, 3–5.
        "recommendations": _arr(_obj({
            "eyebrow": _S,                 # "01 · Product"
            "title": _S,
            "from": _S,                    # the stages + findings behind it
            "do": _S,
            "why_now": _S,
            "measure": _S,
            "watch": _S,
        }, ["eyebrow", "title", "from", "do", "why_now", "measure", "watch"])),
        "carried_decisions": _arr(_obj({
            "recommendation": _S,
            "status": {"type": "string",
                       "enum": ["open", "in progress", "done", "dropped"]},
            "outcome_note": _S,
        }, ["recommendation", "status", "outcome_note"])),

        # 11. Sources — grouped by competitor, each with what it supports.
        "sources": _arr(_obj({"competitor": _S, "detail": _S},
                             ["competitor", "detail"])),

        # 12. Thin meta line — window, derived set, the note on our own figures.
        "meta_line": _S,

        # Machine-readable rollup + the next state file (never rendered as prose).
        "metadata": {"type": "object", "additionalProperties": True},
        "next_state": {"type": "object", "additionalProperties": True},
    },
    ["title", "opening", "radars", "radar_read", "scale_rows", "scale_note",
     "scale_read", "position_x_labels", "position_rows", "position_read",
     "feature_competitors", "feature_rows", "feature_read", "launch_log",
     "threats", "threat_callout", "sentiment_rows", "competitor_praise",
     "our_quotes", "our_themes", "sentiment_read", "not_sourced",
     "review_sections", "recommendations", "carried_decisions", "sources",
     "meta_line", "metadata", "next_state"],
)


# ── Defensive accessors ──────────────────────────────────────────────────────

def _e(s) -> str:
    """HTML-escape any model-supplied value (None → '')."""
    return html.escape(str(s)) if s is not None else ""


def _rows(v) -> list[dict]:
    """A list of dicts, whatever the model actually returned. A string where an
    object was specified loses that row rather than raising."""
    if not isinstance(v, list):
        return []
    return [r for r in v if isinstance(r, dict)]


def _strs(v) -> list[str]:
    """A list of strings; non-scalars dropped."""
    if not isinstance(v, list):
        return []
    return [str(x) for x in v if isinstance(x, (str, int, float))]


def _dct(v) -> dict:
    return v if isinstance(v, dict) else {}


def _num(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _tier_chip(tier, extra_vendor: bool = False) -> str:
    """The inline confidence chip. Unknown/missing tier renders nothing — which
    is why `_fact` refuses to print a value without one."""
    t = str(tier or "").strip().lower()
    out = f'<span class="t {t}">{t.upper()}</span>' if t in TIERS else ""
    if extra_vendor and t != "v":
        out += '<span class="t v">V</span>'
    return out


_UNKNOWN = '<span class="unk">unknown</span>'


def _fact(f, *, unknown: str = _UNKNOWN) -> str:
    """Render a {value, source, date, tier} quantitative field.

    The value prints ONLY when it carries a named source AND a valid tier. A
    blank value renders `unknown` (the skill's "stated as unknown in prose"),
    and so does a value that arrived unsourced or untiered — the renderer will
    not put a number on the page that a reader cannot trace, and will not put
    one there without its tier chip.
    """
    d = _dct(f)
    value = str(d.get("value") or "").strip()
    source = str(d.get("source") or "").strip()
    tier = str(d.get("tier") or "").strip().lower()
    if not value or not source or tier not in TIERS:
        return unknown
    date = str(d.get("date") or "").strip()
    date_html = f' <span class="src">{_e(date)}</span>' if date else ""
    return f"<strong>{_e(value)}</strong>{date_html}{_tier_chip(tier)}"


# ── Radar (computed here; the model only supplies dimensions + scores) ────────

_RADAR_CX, _RADAR_CY, _RADAR_RMAX = 250.0, 235.0, 150.0
_RADAR_RINGS = 5
_SERIES_COLORS = ("#1A6B47", "#9C3223", "#2E4A6B", "#8A5A12", "#6B3572")
_RADAR_MAX_DIMS = 8
_RADAR_MAX_SERIES = 5


def _radar_point(score: float, index: int, n: int) -> tuple[float, float]:
    """Cartesian point for `score` (0–5) on axis `index` of `n`. Axis 0 is at
    the top and axes advance clockwise, matching the reference example."""
    r = _RADAR_RMAX * max(0.0, min(5.0, score)) / 5.0
    theta = math.radians(-90.0 + index * (360.0 / n))
    return (_RADAR_CX + r * math.cos(theta), _RADAR_CY + r * math.sin(theta))


def _radar_ring(level: int, n: int) -> str:
    pts = " ".join(
        f"{x:.1f},{y:.1f}" for x, y in
        (_radar_point(level, i, n) for i in range(n))
    )
    return f'<polygon points="{pts}" fill="none" stroke="#EDEBE3" stroke-width="1"/>'


def _radar(spec: dict) -> str:
    """One radar chart as inline SVG. Returns "" when the spec can't be plotted
    (fewer than three dimensions, or no series) — a missing chart is better than
    a misleading one."""
    dims = _strs(spec.get("dimensions"))[:_RADAR_MAX_DIMS]
    series = _rows(spec.get("series"))[:_RADAR_MAX_SERIES]
    n = len(dims)
    if n < 3 or not series:
        return ""
    parts: list[str] = [_radar_ring(level, n) for level in range(1, _RADAR_RINGS + 1)]
    # spokes
    for i in range(n):
        x, y = _radar_point(_RADAR_RINGS, i, n)
        parts.append(
            f'<line x1="{_RADAR_CX:.0f}" y1="{_RADAR_CY:.0f}" x2="{x:.1f}" '
            f'y2="{y:.1f}" stroke="#E3E1D8" stroke-width="1"/>'
        )
    # axis labels, just outside the outer ring
    for i, label in enumerate(dims):
        theta = math.radians(-90.0 + i * (360.0 / n))
        lx = _RADAR_CX + (_RADAR_RMAX + 16.5) * math.cos(theta)
        ly = _RADAR_CY + (_RADAR_RMAX + 16.5) * math.sin(theta) + 3.0
        dx = math.cos(theta)
        anchor = "middle" if abs(dx) < 0.15 else ("start" if dx > 0 else "end")
        parts.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="{anchor}" '
            'font-family="Inter,sans-serif" font-size="9.5" font-weight="600" '
            f'fill="#5B615B">{_e(label)}</text>'
        )
    # one polygon per series; "us" always takes the first (green) colour
    ordered = sorted(series, key=lambda s: not bool(s.get("is_us")))
    legend: list[tuple[str, str]] = []
    for s in ordered:
        # A series with FEWER scores than dimensions is dropped, not padded.
        # Padding with 0.0 drew the competitor pinned to the centre on every
        # trailing axis — which reads as "we scored them zero on trust and
        # measurement", a fabricated finding rendered as a picture. A missing
        # shape is honest; a wrong one is not.
        raw = s.get("scores")
        scores = [_num(v) for v in raw] if isinstance(raw, list) else []
        if len(scores) < n:
            logger.warning(
                "competitive-intel radar: dropping series %r — %d score(s) for "
                "%d dimensions", str(s.get("name") or "?"), len(scores), n,
            )
            continue
        colour = _SERIES_COLORS[len(legend) % len(_SERIES_COLORS)]
        pts = " ".join(
            f"{x:.1f},{y:.1f}" for x, y in
            (_radar_point(scores[i], i, n) for i in range(n))
        )
        parts.append(
            f'<polygon points="{pts}" fill="{colour}" fill-opacity="0.10" '
            f'stroke="{colour}" stroke-width="2" stroke-linejoin="round"/>'
        )
        legend.append((colour, str(s.get("name") or "")))
    if not legend:
        # Every series was unusable — an empty grid says nothing, so omit the
        # chart entirely rather than shipping axes with no shapes on them.
        return ""
    # legend row along the bottom of the 500×470 viewBox
    slot = 420.0 / max(1, len(legend))
    for idx, (colour, name) in enumerate(legend):
        lx = 40.0 + slot * idx
        parts.append(
            f'<rect x="{lx:.0f}" y="418" width="11" height="11" rx="2" '
            f'fill="{colour}" fill-opacity="0.25" stroke="{colour}" '
            'stroke-width="1.6"/>'
            f'<text x="{lx + 17:.0f}" y="428" font-family="Inter,sans-serif" '
            f'font-size="11.5" font-weight="600" fill="#1F241F">{_e(name)}</text>'
        )
    return (
        '<div class="rc">'
        f'<div class="cap">{_e(spec.get("caption"))}</div>'
        '<svg viewBox="0 0 500 470" xmlns="http://www.w3.org/2000/svg" '
        'style="width:100%;max-width:500px;display:block;margin:0 auto" '
        'role="img" aria-label="Competitive radar on the deciding dimensions">'
        + "".join(parts)
        + "</svg></div>"
    )


# ── Section builders ─────────────────────────────────────────────────────────

def _openers(items: list[dict]) -> str:
    out = []
    for it in items:
        lead = _e(it.get("lead"))
        text = _e(it.get("text"))
        body = f"<b>{lead}</b> {text}" if lead else text
        out.append(f'<div class="opener">{body}</div>')
    return "".join(out)


def _leads_as_paras(items: list[dict]) -> str:
    out = []
    for it in items:
        lead = _e(it.get("lead"))
        text = _e(it.get("text"))
        out.append(f"<p><strong>{lead}</strong> {text}</p>" if lead else f"<p>{text}</p>")
    return "".join(out)


def _paras(items: list[str]) -> str:
    return "".join(f"<p>{_e(p)}</p>" for p in items)


def _eyebrow(label: str, burb: str = "") -> str:
    sub = f'<div class="eyeburb">{burb}</div>' if burb else ""
    return f'<div class="eyebrow">{_e(label)}</div>{sub}'


def _scale_section(data: dict) -> str:
    rows = _rows(data.get("scale_rows"))
    if not rows:
        return ""
    body = []
    for r in rows:
        us = " us" if r.get("is_us") else ""
        cls = f' class="{us.strip()}"' if us else ""
        body.append(
            f"<tr><td{cls}><strong>{_e(r.get('name'))}</strong></td>"
            f"<td{cls}>{_fact(r.get('revenue'))}</td>"
            f"<td{cls}>{_fact(r.get('growth'))}</td>"
            f"<td{cls}>{_e(r.get('differentiator'))}</td>"
            f"<td{cls}>{_e(r.get('takes_from_us')) or '&mdash;'}</td></tr>"
        )
    note = (f'<p class="legend">{_e(data.get("scale_note"))}</p>'
            if data.get("scale_note") else "")
    read = f"<p>{_e(data.get('scale_read'))}</p>" if data.get("scale_read") else ""
    return (
        _eyebrow("Scale benchmark",
                 "Where each competitor actually sits. Most recent reported "
                 "period for each; a figure with no traceable source is shown "
                 "as unknown rather than estimated.")
        + '<table><thead><tr><th style="width:12%">Company</th>'
        '<th style="width:16%">Revenue</th><th style="width:11%">Growth</th>'
        '<th style="width:22%">Their differentiator</th>'
        "<th>What they take from us</th></tr></thead><tbody>"
        + "".join(body) + f"</tbody></table>{note}{read}"
    )


def _position_section(data: dict) -> str:
    rows = _rows(data.get("position_rows"))
    if not rows:
        return ""
    x_labels = _strs(data.get("position_x_labels"))[:3]
    while len(x_labels) < 3:
        x_labels.append("")
    cells_html: list[str] = []
    for r in rows:
        cells_html.append(f'<div class="axl">{_e(r.get("label"))}</div>')
        cells = _rows(r.get("cells"))[:3]
        while len(cells) < 3:
            cells.append({"name": "—", "note": "", "is_us": False})
        for c in cells:
            me = " me" if c.get("is_us") else ""
            note = f'<span>{_e(c.get("note"))}</span>' if c.get("note") else ""
            cells_html.append(
                f'<div class="c{me}"><b>{_e(c.get("name")) or "&mdash;"}</b>{note}</div>'
            )
    xax = "".join(f"<div>{_e(lbl)}</div>" for lbl in x_labels)
    read = f"<p>{_e(data.get('position_read'))}</p>" if data.get("position_read") else ""
    return (
        _eyebrow("Market position",
                 "Every competitor placed, us included and marked. Placement is "
                 'our read <span class="t i">I</span>; the facts behind it are '
                 "sourced throughout.")
        + f'<div class="pos">{"".join(cells_html)}</div>'
        f'<div class="xax"><div></div>{xax}</div>{read}'
    )


_MARKS = {"yes": ('y', "&#10003;"), "partial": ("pt", "partial"), "no": ("n", "&mdash;")}
_STATUS_CLASSES = {"table-stakes": "ts", "contested": "co", "only": "on", "gap": "gp"}


def _feature_cell(cell, *, us: bool = False) -> str:
    d = _dct(cell)
    cls, glyph = _MARKS.get(str(d.get("mark")), _MARKS["no"])
    inner = f"<strong>{glyph}</strong>" if d.get("emphasis") else glyph
    prefix = "us " if us else ""
    return f'<td class="{prefix}{cls}">{inner}</td>'


def _feature_section(data: dict) -> str:
    rows = _rows(data.get("feature_rows"))
    if not rows:
        return ""
    names = _strs(data.get("feature_competitors"))
    head = "".join(f"<th>{_e(n)}</th>" for n in names)
    body = []
    for r in rows:
        cells = _rows(r.get("cells"))[:len(names)]
        while len(cells) < len(names):
            cells.append({"mark": "no", "emphasis": False})
        cap = _e(r.get("capability"))
        cap_html = f"<strong>{cap}</strong>" if r.get("emphasis") else cap
        status_cls = _STATUS_CLASSES.get(str(r.get("status_class")), "ts")
        body.append(
            f"<tr><td>{cap_html}</td>"
            + _feature_cell(r.get("us"), us=True)
            + "".join(_feature_cell(c) for c in cells)
            + f'<td><span class="status {status_cls}">{_e(r.get("status"))}</span>'
            "</td></tr>"
        )
    read = f"<p>{_e(data.get('feature_read'))}</p>" if data.get("feature_read") else ""
    return (
        _eyebrow("Feature benchmark",
                 "Capability by capability. The radar shows the shape; this "
                 "shows which specific capabilities are commodity and which "
                 "belong to one company.")
        + '<table><thead><tr><th style="width:26%">Capability</th>'
        f'<th class="us">Us</th>{head}<th style="width:16%">Status</th>'
        "</tr></thead><tbody>" + "".join(body) + f"</tbody></table>{read}"
    )


_CLASS_CHIPS = {
    "net-new": ("new", "net-new"), "parity": ("par", "parity"),
    "deprecation": ("dep", "retired"), "beta": ("beta", "beta"),
    "market": ("mkt", "market"),
}


def _launch_section(data: dict) -> str:
    blocks = _rows(data.get("launch_log"))
    if not blocks:
        return ""
    out = []
    for b in blocks:
        name = _e(b.get("competitor"))
        entries = _rows(b.get("entries"))
        if b.get("nothing_shipped") or not entries:
            # Silence from a fast-moving rival is a finding — report it WITH the
            # window we checked, never as an empty section.
            window = _e(b.get("window_checked"))
            out.append(
                f"<h2>{name}</h2>"
                '<div class="callout"><span class="lbl">Nothing shipped</span>'
                f"<p style=\"margin-bottom:0\">No launches found in the window "
                f"checked{f' ({window})' if window else ''}. "
                f"{_e(b.get('pattern'))}</p></div>"
            )
            continue
        trs = []
        for e in entries:
            cls, label = _CLASS_CHIPS.get(str(e.get("classification")),
                                          _CLASS_CHIPS["parity"])
            chips = _tier_chip(e.get("tier"), extra_vendor=bool(e.get("vendor_reported")))
            trs.append(
                f"<tr><td>{_e(e.get('date'))}</td>"
                f"<td>{_e(e.get('what'))} {chips}</td>"
                f'<td><span class="cls {cls}">{label}</span></td></tr>'
            )
        pattern = (f'<p class="legend"><strong>Pattern:</strong> '
                   f'{_e(b.get("pattern"))}</p>' if b.get("pattern") else "")
        out.append(
            f"<h2>{name}</h2>"
            '<table><thead><tr><th style="width:12%">Date</th><th>Shipped</th>'
            '<th style="width:14%">Class</th></tr></thead><tbody>'
            + "".join(trs) + f"</tbody></table>{pattern}"
        )
    return (
        _eyebrow(
            "What they launched",
            "Everything shipped in the window, dated and classified. "
            '<span class="cls new">net-new</span> opened a capability nobody '
            'had · <span class="cls par">parity</span> closed a gap · '
            '<span class="cls mkt">market</span> same product, new ground · '
            '<span class="cls dep">retired</span> pulled back.',
        )
        + "".join(out)
    )


_SEVERITY = {"dents": ("dn", "Dents us"), "reshapes": ("rs", "Reshapes us"),
             "removes": ("rm", "Removes us")}
_TIMING = {"now": "Now", "this-year": "This year", "watch": "Watch"}
_DEFENCE = {"named": "yes", "in-flight": "some", "none": ""}


def _threat_section(data: dict) -> str:
    threats = _rows(data.get("threats"))
    if not threats:
        return ""
    trs = []
    for t in threats:
        sev_cls, sev_label = _SEVERITY.get(str(t.get("severity")),
                                           _SEVERITY["dents"])
        defence = str(t.get("defence") or "none")
        def_cls = _DEFENCE.get(defence, "")
        # "None" is written when it is true — the most useful word in this stage.
        def_label = _e(t.get("defence_label")) or (
            "None" if defence == "none" else defence.replace("-", " ").title()
        )
        figures = "".join(
            f' <span class="src">{_e(_dct(f).get("label"))}:</span> '
            f'{_fact(_dct(f).get("fact"))}'
            for f in _rows(t.get("figures"))
        )
        trs.append(
            f"<tr><td><strong>{_e(t.get('threat'))}</strong></td>"
            f'<td><span class="sev {sev_cls}">{sev_label}</span></td>'
            f"<td>{_TIMING.get(str(t.get('timing')), 'Watch')}</td>"
            f'<td><span class="def {def_cls}">{def_label}</span></td>'
            f"<td>{_e(t.get('detail'))}{figures}</td></tr>"
        )
    callout = _dct(data.get("threat_callout"))
    callout_html = ""
    if callout.get("label") or _strs(callout.get("paragraphs")):
        callout_html = (
            '<div class="callout warn">'
            f'<span class="lbl">{_e(callout.get("label")) or "The one that matters"}</span>'
            + _paras(_strs(callout.get("paragraphs")))
            + "</div>"
        )
    return (
        _eyebrow(
            "New markets, new technology, and what could actually take the business",
            "Rated on severity, timing, and whether we have a defence. Where "
            "the defence is none, it says none.",
        )
        + '<table><thead><tr><th style="width:22%">Threat</th>'
        '<th style="width:10%">Severity</th><th style="width:9%">Timing</th>'
        '<th style="width:11%">Our defence</th>'
        "<th>What's actually happening</th></tr></thead><tbody>"
        + "".join(trs) + f"</tbody></table>{callout_html}"
    )


def _sentiment_section(data: dict) -> str:
    rows = _rows(data.get("sentiment_rows"))
    praise = _rows(data.get("competitor_praise"))
    quotes = _rows(data.get("our_quotes"))
    themes = _rows(data.get("our_themes"))
    if not (rows or praise or quotes or themes):
        return ""
    parts = [_eyebrow(
        "What customers say",
        "The same axes for every competitor, us included. Ratings and review "
        "volumes print only where a source was found.",
    )]
    if rows:
        trs = []
        for r in rows:
            cls = ' class="us"' if r.get("is_us") else ""
            trs.append(
                f"<tr><td{cls}><strong>{_e(r.get('name'))}</strong></td>"
                f"<td{cls}>{_fact(r.get('rating'))}</td>"
                f"<td{cls}>{_fact(r.get('review_volume'))}</td>"
                f"<td{cls}>{_e(r.get('direction')) or '&mdash;'}</td>"
                f"<td{cls}>{_e(r.get('themes'))}</td></tr>"
            )
        parts.append(
            '<table><thead><tr><th style="width:14%">Company</th>'
            '<th style="width:12%">Rating</th><th style="width:13%">Reviews</th>'
            '<th style="width:13%">vs prior run</th><th>Themes</th>'
            "</tr></thead><tbody>" + "".join(trs) + "</tbody></table>"
        )
    if praise:
        parts.append("<h3>What each competitor is praised for</h3><table><tbody>")
        parts.append("".join(
            f"<tr><td style=\"width:14%\"><strong>{_e(p.get('name'))}</strong></td>"
            f"<td>{_e(p.get('theme'))} {_tier_chip(p.get('tier'))}</td></tr>"
            for p in praise
        ))
        parts.append("</tbody></table>")
    if quotes:
        parts.append("<h3>What is said about us</h3>")
        parts.append("".join(
            f'<div class="q">{_e(q.get("quote"))}'
            f'<span class="attr">{_e(q.get("attribution"))} '
            f'{_tier_chip(q.get("tier"))}</span></div>'
            for q in quotes
        ))
    if themes:
        parts.append(
            '<table><thead><tr><th style="width:20%">Our complaint theme</th>'
            "<th>What customers describe</th>"
            '<th style="width:19%">Who sells against it</th>'
            "</tr></thead><tbody>"
        )
        for t in themes:
            against = _e(t.get("who_sells_against_it"))
            # A theme that maps to nobody is avoidable loss, not competitive
            # pressure — it must read differently, so it renders in italics.
            against_html = (f"<strong>{against}</strong>" if against
                            else "<em>Nobody &mdash; pure loss</em>")
            parts.append(
                f"<tr><td><strong>{_e(t.get('theme'))}</strong></td>"
                f"<td>{_e(t.get('description'))} {_tier_chip(t.get('tier'))}</td>"
                f"<td>{against_html}</td></tr>"
            )
        parts.append("</tbody></table>")
    if data.get("sentiment_read"):
        parts.append(f"<p>{_e(data.get('sentiment_read'))}</p>")
    if data.get("not_sourced"):
        parts.append(
            '<div class="blank"><span class="k">Not yet sourced</span>'
            f'{_e(data.get("not_sourced"))}</div>'
        )
    return "".join(parts)


def _review_sections(data: dict) -> str:
    sections = _rows(data.get("review_sections"))
    if not sections:
        return ""
    out = [_eyebrow("The strategic picture",
                    "Arena, position and share, product and pricing, momentum, "
                    "money, and organisational signals.")]
    for s in sections:
        out.append(f"<h2>{_e(s.get('title'))}</h2>"
                   + _paras(_strs(s.get("paragraphs"))))
    return "".join(out)


_REC_ROWS = (("From", "from"), ("Do", "do"), ("Why now", "why_now"),
             ("Measure", "measure"), ("Watch", "watch"))


def _recommendations(data: dict) -> str:
    recs = _rows(data.get("recommendations"))
    carried = _rows(data.get("carried_decisions"))
    if not (recs or carried):
        return ""
    out = [_eyebrow("What to do",
                    "One ranked set, drawing on every section above. Ranked by "
                    "leverage, not effort.")]
    for i, r in enumerate(recs, start=1):
        rows = "".join(
            f'<div class="row"><span class="rk">{label}</span>'
            f'<span>{_e(r.get(key))}</span></div>'
            for label, key in _REC_ROWS if r.get(key)
        )
        eyebrow = _e(r.get("eyebrow")) or f"{i:02d}"
        out.append(
            f'<div class="rec"><div class="hd"><span class="num">{eyebrow}</span>'
            f'<div class="ttl">{_e(r.get("title"))}</div></div>'
            f'<div class="bd">{rows}</div></div>'
        )
    if carried:
        out.append("<h3>Carried forward from the last run</h3>")
        for c in carried:
            status = str(c.get("status") or "open")
            cls = {"done": "done", "dropped": "dropped",
                   "in progress": "progress"}.get(status, "")
            out.append(
                f'<div class="carry"><span class="st {cls}">{_e(status)}</span>'
                f"{_e(c.get('recommendation'))}"
                + (f" &mdash; {_e(c.get('outcome_note'))}"
                   if c.get("outcome_note") else "")
                + "</div>"
            )
    return "".join(out)


def _sources(data: dict) -> str:
    rows = _rows(data.get("sources"))
    if not rows:
        return ""
    boxes = "".join(
        f'<div class="srcbox"><b>{_e(s.get("competitor"))}</b>'
        f'{_e(s.get("detail"))}</div>'
        for s in rows
    )
    return _eyebrow("Sources") + f'<div class="srcgrid">{boxes}</div>'


def _metadata_script(metadata: dict) -> str:
    """The machine-readable rollup as an inert JSON block. `<` is escaped to
    \\u003c so model-supplied strings can never close the script element."""
    payload = json.dumps(metadata or {}, ensure_ascii=False).replace("<", "\\u003c")
    return f'<script type="application/json" id="report-metadata">{payload}</script>'


# ── Entry point ──────────────────────────────────────────────────────────────

def render_html(data: dict) -> str:
    """Populate the pinned competitive-intelligence template from the model's
    report data. Returns a self-contained HTML document.

    Section order is the skill's Output order and is not data-driven: sections
    are additive, never substitutive, so the radar never replaces a benchmark.
    An empty section is omitted rather than rendered as a blank frame.
    """
    data = _dct(data)
    radars = [_radar(r) for r in _rows(data.get("radars"))[:2]]
    radars = [r for r in radars if r]
    radar_block = ""
    if radars:
        radar_block = (
            _eyebrow(
                "Where we win and where we lose",
                "The dimensions that decide this category, scored 0&ndash;5. "
                'Scoring is judgment <span class="t i">I</span>; the facts '
                "underneath are sourced throughout. Run twice, because "
                "averaging a specialist into one chart hides the shape worth "
                "seeing.",
            )
            + f'<div class="radarwrap">{"".join(radars)}</div>'
            + _leads_as_paras(_rows(data.get("radar_read")))
        )

    meta_line = (
        f'<div class="metaline">{_e(data.get("meta_line"))}</div>'
        if data.get("meta_line") else ""
    )
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f"<title>Competitive Intelligence — {_e(data.get('title'))}</title>\n"
        f"<style>{_STYLE}</style>\n</head>\n<body>\n"
        '<div class="frame"><div class="page">\n'
        '<div class="brandmark">Generated by <b>Sprntly</b></div>\n'
        f"<h1>{_e(data.get('title'))}</h1>\n"
        + _openers(_rows(data.get("opening")))
        + radar_block
        + _scale_section(data)
        + _position_section(data)
        + _feature_section(data)
        + _launch_section(data)
        + _threat_section(data)
        + _sentiment_section(data)
        + _review_sections(data)
        + _recommendations(data)
        + _sources(data)
        + meta_line
        + "\n</div></div>\n"
        + _metadata_script(_dct(data.get("metadata")))
        + "\n</body>\n</html>"
    )
