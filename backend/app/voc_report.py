"""Voice-of-Customer report — structured data → fixed HTML template.

The `voice-of-customer-report` skill renders the polished report in
`backend/skills/voice-of-customer-report/example-output.html` (TL;DR panel with
VOL/SEV pills, a user-problem table, quote-led theme cards, a gated recommendation
list). Asking the model to hand-author that HTML is slow, expensive, and the CSS
drifts run to run. Instead the model emits ONLY the report's data as JSON (`SCHEMA`)
and this module's deterministic template (`render_html`) populates the pinned
HTML/CSS — pixel-identical every run, and XSS-safe: every model-supplied string is
HTML-escaped, and the report only ever renders inside the frontend's sandboxed,
script-less iframe (see EvidenceHtmlBrief).

`build()` runs the one attributed `llm_call` (with the VoC SKILL.md method bound, so
the framing/counts/gate hard rules still govern the extraction) and returns the
rendered HTML string, which callers drop into the Ask payload's `answer` field.
"""
from __future__ import annotations

import html
import logging

from app.graph.gateway import llm_call

logger = logging.getLogger(__name__)

_VOC_SKILL = "voice-of-customer-report"

# The verbatim <style> block from example-output.html — the pinned design. Kept
# byte-for-byte so the rendered report matches the reference exactly (minus the
# sample banner and brand chrome, which are demo-only and never emitted here).
_STYLE = """
  :root{
    --desk:#E9E7E2; --page:#FFFFFF; --ink:#1F241F; --sec:#5B615B; --accent:#1A6B47;
    --happy-bg:#E7F1EA; --happy-fg:#1A6B47; --edge-bg:#FBF0DC; --edge-fg:#8A5A12;
    --fail-bg:#F9E7E4; --fail-fg:#9C3223; --hair:#E3E1DC; --quiet:#F6F5F2;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--desk);font-family:'Inter',sans-serif;font-size:15px;line-height:1.6;color:var(--ink);padding:40px 16px}
  .page{max-width:900px;margin:0 auto;background:var(--page);border-radius:2px;box-shadow:0 2px 14px rgba(31,36,31,.10);padding:72px;position:relative}
  .chips{max-width:900px;margin:0 auto 12px;display:flex;gap:8px;flex-wrap:wrap;font-family:'IBM Plex Mono',monospace;font-size:11.5px}
  .chip{padding:4px 10px;border-radius:2px;background:#fff;color:var(--sec);box-shadow:0 1px 3px rgba(31,36,31,.08)}
  .chip.green{background:var(--happy-bg);color:var(--happy-fg)}
  h1{font-family:'Spectral',serif;font-weight:600;font-size:33px;line-height:1.25;margin-bottom:22px}
  .eyebrow{font-size:10.5px;text-transform:uppercase;letter-spacing:.14em;color:var(--accent);font-weight:600;margin:44px 0 14px;padding-top:22px;border-top:1px solid var(--hair)}
  p{margin-bottom:12px}

  /* ===== TL;DR panel (redesigned) ===== */
  .tldr{background:var(--quiet);border-radius:2px;padding:0;overflow:hidden;margin-bottom:8px}
  .tldr-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:16px 26px;background:var(--happy-bg);border-bottom:2px solid var(--accent)}
  .tldr-head .t{font-size:11px;text-transform:uppercase;letter-spacing:.18em;color:var(--accent);font-weight:600}
  .tldr-src{display:flex;gap:6px;flex-wrap:wrap}
  .srcchip{font-family:'IBM Plex Mono',monospace;font-size:10.5px;color:var(--accent);background:#fff;border:1px solid #C7DCCE;border-radius:2px;padding:2px 8px}
  .tldr-lede{font-family:'Spectral',serif;font-size:19px;line-height:1.5;padding:22px 26px 6px}
  .tldr-lede b{font-weight:600}
  .tfind{display:grid;grid-template-columns:44px 1fr 172px;gap:14px;align-items:start;padding:16px 26px;border-top:1px solid var(--hair)}
  .tfind .disc{width:30px;height:30px;border-radius:50%;background:var(--accent);color:#fff;font-family:'IBM Plex Mono',monospace;font-size:12px;display:flex;align-items:center;justify-content:center;margin-top:2px}
  .tfind h4{font-size:15px;font-weight:600;margin-bottom:2px}
  .tfind .d{font-size:13.5px;color:var(--sec)}
  .tfind .metric{font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--accent);margin-top:5px}
  .tfind .side{display:flex;flex-direction:column;gap:5px;align-items:flex-end}
  .mini{font-family:'IBM Plex Mono',monospace;font-size:10.5px;padding:3px 8px;border-radius:2px;white-space:nowrap}
  .mini .k{opacity:.65;margin-right:4px}
  .mini.high{background:var(--fail-bg);color:var(--fail-fg)}
  .mini.med{background:var(--edge-bg);color:var(--edge-fg)}
  .mini.low{background:var(--happy-bg);color:var(--happy-fg)}
  .flag{display:inline-block;background:var(--fail-bg);color:var(--fail-fg);font-family:'IBM Plex Mono',monospace;font-size:10.5px;padding:2px 7px;border-radius:2px;margin-left:6px;vertical-align:middle}

  /* ===== at a glance ===== */
  table{width:100%;border-collapse:collapse;font-size:13.5px;margin:6px 0 4px}
  th{font-size:10.5px;text-transform:uppercase;letter-spacing:.08em;color:var(--sec);text-align:left;padding:8px 10px;border-bottom:1.5px solid var(--ink)}
  th.pair{border-bottom-color:var(--accent);color:var(--accent)}
  td{padding:11px 10px;border-bottom:1px solid var(--hair);vertical-align:top}
  .mono{font-family:'IBM Plex Mono',monospace;font-size:12px}
  .pill{display:inline-block;padding:3px 10px;border-radius:2px;font-family:'IBM Plex Mono',monospace;font-size:11px;white-space:nowrap}
  .pill.high{background:var(--fail-bg);color:var(--fail-fg)}
  .pill.med{background:var(--edge-bg);color:var(--edge-fg)}
  .pill.low{background:var(--happy-bg);color:var(--happy-fg)}
  .sub{display:block;font-family:'IBM Plex Mono',monospace;font-size:10px;color:var(--sec);margin-top:4px}
  td.vs{border-left:2px solid var(--quiet)}
  .impact-metric{font-family:'IBM Plex Mono',monospace;font-size:11.5px;color:var(--accent);display:block;margin-bottom:2px}
  .impact-how{font-size:12.5px}
  .impact-rev{display:block;font-family:'IBM Plex Mono',monospace;font-size:11px;margin-top:4px;color:var(--fail-fg)}
  .impact-rev.na{color:var(--sec)}

  /* themes */
  .themes{display:grid;grid-template-columns:1fr 1fr;gap:16px}
  .theme{border:1px solid var(--hair);border-radius:2px;padding:18px 20px}
  .theme h3{font-family:'Spectral',serif;font-weight:600;font-size:18px;margin-bottom:4px}
  .theme .size{font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--sec);margin-bottom:8px}
  .theme .size b{color:var(--accent);font-weight:500}
  .theme .desc{font-size:13.5px;color:var(--sec);margin-bottom:10px}
  .quote{font-family:'Spectral',serif;font-style:italic;font-size:15px;line-height:1.5;margin:0 0 8px;padding-left:12px;border-left:3px solid var(--accent)}
  .quote .attr{display:block;font-family:'IBM Plex Mono',monospace;font-style:normal;font-size:10.5px;color:var(--sec);margin-top:3px}
  .impact{background:var(--happy-bg);border-radius:2px;padding:8px 12px;font-size:12.5px;color:var(--happy-fg);margin-top:6px}
  .impact.warn{background:var(--edge-bg);color:var(--edge-fg)}

  /* recs */
  .rec{border:1px solid var(--hair);border-radius:2px;padding:16px 20px;margin-bottom:12px;display:flex;gap:16px;align-items:flex-start}
  .rec .rank{font-family:'IBM Plex Mono',monospace;font-size:13px;color:var(--accent);font-weight:500;flex:0 0 30px;padding-top:2px}
  .rec h4{font-size:15px;font-weight:600;margin-bottom:4px}
  .rec .why{font-size:13px;color:var(--sec);margin-bottom:8px}
  .rec .metric{font-family:'IBM Plex Mono',monospace;font-size:11.5px;color:var(--accent);margin-bottom:10px}
  .cta{display:inline-block;font-family:'IBM Plex Mono',monospace;font-size:11.5px;padding:6px 12px;border-radius:2px;margin-right:8px;cursor:pointer}
  .cta.primary{background:var(--accent);color:#fff}
  .cta.ghost{border:1px solid var(--accent);color:var(--accent)}
  .gate{font-family:'IBM Plex Mono',monospace;font-size:11.5px;color:var(--sec);margin:-4px 0 14px}
  @media(max-width:760px){.page{padding:32px 20px}.themes{grid-template-columns:1fr}.tfind{grid-template-columns:38px 1fr}.tfind .side{flex-direction:row;justify-content:flex-start;grid-column:2}}
  @media print{body{background:#fff;padding:0}.chips{display:none}.page{box-shadow:none;padding:40px}}
"""

