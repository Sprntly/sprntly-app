"""Voice-of-Customer report — structured data → fixed HTML template (skill v3).

The `voice-of-customer-report` skill (v3) renders a report-style document:
title + explicit date range, an "Asked:" line, a run line, a prose TL;DR with
findings written as problems, a problems-at-a-glance table (accounts ·
frustration 1–5 · tone read · metric impacted), a volume-vs-frustration radar,
theme cards with real quotes, and goal-fit recommendations with a required
"deliberately not recommended" block. The reference standard is the skill's
`examples/` reports — this template is their design, pinned.

Asking the model to hand-author that HTML is slow, expensive, and the CSS
drifts run to run. Instead the model emits ONLY the report's data as JSON
(`SCHEMA`) and this module's deterministic template (`render_html`) populates
the pinned HTML/CSS — pixel-identical every run, and XSS-safe: every
model-supplied string is HTML-escaped, and the report only ever renders inside
the frontend's sandboxed, script-less iframe (see EvidenceHtmlBrief). The radar
chart is computed here as inline SVG from the glance rows' numbers — the model
never draws.

`build()` runs the one attributed `llm_call` (with the VoC SKILL.md method
bound, so capture-stage discipline, the counting rule, and the goal-fit gate
still govern the extraction) and returns the rendered HTML string, which
callers drop into the Ask payload's `answer` field.
"""
from __future__ import annotations

import html
import logging
import math

from app.graph.gateway import llm_call

logger = logging.getLogger(__name__)

_VOC_SKILL = "voice-of-customer-report"

