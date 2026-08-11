"""PRD-tab chat grounding — the "CURRENT PRD CONTEXT" block.

A question typed in the chat next to an open PRD ("tighten the success
metrics", "which ticket covers the export flow?") is answerable only when the
model can actually see that PRD. This module assembles that context block:
the rendered PRD, the brief insight it was generated from, and the PRD's
evidence / tickets / prototype — the same artifact set the MCP tools expose,
fetched through the same tenant-gated helpers.

Best-effort by construction: a missing PRD, a foreign tenant, an absent
artifact, or any read error collapses to an empty string (or a skipped
section) so a PRD-tab ask degrades to the plain corpus+KG answer rather than
failing.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Per-section character caps. v3 PRD/evidence bodies are self-contained HTML
# pages; scripts/styles are stripped below, but the remaining markup is still
# verbose, so the caps are generous enough for a full document while keeping
# the whole block comfortably inside the answer model's context alongside the
# corpus + KG bundle.
_PRD_CAP = 60_000
_EVIDENCE_CAP = 30_000
_INSIGHT_CAP = 4_000
_TICKETS_CAP = 12_000

_HEADER = (
    "=== CURRENT PRD CONTEXT ===\n"
    "The user has this PRD open next to the chat. Questions like \"this PRD\", "
    "\"this document\", or unqualified asks about requirements/metrics/tickets "
    "refer to it. Document bodies below may be HTML — read the content, ignore "
    "the markup.\n"
)


def _strip_noise(html: str) -> str:
    """Drop <script>/<style>/comment blocks from an HTML document body — pure
    prompt noise that would eat most of the section cap before any content."""
    text = re.sub(r"(?is)<(script|style)\b.*?</\1\s*>", " ", html or "")
    return re.sub(r"(?s)<!--.*?-->", " ", text).strip()


def _cap(text: str, cap: int) -> str:
    text = (text or "").strip()
    if len(text) <= cap:
        return text
    return text[:cap] + "\n[… truncated]"


def _prd_section(prd: dict, rendered: dict) -> str:
    body = _strip_noise(rendered.get("payload_md") or "")
    lines = [
        f"## The PRD (id {prd['id']})",
        f"Title: {prd.get('title') or '(untitled)'}",
        f"Status: {prd.get('status') or 'unknown'}",
    ]
    if body:
        lines += ["Document:", _cap(body, _PRD_CAP)]
    return "\n".join(lines)


def _format_section(enterprise_id: str, prd: dict) -> str | None:
    """WHICH uploaded format wrote this PRD — read from the stamp the generation
    left on the row (`prds.artifact_template_id`), never inferred.

    This exists because inference produced a confidently wrong answer. Asked
    "what template did this PRD use?", the assistant had nothing authoritative
    in front of it, so it compared the document's section headings against the
    company's CONFLUENCE page templates and reported that the PRD used none of
    them and was "a custom structure" — a coherent paragraph about entirely the
    wrong kind of template, for a PRD that had in fact been written in one of
    the company's own uploaded formats.

    THE STAMP IS A HISTORICAL FACT AND OUTRANKS WHAT IS ACTIVE NOW. A PRD
    written in the format that was active in March is still a March-format PRD
    after someone activates a different one in June, and this section says so
    explicitly — otherwise a model holding both this and the workspace's format
    library will helpfully "correct" the stamp to whichever format is currently
    active, which is exactly the reasoning that must not happen here.

    Absent stamp → the built-in format, which is what NULL has always meant on
    that column (`db.prds.set_prd_artifact_template` writes nothing for it).
    Stated rather than omitted: "no custom format" is the answer to the
    question, and a missing section would send the model looking for one."""
    template_id = (prd.get("artifact_template_id") or "").strip()
    if not template_id:
        return (
            "## The format this PRD was written in\n"
            "Sprntly's built-in PRD format — this document was not written in "
            "one of the company's own uploaded formats."
        )
    from app.db.artifact_templates import get_template_by_id

    row = get_template_by_id(enterprise_id, template_id)
    if not row:
        # Stamped, but the format has since been deleted. Saying so beats both
        # silence and a guess: the shape of the document is still that format's.
        return (
            "## The format this PRD was written in\n"
            "One of the company's own uploaded formats, which has since been "
            "deleted from their library."
        )
    name = " ".join((row.get("name") or "").split()) or "(untitled)"
    line = f"“{name}”, one of the company's own uploaded PRD formats."
    if not row.get("is_active"):
        # The single most useful clause in this section: it is the difference
        # between "this document is in the format you expect" and "your active
        # format changed after this was written".
        line += (
            " It is NOT the company's currently active PRD format — this PRD "
            "was written in it, and later PRDs use whichever format is active "
            "now. That is not an error and does not need correcting."
        )
    return f"## The format this PRD was written in\n{line}"


def _insight_section(prd: dict) -> str | None:
    """The brief insight this PRD was generated from. Themed PRDs
    (ideation/chat/upload) anchor to the brief with a SENTINEL insight_index —
    the brief insight at that index is unrelated to them, so they skip the
    section rather than claim a foreign insight as their source."""
    if prd.get("theme_id"):
        return None
    from app.db.briefs import get_brief_by_id

    brief = get_brief_by_id(prd["brief_id"])
    if not brief:
        return None
    # get_brief_by_id explodes payload keys to the top level of the dict.
    insights = brief.get("insights") or []
    idx = prd.get("insight_index")
    if not isinstance(idx, int) or not (0 <= idx < len(insights)):
        return None
    insight = insights[idx] or {}
    if not isinstance(insight, dict):
        return None
    title = insight.get("title") or f"Insight #{idx + 1}"
    body = insight.get("body") or insight.get("description") or insight.get("summary") or ""
    section = f"## Source insight (the finding this PRD was generated from)\n{title}"
    if body:
        section += f"\n{_cap(str(body), _INSIGHT_CAP)}"
    return section


def _evidence_section(prd: dict) -> str | None:
    # Evidence rows are keyed (brief_id, insight_index); for a themed PRD that
    # key points at a brief insight's evidence, not this PRD's — skip it.
    if prd.get("theme_id"):
        return None
    from app.db.evidences import find_latest_evidence

    row = find_latest_evidence(prd["brief_id"], prd["insight_index"])
    if not row or row.get("status") != "ready":
        return None
    body = _strip_noise(row.get("payload_md") or "")
    if not body:
        return None
    title = row.get("title") or "(untitled)"
    return (
        "## Evidence (the research behind this PRD)\n"
        f"Title: {title}\n{_cap(body, _EVIDENCE_CAP)}"
    )


def _tickets_section(enterprise_id: str, prd_id: int) -> str | None:
    from app.db.prd_tickets import get_tickets

    row = get_tickets(enterprise_id, prd_id)
    stories = (row or {}).get("stories") or []
    if not stories:
        return None
    lines = [f"## Tickets generated from this PRD ({len(stories)})"]
    for story in stories:
        if not isinstance(story, dict):
            continue
        title = story.get("title") or "(untitled)"
        body = " ".join(str(story.get("body") or "").split())
        if len(body) > 300:
            body = body[:300] + "…"
        ac = story.get("acceptance_criteria") or []
        line = f"- {title}"
        if body:
            line += f" — {body}"
        if ac:
            line += f" (acceptance criteria: {len(ac)})"
        lines.append(line)
    return _cap("\n".join(lines), _TICKETS_CAP)


def _prototype_section(enterprise_id: str, prd_id: int) -> str | None:
    from app.db.prototypes import find_prototype_by_prd

    row = find_prototype_by_prd(
        prd_id=prd_id, workspace_id=enterprise_id, statuses=None
    )
    if not row:
        return None
    parts = [f"status: {row.get('status') or 'unknown'}"]
    if row.get("target_platform"):
        parts.append(f"platform: {row['target_platform']}")
    if row.get("bundle_url"):
        parts.append(f"url: {row['bundle_url']}")
    return "## Prototype built from this PRD\n" + ", ".join(parts)


def build_prd_context(enterprise_id: str | None, prd_id: int | None) -> str:
    """Render the full PRD context block for a PRD-tab ask, or '' when it can't
    be built (no tenant/prd, foreign tenant, or any read failure).

    Re-runs the ownership gate even though the route already gated the request
    — this function feeds tenant data into an LLM prompt, so it must be safe
    to call with an arbitrary (enterprise_id, prd_id) pair on its own.
    """
    if not enterprise_id or not prd_id:
        return ""
    try:
        from app.db.prds import get_prd_rendered
        from app.deps.ownership import require_owned_prd

        prd = require_owned_prd(prd_id, enterprise_id)
        rendered = get_prd_rendered(prd_id) or prd
        sections: list[str] = [_HEADER, _prd_section(prd, rendered)]
        for build in (
            lambda: _format_section(enterprise_id, prd),
            lambda: _insight_section(prd),
            lambda: _evidence_section(prd),
            lambda: _tickets_section(enterprise_id, prd_id),
            lambda: _prototype_section(enterprise_id, prd_id),
        ):
            try:
                section = build()
            except Exception:  # noqa: BLE001 — one missing artifact must not drop the rest
                logger.exception(
                    "prd context section failed for prd_id=%s", prd_id
                )
                continue
            if section:
                sections.append(section)
        return "\n\n".join(sections)
    except Exception:  # noqa: BLE001 — grounding must never break the answer
        logger.exception(
            "prd context unavailable for enterprise=%s prd_id=%s",
            enterprise_id,
            prd_id,
        )
        return ""