# ── JSON schema for the report DATA (the model returns this, never HTML) ──────

_LEVEL = {"type": "string", "enum": ["low", "med", "high"]}


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required,
            "additionalProperties": False}


SCHEMA: dict = _obj(
    {
        "title": {"type": "string"},          # e.g. "Voice of Customer — Q2 2026"
        "lede": {"type": "string"},            # one-sentence arc of the quarter
        "coverage": {"type": "string"},        # classification-coverage note for a chip
        "sources": {"type": "array", "items": {"type": "string"}},   # TL;DR head chips
        "top_findings": {"type": "array", "items": _obj({
            "problem": {"type": "string"},
            "sentence": {"type": "string"},
            "impact_line": {"type": "string"},   # the "IMPACTS → …" mono line
            "vol": _obj({"level": _LEVEL, "count": {"type": "string"}}, ["level", "count"]),
            "sev": _obj({"level": _LEVEL}, ["level"]),
            "silent_killer": {"type": "boolean"},
        }, ["problem", "sentence", "impact_line", "vol", "sev", "silent_killer"])},
        "problems": {"type": "array", "items": _obj({
            "problem": {"type": "string"},
            "vol": _obj({"level": _LEVEL, "count": {"type": "string"}}, ["level", "count"]),
            "sev": _obj({"level": _LEVEL, "note": {"type": "string"}}, ["level", "note"]),
            "metric": {"type": "string"},
            "by_how_much": {"type": "string"},
            "revenue_line": {"type": "string"},
            "revenue_unknown": {"type": "boolean"},
            "silent_killer": {"type": "boolean"},
        }, ["problem", "vol", "sev", "metric", "by_how_much", "revenue_line",
            "revenue_unknown", "silent_killer"])},
        "long_tail": _obj({
            "label": {"type": "string"}, "count_note": {"type": "string"},
        }, ["label", "count_note"]),
        "themes": {"type": "array", "items": _obj({
            "title": {"type": "string"},
            "size_line": {"type": "string"},
            "description": {"type": "string"},
            "quotes": {"type": "array", "items": _obj({
                "text": {"type": "string"}, "attr": {"type": "string"},
            }, ["text", "attr"])},
            "impact_line": {"type": "string"},
            "impact_warn": {"type": "boolean"},
            "silent_killer": {"type": "boolean"},
        }, ["title", "size_line", "description", "quotes", "impact_line",
            "impact_warn", "silent_killer"])},
        "gate": _obj({
            "candidates": {"type": "integer"},
            "selected": {"type": "integer"},
            "routed": {"type": "integer"},
        }, ["candidates", "selected", "routed"]),
        "goals_note": {"type": "string"},   # eyebrow suffix, e.g. "activation · NRR"
        "recommendations": {"type": "array", "items": _obj({
            "title": {"type": "string"},
            "description": {"type": "string"},
            "impact_line": {"type": "string"},
            "investigation_only": {"type": "boolean"},
        }, ["title", "description", "impact_line", "investigation_only"])},
    },
    ["title", "lede", "sources", "top_findings", "problems", "themes", "gate",
     "recommendations"],
)