# The <style> block from the skill's examples/ reports — the pinned v3 design.
# Deliberate divergences from the reference files: no print button (the report
# renders in a script-less sandboxed iframe where onclick is dead), and the
# .page keeps the frame's own padding modest.
_STYLE = """
:root{--ink:#1a1813;--ink2:#403c34;--soft:#8a857a;--line:#e6e3da;--rule:#d4cfc2;--page:#fff;--bg:#fff;
--accent:#1f6f52;--red:#bb463c;--red-s:#fbecea;--amber:#a9762a;--amber-s:#f7efdd;--teal:#1f7a5a;--teal-s:#e9f3ed;
--serif:'Newsreader',Georgia,serif;--sans:'Inter',system-ui,sans-serif;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font-family:var(--serif);line-height:1.6;-webkit-font-smoothing:antialiased;padding:24px 12px 64px}
.page{max-width:820px;margin:0 auto;background:var(--page);padding:34px 56px 56px}
.eyebrow{font-family:var(--sans);font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--accent);font-weight:700}
h1{font-weight:600;font-size:33px;line-height:1.15;margin:10px 0 6px;letter-spacing:-.01em}
.period{font-family:var(--sans);font-size:13.5px;font-weight:600;color:var(--ink2);margin-bottom:12px}
.period span{color:var(--soft);font-weight:500}
.deck{font-size:18px;font-style:italic;color:var(--ink2);margin-bottom:14px}
.askedline{font-family:var(--sans);font-size:12.5px;color:var(--ink2);background:#f6f5f0;border-left:3px solid var(--accent);padding:9px 13px;margin-bottom:12px;border-radius:0 6px 6px 0}
.askedline b{color:var(--ink)}
.runline{font-family:var(--sans);font-size:12.5px;color:var(--ink2);line-height:1.7;border-top:1px solid var(--rule);border-bottom:1px solid var(--rule);padding:13px 0}
.runline b{color:var(--ink)}
h2{font-weight:600;font-size:13px;font-family:var(--sans);letter-spacing:.12em;text-transform:uppercase;color:var(--accent);margin:38px 0 0;padding-bottom:7px;border-bottom:2px solid var(--accent);display:inline-block}
.sec{margin-top:6px}
.tldr .src{font-style:italic;color:var(--ink2);font-size:15.5px;margin-top:16px}
.tldr .intro{font-size:17px;margin-top:13px;color:var(--ink)}
.tldr .intro b{font-weight:600}
.enum{margin:15px 0 4px;padding:0}
.enum .item{font-size:16.5px;line-height:1.55;margin:13px 0;padding-left:6px}
.enum .hash{font-family:var(--sans);font-weight:700;color:var(--accent);font-size:15px;margin-right:6px}
.enum .item b{font-weight:600}
.enum .vq{display:block;font-size:14.5px;font-style:italic;color:var(--ink2);margin-top:4px;padding-left:30px;border-left:2px solid var(--line);margin-left:4px}
.tldr .close{font-size:17px;margin-top:18px;padding-top:15px;border-top:1px dashed var(--line);color:var(--ink)}
.tldr .close b{font-weight:600}
.glance{margin-top:18px;width:100%;border-collapse:collapse;font-family:var(--sans);font-size:12.5px;border:1px solid var(--rule);border-radius:8px;overflow:hidden}
.glance th{background:#f6f5f0;text-align:left;padding:9px 11px;font-size:10px;letter-spacing:.05em;text-transform:uppercase;color:var(--ink2);font-weight:700;border-bottom:1px solid var(--rule)}
.glance td{padding:9px 11px;border-top:1px solid var(--line);color:var(--ink2);vertical-align:top}
.glance td b{color:var(--ink)}
.glance .sent{font-style:italic;color:var(--ink2)}
.glance .metric{color:var(--accent);font-weight:600}
.glance .metric.none{color:var(--soft);font-weight:400;font-style:italic}
.f5{color:var(--red);font-weight:700}.f4{color:#c25f37;font-weight:700}.f3{color:var(--amber);font-weight:700}.f2{color:var(--ink2);font-weight:600}.f1{color:var(--soft)}
.glance tr.minor td{color:var(--soft)}
.tnote{font-family:var(--sans);font-size:11.5px;color:var(--soft);line-height:1.6;margin-top:9px}
.tnote b{color:var(--ink2)}
.radarwrap{margin-top:18px;border:1px solid var(--rule);border-radius:10px;background:#fcfbf8;padding:16px 10px 8px}
.rlegend{font-family:var(--sans);display:flex;gap:24px;justify-content:center;font-size:11.5px;color:var(--ink2);font-weight:600;padding-bottom:4px;flex-wrap:wrap}
.rlegend i{display:inline-block;width:20px;height:3px;border-radius:2px;vertical-align:middle;margin-right:7px}
.readnote{font-family:var(--sans);font-size:12.5px;line-height:1.65;color:var(--ink2);background:#eef3f0;border:1px solid #cfe0d7;border-radius:8px;padding:11px 14px;margin-top:12px}
.readnote b{color:var(--accent)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:20px}
.card{border:1px solid var(--line);border-radius:10px;padding:19px 20px 18px;border-top:3px solid var(--c);background:#fcfbf8;display:flex;flex-direction:column}
.card.c1{--c:var(--red)}.card.c2{--c:var(--amber)}.card.c3{--c:var(--teal)}.card.c4{--c:#c3bdac}
.card .ch{display:flex;align-items:baseline;gap:9px;flex-wrap:wrap;margin-bottom:4px}
.card h3{font-size:19px;font-weight:600;line-height:1.2}
.card .size{font-family:var(--sans);font-size:10.5px;font-weight:700;color:var(--c);background:#fff;border:1px solid var(--line);border-radius:999px;padding:2px 9px;white-space:nowrap}
.card .rank{font-family:var(--sans);margin-left:auto;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--c)}
.card .desc{font-size:15px;color:var(--ink2);margin:6px 0 11px}
.card .impact{font-family:var(--sans);display:flex;gap:6px;flex-wrap:wrap;margin-bottom:13px}
.stat{font-size:10.5px;font-weight:600;color:var(--ink2);background:#fff;border:1px solid var(--line);border-radius:6px;padding:3px 8px}
.stat b{color:var(--ink)}.stat.churn{background:var(--red-s);border-color:#eccfc9;color:var(--red)}.stat.money{background:var(--teal-s);border-color:#c9e3d4;color:var(--teal)}.stat.mood{background:#f6f5f0;border-color:var(--rule);color:var(--ink2);font-style:italic;font-weight:600}.stat.miss{color:var(--soft);font-style:italic;font-weight:500}
.voice{border-top:1px dashed var(--line);padding-top:11px;margin-top:auto}
.voice .vl{font-family:var(--sans);font-size:9.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--soft);font-weight:700;margin-bottom:6px}
.q{font-size:14px;line-height:1.5;color:var(--ink);margin:7px 0;padding-left:14px;border-left:2px solid var(--c)}
.q .who{font-family:var(--sans);display:block;font-size:11px;color:var(--soft);font-weight:600;font-style:normal;margin-top:2px}
.flag{font-family:var(--sans);background:var(--amber-s);border:1px solid #ecd9b0;border-radius:7px;padding:9px 11px;margin-top:11px;font-size:12px;line-height:1.5;color:var(--ink2)}.flag b{color:#7a4f00}
.flag.gap{background:#f6f5f0;border-color:var(--line)}.flag.gap b{color:var(--ink2)}
.minor .row{font-size:14px;color:var(--ink2);padding:5px 0;border-top:1px solid var(--line)}
.minor .row:first-of-type{border-top:none}.minor .row b{color:var(--ink);font-weight:600}
.minor .who{font-family:var(--sans);font-size:11px;color:var(--soft)}
.goalnote{font-family:var(--sans);font-size:12.5px;color:var(--ink2);background:#eef3f0;border:1px solid #cfe0d7;border-radius:8px;padding:10px 13px;margin-top:14px}.goalnote b{color:var(--accent)}
.recs{margin-top:14px;border:1px solid var(--rule);border-radius:10px;overflow:hidden}
.rrow{display:flex;gap:16px;padding:16px 20px;border-top:1px solid var(--line)}
.rrow:first-child{border-top:none}
.rnum{font-family:var(--sans);width:26px;height:26px;border-radius:6px;background:var(--accent);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13px;flex-shrink:0}
.rbody .rt{font-size:17px;font-weight:600;line-height:1.3}
.rbody .rm{font-family:var(--sans);font-size:12.5px;color:var(--ink2);margin-top:4px;line-height:1.55}.rbody .rm b{color:var(--ink)}
.rbody .moves{font-family:var(--sans);font-size:11.5px;color:var(--accent);font-weight:600;margin-top:4px}
.notdoing{font-family:var(--sans);font-size:12.5px;color:var(--ink2);background:#faf9f5;border:1px solid var(--line);border-radius:8px;padding:11px 14px;margin-top:14px;line-height:1.6}.notdoing b{color:var(--ink)}
.basisnote{font-family:var(--sans);font-size:12.5px;color:#7a4f00;background:var(--amber-s);border:1px solid #ecd9b0;border-radius:8px;padding:10px 13px;margin-top:12px;line-height:1.6}.basisnote b{color:#5e3c00}
.softnext{font-family:var(--sans);font-size:12.5px;color:var(--soft);margin-top:12px;font-style:italic}
@media print{body{padding:0;background:#fff}.page{max-width:100%;padding:0}.card,.rrow,.recs,.glance,.radarwrap{break-inside:avoid}}
@media(max-width:680px){.grid{grid-template-columns:1fr}.page{padding:28px 22px}}
"""

