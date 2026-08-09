"""Display labels for skill/pipeline ids.

Split out of the deleted `app.skills.catalog`. That module was catalog POLICY
over a ~78-skill vendored library — category, routability, the router's menu —
and all of it went with the built-in skill layer. `humanize_label` did not: it
turns an id into prose for surfaces that outlived the catalog (a captured
report's fallback title, the public share page's kind label), and those ids are
now pipeline ids rather than menu entries.

Deliberately dependency-free — no disk read, no DB — so a label never depends
on a skill still being vendored.
"""
from __future__ import annotations

# Acronyms to upper-case when humanising an id into a display label.
_ACRONYMS = {
    "prd", "okr", "nct", "gtm", "rice", "saas", "cir", "jtbd", "ice",
    "wsjf", "pmf", "nps", "rag", "ab", "kpi", "ui", "ux",
}


def humanize_label(skill_id: str) -> str:
    """`prd-author` → `PRD author`, `okr-nct` → `OKR NCT`, `roadmap` → `Roadmap`."""
    words = skill_id.split("-")
    out = []
    for i, w in enumerate(words):
        if w in _ACRONYMS:
            out.append(w.upper())
        elif i == 0:
            out.append(w.capitalize())
        else:
            out.append(w)
    return " ".join(out)