_FLAG = '<span class="flag">🔇 SILENT KILLER</span>'
_FLAG_SM = '<span class="flag">🔇</span>'


# ── Rendering (deterministic; all model text HTML-escaped) ────────────────────

def _e(s) -> str:
    """HTML-escape any model-supplied value (None → '')."""
    return html.escape(str(s)) if s is not None else ""


def _lvl(x) -> str:
    """Normalise a rating to one of the pill classes."""
    v = str(x or "").strip().lower()
    return v if v in ("low", "med", "high") else "med"


def _lede(text: str) -> str:
    """Render the lede, honouring **bold** spans (the only markup the lede uses)
    while escaping everything else."""
    import re
    parts = re.split(r"\*\*(.+?)\*\*", text or "")
    out = []
    for i, part in enumerate(parts):
        out.append(f"<b>{_e(part)}</b>" if i % 2 else _e(part))
    return "".join(out)


def _tfind(i: int, f: dict) -> str:
    flag = _FLAG if f.get("silent_killer") else ""
    vol, sev = f.get("vol") or {}, f.get("sev") or {}
    return (
        '<div class="tfind">'
        f'<div class="disc">#{i}</div>'
        '<div>'
        f'<h4>{_e(f.get("problem"))}{flag}</h4>'
        f'<div class="d">{_e(f.get("sentence"))}</div>'
        f'<div class="metric">{_e(f.get("impact_line"))}</div>'
        '</div>'
        '<div class="side">'
        f'<span class="mini {_lvl(vol.get("level"))}"><span class="k">VOL</span>'
        f'{_lvl(vol.get("level"))} · {_e(vol.get("count"))}</span>'
        f'<span class="mini {_lvl(sev.get("level"))}"><span class="k">SEV</span>'
        f'{_lvl(sev.get("level"))}</span>'
        '</div></div>'
    )