# ── JSON schema for the report DATA (the model returns this, never HTML) ──────


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required,
            "additionalProperties": False}


_S = {"type": "string"}
_QUOTE = _obj({"text": _S, "attr": _S}, ["text", "attr"])

SCHEMA: dict = _obj(
    {
        "eyebrow": _S,              # "Acme · Product" — company/team above the title
        "period": _S,               # "6 January 2026 – 30 June 2026"
        "period_note": _S,          # "176 days · last two quarters" ("" ok)
        "deck": _S,                 # optional italic one-line headline ("" ok)
        "asked": _S,                # the user's request, verbatim
        "honored": _S,              # one line on how the ask was honored ("" ok)
        "run_line": _obj({
            "scope": _S,            # filters applied, or "no filters applied"
            "excluded": _S,         # what the filter excluded, named ("" ok)
            "sources": _S,          # e.g. "14 CSM calls · 218 tickets"
            "coverage": _S,         # accounts of base + records captured vs counted
            "goals": _S,            # the team's goal & tracked metrics
        }, ["scope", "excluded", "sources", "coverage", "goals"]),
        "tldr": _obj({
            "source_line": _S,      # italic source/method sentence
            "intro": _S,            # lead-in before the enumerated problems
            "close": _S,            # "What this means for us" prose
        }, ["source_line", "intro", "close"]),
        "findings": {"type": "array", "items": _obj({
            "problem": _S,          # bolded: who is stuck, with what, why they can't fix it
            "sentence": _S,         # the supporting sentence after the bold problem
            "quote": _S,            # one short verbatim quote ("" ok)
            "quote_attr": _S,       # "— Account (status)" ("" ok)
        }, ["problem", "sentence", "quote", "quote_attr"])},
        "glance": {"type": "array", "items": _obj({
            "problem": _S,
            "accounts": _S,                                  # display, e.g. "24 (59%)"
            "accounts_n": {"type": "integer"},               # numeric, feeds the radar
            "frustration": {"type": "integer", "minimum": 1, "maximum": 5},
            "tone": _S,                                      # plain-language tone read
            "metric": _S,                                    # goal metric, or "none identified"
            "metric_none": {"type": "boolean"},              # true → dimmed metric cell
            "minor": {"type": "boolean"},                    # true → dimmed one-off row, off the radar
        }, ["problem", "accounts", "accounts_n", "frustration", "tone",
            "metric", "metric_none", "minor"])},
        "glance_notes": _S,          # denominator + dedup rule · captured-vs-not-counted breakdown · how frustration was derived
        "radar_read": _S,            # prose read of the volume/frustration divergences
        "themes": {"type": "array", "items": _obj({
            "title": _S,
            "size_chip": _S,                                 # "24 accts · 59%"
            "rank_label": _S,                                # "Critical" / "Wide · calm" / "Low"
            "tier": {"type": "string",
                     "enum": ["critical", "high", "medium", "minor"]},
            "description": _S,
            "stats": {"type": "array", "items": _obj({
                "text": _S,
                "kind": {"type": "string",
                         "enum": ["plain", "mood", "churn", "money", "miss"]},
            }, ["text", "kind"])},
            "quotes": {"type": "array", "items": _QUOTE},    # 2–3 real quotes
            "rows": {"type": "array", "items": _obj({        # minor-bucket card rows
                "text": _S, "who": _S,
            }, ["text", "who"])},
            "flag": _S,                                      # silent-killer / vocal-minority / quote-gap note ("" ok)
            "flag_kind": {"type": "string", "enum": ["warn", "gap", ""]},
        }, ["title", "size_chip", "rank_label", "tier", "description", "stats",
            "quotes", "rows", "flag", "flag_kind"])},
        "goal_note": _S,             # how the recommendations were selected (goal fit)
        "recommendations": {"type": "array", "items": _obj({
            "title": _S,
            "why": _S,
            "moves": _S,             # "weekly participation ↑ · gross retention ↑"
        }, ["title", "why", "moves"])},
        "not_recommended": _S,       # REQUIRED prose: what was passed over and why
        "basis_note": _S,            # Tier-1 ranking caveat when no behavior/commercial data ("" otherwise)
    },
    ["eyebrow", "period", "period_note", "deck", "asked", "honored", "run_line",
     "tldr", "findings", "glance", "glance_notes", "radar_read", "themes",
     "goal_note", "recommendations", "not_recommended", "basis_note"],
)

