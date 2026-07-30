"""Per-turn clamp applied where conversation history is folded into a prompt.

The failure this exists to prevent: an HTML report answer (VoC, public-feedback,
DS analysis) is persisted verbatim as a conversation turn and replayed into every
later prompt in that thread. Carrying its base64 charts along is hundreds of
thousands of tokens — a non-retryable 400 on every subsequent ask.
"""
from __future__ import annotations

from app.prompt_history import (
    MAX_TURN_CHARS,
    clamp_turn_text,
    html_to_text,
    looks_like_html,
    strip_data_uris,
)

_CHART = "A" * 200_000
_REPORT = (
    '<!doctype html><html><head><style>body{color:red}</style></head><body>'
    '<div class="page"><h1>What your data shows</h1>'
    "<h2>Export users retain 2.3x longer</h2><p>The gap is 34pp.</p>"
    f'<figure><img alt="Analysis chart" src="data:image/png;base64,{_CHART}"></figure>'
    "<p><strong>TL;DR</strong> Ship the export nudge.</p>"
    "</div></body></html>"
)


def test_data_uris_are_stripped():
    out = strip_data_uris(f'<img src="data:image/png;base64,{_CHART}">')
    assert "base64" not in out and _CHART[:50] not in out
    assert "[embedded image omitted]" in out


def test_jpeg_and_svg_data_uris_are_stripped_too():
    for mime in ("image/jpeg", "image/svg+xml", "application/pdf"):
        out = strip_data_uris(f"data:{mime};base64,{'B' * 5000}")
        assert "BBBB" not in out


def test_data_uri_strip_stops_at_the_payload():
    """Reported by review: `\s` in the base64 class walked past the payload and
    ate the prose after it — "…;base64,AAAA\n\nExport users retain 2.3x longer"
    became "[embedded image omitted].3x longer", deleting the narrative the clamp
    exists to keep."""
    out = strip_data_uris(
        "src=data:image/png;base64,AAAA\n\nExport users retain 2.3x longer."
    )
    assert "[embedded image omitted]" in out
    assert "Export users retain 2.3x longer." in out
    assert "AAAA" not in out


def test_html_report_is_reduced_to_its_narrative():
    assert looks_like_html(_REPORT)
    text = html_to_text(_REPORT)
    assert "Export users retain 2.3x longer" in text
    assert "The gap is 34pp." in text
    assert "TL;DR" in text
    # chrome the model never wrote and the reader never sees
    assert "color:red" not in text and "<h2>" not in text


def test_clamp_makes_a_chart_report_prompt_safe():
    out = clamp_turn_text(_REPORT)

    assert "base64" not in out, "the megabyte must not survive into a prompt"
    assert len(out) <= MAX_TURN_CHARS + 40
    assert "Export users retain 2.3x longer" in out, "narrative context survives"


def test_clamp_truncates_a_long_plain_turn():
    out = clamp_turn_text("word " * 5000)
    assert len(out) <= MAX_TURN_CHARS + 40
    assert out.endswith("[earlier turn truncated]")


def test_clamp_leaves_a_normal_markdown_turn_alone():
    turn = "## Findings\n\n- churn is 4.2%\n- pro plan retains best\n"
    assert clamp_turn_text(turn) == turn.strip()


def test_clamp_tolerates_non_strings():
    for value in (None, 123, {"a": 1}, [], ""):
        assert clamp_turn_text(value) == ""


def test_qa_agent_render_history_clamps_every_turn():
    """The generic fold — used by the haiku router AND every answer call."""
    import app.qa_agent as qa

    rendered = qa._render_history(
        [
            {"role": "user", "content": "analyze my data"},
            {"role": "assistant", "content": _REPORT},
            {"role": "user", "content": "and by plan?"},
        ]
    )

    assert "base64" not in rendered
    assert len(rendered) < 12_000
    assert "and by plan?" in rendered
    assert "Export users retain 2.3x longer" in rendered


def test_every_history_fold_site_is_clamped():
    """Each intercept keeps its own `_render_history`; all of them fold raw
    assistant turns, so all of them are exposed to a chart-bearing report.
    (chat_intent is excluded: it already has its own per-turn + total clamp.)

    `connector_lookup.answer._render_history` is the shared fold for EVERY
    connector adapter (Jira via the jira_lookup shim, Slack, ClickUp, Fireflies,
    GitHub, HubSpot, Drive), so one entry here covers all of them — and covers
    adapters added later, which is the point of folding in one place.
    `jira_lookup._render_history` stays listed because it is a public seam other
    callers use; it delegates to the shared renderer."""
    from app import call_digest, jira_lookup, public_feedback
    from app.connector_lookup import answer as connector_answer
    import app.ds.claude_analysis as claude_analysis
    import app.qa_agent as qa

    history = [{"role": "assistant", "content": _REPORT}]
    renderers = [
        qa._render_history,
        claude_analysis._render_history,
        call_digest._render_history,
        call_digest._render_history_tail,
        jira_lookup._render_history,
        connector_answer._render_history,
        public_feedback._render_history,
    ]
    for render in renderers:
        out = render(history)
        assert "base64" not in out, f"{render.__module__}.{render.__name__} folds base64"
        assert len(out) < 12_000, f"{render.__module__}.{render.__name__} is unbounded"


def test_qa_agent_render_history_bounds_many_fat_turns():
    """Six report-sized turns must still fold into a sane prompt block."""
    import app.qa_agent as qa

    history = [{"role": "assistant", "content": _REPORT} for _ in range(12)]
    rendered = qa._render_history(history)

    assert "base64" not in rendered
    assert len(rendered) <= (MAX_TURN_CHARS + 200) * qa._HISTORY_TURNS