def _prow(p: dict) -> str:
    flag = f" {_FLAG_SM}" if p.get("silent_killer") else ""
    vol, sev = p.get("vol") or {}, p.get("sev") or {}
    rev_cls = "impact-rev na" if p.get("revenue_unknown") else "impact-rev"
    return (
        "<tr>"
        f"<td>{_e(p.get('problem'))}{flag}</td>"
        f'<td><span class="pill {_lvl(vol.get("level"))}">{_lvl(vol.get("level"))}</span>'
        f'<span class="sub">{_e(vol.get("count"))}</span></td>'
        f'<td class="vs"><span class="pill {_lvl(sev.get("level"))}">{_lvl(sev.get("level"))}</span>'
        f'<span class="sub">{_e(sev.get("note"))}</span></td>'
        f'<td><span class="impact-metric">{_e(p.get("metric"))}</span></td>'
        "<td>"
        f'<span class="impact-how">{_e(p.get("by_how_much"))}</span>'
        f'<span class="{rev_cls}">{_e(p.get("revenue_line"))}</span>'
        "</td></tr>"
    )


def _long_tail(lt: dict) -> str:
    return (
        "<tr>"
        f"<td>{_e(lt.get('label'))}</td>"
        '<td><span class="pill low">low</span>'
        f'<span class="sub">{_e(lt.get("count_note"))}</span></td>'
        '<td class="vs"><span class="pill low">low</span></td>'
        '<td><span class="impact-how" style="color:var(--sec)">—</span></td>'
        '<td><span class="impact-how" style="color:var(--sec)">monitor — no metric movement claimed</span></td>'
        "</tr>"
    )


def _theme(t: dict) -> str:
    flag = f" {_FLAG_SM}" if t.get("silent_killer") else ""
    quotes = "".join(
        f'<div class="quote">{_e(q.get("text"))}'
        f'<span class="attr">{_e(q.get("attr"))}</span></div>'
        for q in (t.get("quotes") or [])
    )
    impact_cls = "impact warn" if t.get("impact_warn") else "impact"
    return (
        '<div class="theme">'
        f"<h3>{_e(t.get('title'))}{flag}</h3>"
        f'<div class="size">{_e(t.get("size_line"))}</div>'
        f'<div class="desc">{_e(t.get("description"))}</div>'
        f"{quotes}"
        f'<div class="{impact_cls}">{_e(t.get("impact_line"))}</div>'
        "</div>"
    )


def _rec(i: int, r: dict) -> str:
    if r.get("investigation_only"):
        ctas = '<span class="cta ghost">Move to backlog</span>'
    else:
        ctas = ('<span class="cta primary">Generate PRD</span>'
                '<span class="cta ghost">Move to backlog</span>')
    return (
        '<div class="rec">'
        f'<div class="rank">R{i}</div>'
        '<div>'
        f'<h4>{_e(r.get("title"))}</h4>'
        f'<div class="why">{_e(r.get("description"))}</div>'
        f'<div class="metric">{_e(r.get("impact_line"))}</div>'
        f"{ctas}"
        '</div></div>'
    )