_TIER_CLASS = {"critical": "c1", "high": "c3", "medium": "c2", "minor": "c4"}


# ── Rendering (deterministic; all model text HTML-escaped) ────────────────────

def _e(s) -> str:
    """HTML-escape any model-supplied value (None → '')."""
    return html.escape(str(s)) if s is not None else ""


def _radar_svg(axes: list[dict]) -> str:
    """Volume-vs-frustration radar as inline SVG, computed from the glance rows
    (never model-drawn). `axes` items carry label, volume (accounts_n) and
    frustration (1–5). Needs ≥3 axes to draw a polygon; returns "" otherwise."""
    if len(axes) < 3:
        return ""
    cx, cy, radius = 330.0, 208.0, 132.0
    n = len(axes)
    vmax = max(1, max(int(a.get("volume") or 0) for a in axes))

    def pt(i: int, frac: float) -> tuple[float, float]:
        ang = math.radians(-90 + i * 360.0 / n)
        return cx + radius * frac * math.cos(ang), cy + radius * frac * math.sin(ang)

    def poly(fracs: list[float]) -> str:
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in
                        (pt(i, f) for i, f in enumerate(fracs)))

    rings = "".join(
        f'<polygon points="{poly([r] * n)}" fill="none" '
        f'stroke="{"#cfcabc" if r == 1.0 else "#e3e0d6"}"/>'
        for r in (0.25, 0.5, 0.75, 1.0)
    )
    spokes = "".join(
        f'<line x1="{cx:.0f}" y1="{cy:.0f}" x2="{pt(i, 1.0)[0]:.1f}" y2="{pt(i, 1.0)[1]:.1f}"/>'
        for i in range(n)
    )
    vol_fracs = [max(0.0, min(1.0, (int(a.get("volume") or 0)) / vmax)) for a in axes]
    fr_fracs = [max(0.0, min(1.0, (int(a.get("frustration") or 0)) / 5.0)) for a in axes]
    dots_v = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5"/>'
                     for x, y in (pt(i, f) for i, f in enumerate(vol_fracs)))
    dots_f = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5"/>'
                     for x, y in (pt(i, f) for i, f in enumerate(fr_fracs)))

    labels = []
    for i, a in enumerate(axes):
        x, y = pt(i, 1.17)
        cos = math.cos(math.radians(-90 + i * 360.0 / n))
        anchor = "middle" if abs(cos) < 0.35 else ("start" if cos > 0 else "end")
        labels.append(f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}">'
                      f"{_e(a.get('label'))}</text>")
    scale = ['<text x="337" y="204">0</text>'] + [
        f'<text x="337" y="{cy - radius * r + 4:.0f}">{vmax * r:.0f} · {5 * r:.1f}</text>'
        for r in (0.25, 0.5, 0.75, 1.0)
    ]
    return (
        '<div class="radarwrap">'
        '<div class="rlegend">'
        f'<span><i style="background:#1f6f52"></i>Volume — accounts affected (0–{vmax})</span>'
        '<span><i style="background:#bb463c"></i>Frustration — 1 to 5, from their language</span>'
        "</div>"
        '<svg viewBox="0 0 660 400" xmlns="http://www.w3.org/2000/svg" '
        'style="width:100%;height:auto;font-family:Inter,system-ui,sans-serif">'
        f"{rings}"
        f'<g stroke="#eceae2">{spokes}</g>'
        f'<polygon points="{poly(fr_fracs)}" fill="#bb463c" fill-opacity="0.12" '
        'stroke="#bb463c" stroke-width="2"/>'
        f'<polygon points="{poly(vol_fracs)}" fill="#1f6f52" fill-opacity="0.14" '
        'stroke="#1f6f52" stroke-width="2"/>'
        f'<g fill="#1f6f52">{dots_v}</g>'
        f'<g fill="#bb463c">{dots_f}</g>'
        f'<g font-size="12" font-weight="600" fill="#403c34">{"".join(labels)}</g>'
        f'<g font-size="10.5" fill="#8a857a" text-anchor="start">{"".join(scale)}</g>'
        "</svg></div>"
    )


def _finding(i: int, f: dict) -> str:
    quote = ""
    if f.get("quote"):
        attr = f" {_e(f.get('quote_attr'))}" if f.get("quote_attr") else ""
        quote = f'<span class="vq">"{_e(f.get("quote"))}"{attr}</span>'
    return (
        '<div class="item">'
        f'<span class="hash">#{i}</span><b>{_e(f.get("problem"))}</b> '
        f"{_e(f.get('sentence'))}{quote}</div>"
    )


def _glance_row(g: dict) -> str:
    fr = min(5, max(1, int(g.get("frustration") or 1)))
    minor = g.get("minor")
    problem = _e(g.get("problem")) if minor else f"<b>{_e(g.get('problem'))}</b>"
    metric_cls = "metric none" if g.get("metric_none") else "metric"
    row_cls = ' class="minor"' if minor else ""
    return (
        f"<tr{row_cls}>"
        f"<td>{problem}</td>"
        f"<td>{_e(g.get('accounts'))}</td>"
        f'<td class="f{fr}">{fr} / 5</td>'
        f'<td class="sent">{_e(g.get("tone"))}</td>'
        f'<td class="{metric_cls}">{_e(g.get("metric"))}</td></tr>'
    )


def _theme_card(t: dict) -> str:
    cls = _TIER_CLASS.get(str(t.get("tier") or "").lower(), "c2")
    minor = cls == "c4"
    stats = "".join(
        f'<span class="stat{" " + s["kind"] if s.get("kind") not in (None, "", "plain") else ""}">'
        f"{_e(s.get('text'))}</span>"
        for s in (t.get("stats") or [])
    )
    impact = f'<div class="impact">{stats}</div>' if stats else ""
    if t.get("rows"):
        voice_label = "🗣 What was asked"
        body = "".join(
            f'<div class="row">{_e(r.get("text"))} '
            f'<span class="who">{_e(r.get("who"))}</span></div>'
            for r in (t.get("rows") or [])
        )
    else:
        voice_label = "🗣 Voice of customer"
        body = "".join(
            f'<div class="q">{_e(q.get("text"))}'
            f'<span class="who">{_e(q.get("attr"))}</span></div>'
            for q in (t.get("quotes") or [])
        )
    flag = ""
    if t.get("flag"):
        flag_cls = "flag gap" if t.get("flag_kind") == "gap" else "flag"
        flag = f'<div class="{flag_cls}">{_e(t.get("flag"))}</div>'
    return (
        f'<div class="card {cls}{" minor" if minor else ""}">'
        '<div class="ch">'
        f"<h3>{_e(t.get('title'))}</h3>"
        f'<span class="size">{_e(t.get("size_chip"))}</span>'
        f'<span class="rank">{_e(t.get("rank_label"))}</span>'
        "</div>"
        f'<div class="desc">{_e(t.get("description"))}</div>'
        f"{impact}"
        f'<div class="voice"><div class="vl">{voice_label}</div>{body}</div>'
        f"{flag}</div>"
    )