def render_html(data: dict) -> str:
    """Populate the pinned VoC template from the model's report data. Returns a
    self-contained HTML document (no sample banner, no brand chrome)."""
    src_chips = "".join(
        f'<span class="srcchip">{_e(s)}</span>' for s in (data.get("sources") or [])
    )
    findings = "".join(_tfind(i + 1, f) for i, f in enumerate(data.get("top_findings") or []))
    rows = "".join(_prow(p) for p in (data.get("problems") or []))
    if data.get("long_tail"):
        rows += _long_tail(data["long_tail"])
    themes = "".join(_theme(t) for t in (data.get("themes") or []))
    recs = "".join(_rec(i + 1, r) for i, r in enumerate(data.get("recommendations") or []))

    gate = data.get("gate") or {}
    cand, sel, routed = gate.get("candidates", 0), gate.get("selected", 0), gate.get("routed", 0)
    gate_line = (
        f"PRIORITIZATION GATE PASSED ✓ — {_e(cand)} candidate actions identified · "
        f"{_e(sel)} selected · cap 5–7 unless impacts tie at the cut · "
        f"{_e(routed)} routed to monitor/backlog, not listed here"
    )
    goals = data.get("goals_note")
    rec_eyebrow = "Recommendations"
    if goals:
        rec_eyebrow += f" — selected by fit to this quarter's goals ({_e(goals)})"

    coverage_chip = (
        f'<span class="chip">{_e(data["coverage"])}</span>' if data.get("coverage") else ""
    )

    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f"<title>{_e(data.get('title') or 'Voice of Customer')}</title>\n"
        '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '<link href="https://fonts.googleapis.com/css2?family=Spectral:ital,wght@0,400;0,600;1,400;1,500&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">\n'
        f"<style>{_STYLE}</style>\n</head>\n<body>\n"
        '<div class="chips">'
        '<span class="chip green">voice-of-customer-report</span>'
        '<span class="chip">curated, direct-access sources only</span>'
        '<span class="chip">basis: volume + impact + commercial</span>'
        f"{coverage_chip}</div>\n"
        '<div class="page">\n'
        f"<h1>{_e(data.get('title') or 'Voice of Customer')}</h1>\n"
        '<div class="tldr">'
        '<div class="tldr-head"><span class="t">TL;DR</span>'
        f'<span class="tldr-src">{src_chips}</span></div>'
        f'<div class="tldr-lede">{_lede(data.get("lede") or "")}</div>'
        f"{findings}</div>\n"
        '<div class="eyebrow">User problems at a glance</div>\n'
        "<table><tr>"
        "<th>User problem</th><th class=\"pair\">Volume</th>"
        "<th class=\"pair\">Severity</th><th>Metric it impacts</th><th>By how much</th>"
        f"</tr>{rows}</table>\n"
        '<div class="eyebrow">Themes — in the customer\'s words</div>\n'
        f'<div class="themes">{themes}</div>\n'
        f'<div class="eyebrow">{rec_eyebrow}</div>\n'
        f'<div class="gate">{gate_line}</div>\n'
        f"{recs}\n"
        "</div>\n</body>\n</html>"
    )


# ── LLM extraction ────────────────────────────────────────────────────────────

_SYSTEM = (
    "You produce a Voice of Customer report as STRUCTURED DATA that a fixed "
    "template renders — you do NOT write HTML or CSS. Follow the "
    "voice-of-customer-report skill's method and hard rules exactly:\n"
    "- Name every problem as a USER problem (the difficulty from the user's "
    "side), never an internal/solution label.\n"
    "- Use real COUNTS, never percentages, for these qualitative sources.\n"
    "- Volume and severity each rate low/med/high; keep the raw count with the "
    "volume and a 2–4 word justification with the severity.\n"
    "- Every problem names ONE metric it impacts and quantifies by how much, and "
    "ALWAYS carries a revenue line: a sourced figure, or set revenue_unknown=true "
    "with revenue_line='revenue: 🅘 unknown'. Never estimate revenue.\n"
    "- Set silent_killer=true when volume is low but severity or dollars are high.\n"
    "- Confidence tiers 🅗 hard / 🅢 soft / 🅘 unknown belong inline on the counts "
    "and impact lines.\n"
    "- Prioritization gate: rank all candidate actions, select the top 5 (up to 7 "
    "only on a tie at the cut); put the true candidate/selected/routed counts in "
    "`gate`. `recommendations` holds ONLY the selected 5–7.\n"
    "- Quotes are VERBATIM from the corpus with source + date attribution.\n"
    "Every quote, count, and figure must come from the material provided below — "
    "never invent them."
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
        prompt_version="qa-voc-report-v1",
        json_schema=SCHEMA,
        skill=_VOC_SKILL,
        max_tokens=12000,
    )
    data = result.output
    if not isinstance(data, dict):
        raise ValueError(f"voc_report: expected dict output, got {type(data).__name__}")
    return render_html(data)