def _rec(i: int, r: dict) -> str:
    # No CTAs inside the report: the document renders in a script-less sandboxed
    # iframe where nothing is clickable — the REAL "Generate PRD" action lives in
    # the content panel's bottom bar, outside the iframe.
    return (
        '<div class="rrow">'
        f'<div class="rnum">{i}</div>'
        '<div class="rbody">'
        f'<div class="rt">{_e(r.get("title"))}</div>'
        f'<div class="rm"><b>Why:</b> {_e(r.get("why"))}</div>'
        f'<div class="moves">Moves: {_e(r.get("moves"))}</div>'
        "</div></div>"
    )


def render_html(data: dict) -> str:
    """Populate the pinned VoC v3 template from the model's report data. Returns
    a self-contained HTML document. The h1 is the literal skill-mandated title;
    the radar is computed from the non-minor glance rows."""
    rl = data.get("run_line") or {}
    period_note = (
        f" <span>· {_e(data.get('period_note'))}</span>" if data.get("period_note") else ""
    )
    deck = f'<p class="deck">{_e(data.get("deck"))}</p>' if data.get("deck") else ""
    honored = f" {_e(data.get('honored'))}" if data.get("honored") else ""
    excluded = (
        f" · <b>Excluded by filter:</b> {_e(rl.get('excluded'))}" if rl.get("excluded") else ""
    )
    findings = "".join(
        _finding(i + 1, f) for i, f in enumerate(data.get("findings") or [])
    )
    rows = "".join(_glance_row(g) for g in (data.get("glance") or []))
    radar = _radar_svg([
        {"label": g.get("problem"), "volume": g.get("accounts_n"),
         "frustration": g.get("frustration")}
        for g in (data.get("glance") or []) if not g.get("minor")
    ])
    radar_read = (
        f'<div class="readnote">{_e(data.get("radar_read"))}</div>'
        if data.get("radar_read") else ""
    )
    themes = "".join(_theme_card(t) for t in (data.get("themes") or []))
    recs = "".join(_rec(i + 1, r) for i, r in enumerate(data.get("recommendations") or []))
    basis = (
        f'<div class="basisnote">{_e(data.get("basis_note"))}</div>'
        if data.get("basis_note") else ""
    )
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f"<title>Voice of Customer Report — {_e(data.get('period'))}</title>\n"
        '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '<link href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,ital,wght@6..72,0,400;6..72,0,500;6..72,0,600;6..72,0,700;6..72,1,400&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">\n'
        f"<style>{_STYLE}</style>\n</head>\n<body>\n"
        '<div class="page">\n'
        f'<div class="eyebrow">{_e(data.get("eyebrow"))}</div>\n'
        "<h1>Voice of Customer Report</h1>\n"
        f'<div class="period">{_e(data.get("period"))}{period_note}</div>\n'
        f"{deck}"
        f'<div class="askedline"><b>Asked:</b> "{_e(data.get("asked"))}"{honored}</div>\n'
        f'<div class="runline"><b>Scope:</b> {_e(rl.get("scope"))}{excluded} · '
        f'<b>Sources:</b> {_e(rl.get("sources"))} · '
        f'<b>Coverage:</b> {_e(rl.get("coverage"))} · '
        f'<b>Goal &amp; metrics:</b> {_e(rl.get("goals"))}</div>\n'
        "<h2>TL;DR</h2>\n"
        '<div class="sec tldr">\n'
        f'<p class="src">{_e((data.get("tldr") or {}).get("source_line"))}</p>\n'
        f'<p class="intro">{_e((data.get("tldr") or {}).get("intro"))}</p>\n'
        f'<div class="enum">{findings}</div>\n'
        f'<p class="close"><b>What this means for us:</b> '
        f'{_e((data.get("tldr") or {}).get("close"))}</p>\n'
        "</div>\n"
        "<h2>Problems at a glance</h2>\n"
        '<div class="sec"><table class="glance">\n'
        "<tr><th>Problem</th><th>Accounts</th><th>Frustration</th>"
        "<th>How they sound</th><th>Metric impacted</th></tr>\n"
        f"{rows}</table>\n"
        f'<p class="tnote">{_e(data.get("glance_notes"))}</p>\n'
        "</div>\n"
        "<h2>Volume vs frustration</h2>\n"
        f'<div class="sec">{radar}{radar_read}</div>\n'
        "<h2>Themes — what we're seeing</h2>\n"
        f'<div class="grid">{themes}</div>\n'
        "<h2>Recommendations</h2>\n"
        '<div class="sec">\n'
        f'<div class="goalnote">{_e(data.get("goal_note"))}</div>\n'
        f'<div class="recs">{recs}</div>\n'
        f'<div class="notdoing"><b>Deliberately not recommended:</b> '
        f'{_e(data.get("not_recommended"))}</div>\n'
        f"{basis}"
        '<p class="softnext">Any of these can be developed into a PRD.</p>\n'
        "</div>\n</div>\n</body>\n</html>"
    )


# ── LLM extraction ────────────────────────────────────────────────────────────

_SYSTEM = (
    "You produce a Voice of Customer report as STRUCTURED DATA that a fixed "
    "template renders — you do NOT write HTML, CSS, or SVG (the radar chart is "
    "drawn by the template from your glance numbers). Follow the "
    "voice-of-customer-report skill's method exactly, in order:\n"
    "- CAPTURE FIRST (see REFERENCE: CAPTURE.md): read every call in full and "
    "capture one record per mention with an origin tier before any counting. "
    "Apply the counting rule — `asserted`, `speculative` and `undetermined` "
    "records never count toward theme sizes; disclose records captured vs "
    "counted in run_line.coverage.\n"
    "- SCOPE to the user's request: resolve the window to explicit dates in "
    "`period`, quote the ask verbatim in `asked`, and state filters (or 'no "
    "filters applied') in run_line.scope. Percentages always carry their "
    "denominator, re-derived inside the applied scope.\n"
    "- FINDINGS ARE PROBLEMS: each TL;DR finding names who is stuck, with "
    "what, and why they can't fix it themselves — not an observation.\n"
    "- FRUSTRATION is an integer 1–5 read from observable language only "
    "(escalation words, blame/cancellation framing, repeat contacts, giving "
    "up, workarounds), each with a short plain-language tone read. State in "
    "glance_notes that it is analyst-assigned and can vary by a point.\n"
    "- Theme sizes are ACCOUNTS, deduped at report time (say so in "
    "glance_notes with the denominator). accounts_n is the integer that "
    "feeds the radar.\n"
    "- METRIC IMPACTED is a mapping, not a measurement: one tracked goal "
    "metric per problem, 'none identified' (metric_none=true) where none "
    "credibly applies, and 'X — asserted, not measured' where customers "
    "claim a link you cannot measure.\n"
    "- QUOTES are verbatim from the corpus with attribution — 2–3 strong "
    "ones per theme; flag a quote gap rather than manufacture one.\n"
    "- RECOMMENDATIONS: the most important ~5 selected by GOAL FIT, each "
    "naming the metric it moves; `not_recommended` must name what was "
    "passed over and why. Flag silent killers and vocal minorities in the "
    "theme `flag` fields.\n"
    "- If the run is Tier 1 (volume + frustration only — no churn/usage or "
    "commercial data), say so in `basis_note`; otherwise leave it empty.\n"
    "Every quote, count, and figure must come from the material provided "
    "below — never invent, estimate, or extrapolate any number."
)


def build(
    *,
    enterprise_id: str,
    question: str,
    corpus_text: str,
    source_line: str,
    model: str,
) -> str:
    """Run the report extraction over `corpus_text` and return rendered HTML.

    Raises on any failure so callers can fall back to a plain message."""
    result = llm_call(
        enterprise_id=enterprise_id,
        agent="qa",
        purpose="voc_report",
        model=model,
        system=_SYSTEM,
        input=f"Question: {question}\n\n{source_line}\n\n{corpus_text}",
        prompt_version="qa-voc-report-v2",
        json_schema=SCHEMA,
        skill=_VOC_SKILL,
        max_tokens=16000,
        # A full-window corpus (100+ calls, ~70k input tokens) + a big JSON
        # report exceeds the default per-request timeout — stream on the long
        # read timeout like the other document-scale generations.
        long_output=True,
    )
    data = result.output
    if not isinstance(data, dict):
        raise ValueError(f"voc_report: expected dict output, got {type(data).__name__}")
    return render_html(data)
