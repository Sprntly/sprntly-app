"""Per-turn clamp applied where conversation history is folded into a prompt.

The failure this exists to prevent: an HTML report answer (VoC, public-feedback,
DS analysis) is persisted verbatim as a conversation turn and replayed into every
later prompt in that thread. Carrying its base64 charts along is hundreds of
thousands of tokens — a non-retryable 400 on every subsequent ask.
"""
from __future__ import annotations

import re

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


def _discover_history_renderers():
    """Every `_render_history*` under app/, found by scanning the source tree.

    Deliberately discovery-based rather than a hardcoded list. Each chat
    intercept keeps its OWN renderer and new intercepts keep arriving — the list
    this replaces was dutifully extended for `connector_lookup.answer` and still
    missed `ticket_update`, which shipped unclamped (found 2026-07-30). A list
    only covers the sites someone remembered; a scan fails loudly on the next one.
    """
    import importlib
    import inspect
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    found = []
    for path in sorted(app_dir.rglob("*.py")):
        source = path.read_text(encoding="utf-8", errors="ignore")
        names = re.findall(r"^def (_render_history\w*)\(", source, re.M)
        if not names:
            continue
        rel = path.relative_to(app_dir).with_suffix("")
        module_name = "app." + str(rel).replace("/", ".")
        if module_name.endswith(".__init__"):
            module_name = module_name[: -len(".__init__")]
        module = importlib.import_module(module_name)
        for name in names:
            fn = getattr(module, name, None)
            # Single-argument renderers only: the history-folding shape.
            if fn and len(inspect.signature(fn).parameters) == 1:
                found.append((f"{module_name}.{name}", fn))
    return found


def test_discovery_finds_the_known_fold_sites():
    """Guard the guard — a scan that silently matched nothing would make the
    test below pass vacuously."""
    names = [name for name, _ in _discover_history_renderers()]
    assert len(names) >= 7, names
    for expected in ("qa_agent", "claude_analysis", "ticket_update", "call_digest"):
        assert any(expected in n for n in names), f"{expected} not discovered: {names}"


def test_every_history_fold_site_is_bounded():
    """No fold site may replay a chart-bearing report at full size.

    The invariant is BYTES, not the literal absence of "base64": chat_intent
    truncates each turn to a char budget instead of stripping data URIs, which is
    equally safe. So assert what actually prevents the 400 — the rendered block
    stays small, and no single base64 run survives at a size that could matter.
    """
    history = [{"role": "assistant", "content": _REPORT}]
    renderers = _discover_history_renderers()
    assert renderers

    for name, render in renderers:
        out = render(history)
        assert len(out) < 12_000, f"{name} folds an unbounded turn ({len(out)} chars)"
        longest = max((len(m) for m in re.findall(r"[A-Za-z0-9+/=]{40,}", out)), default=0)
        assert longest < 500, f"{name} replays a {longest}-char base64 run"


def test_qa_agent_render_history_bounds_many_fat_turns():
    """Twelve report-sized turns must still fold into a sane prompt block.

    The bound is the CHAR budget now that the turn cap is gone. That budget is
    the old worst case (6 turns x MAX_TURN_CHARS), so uncapping the turn count
    did not raise the ceiling on what this block can cost — it only changed
    which turns get to sit under it."""
    import app.qa_agent as qa

    history = [{"role": "assistant", "content": _REPORT} for _ in range(12)]
    rendered = qa._render_history(history)

    assert "base64" not in rendered
    assert len(rendered) <= qa._HISTORY_CHAR_BUDGET + 500
    assert qa._HISTORY_CHAR_BUDGET == 6 * MAX_TURN_CHARS


# ─── whole-conversation folding: compact, never silently drop ────────────────


def _turns(n: int, *, chars: int) -> list[dict]:
    """`n` distinguishable turns, each `chars` long once clamped."""
    return [
        {"role": "user", "content": f"turn-{i:03d} " + "z" * chars}
        for i in range(n)
    ]


def test_short_thread_is_byte_identical_to_the_old_renderer():
    """The common case must not move. This is the exact string the pre-uncap
    last-N/newest-first renderer produced for a small thread, written out by
    hand rather than derived from the implementation under test."""
    from app.prompt_history import render_history_block

    history = [
        {"role": "user", "content": "Users keep asking for CSV export."},
        {"role": "assistant", "content": "14 requests this quarter."},
        {"role": "user", "content": "Cap it at 50k rows."},
    ]

    assert render_history_block(history) == (
        "Conversation so far:\n"
        "User: Users keep asking for CSV export.\n"
        "Assistant: 14 requests this quarter.\n"
        "User: Cap it at 50k rows.\n"
        "\n"
    )


def test_thread_past_the_old_turn_cap_still_fits_whole():
    """40 short turns blow the OLD 6-turn window but not the byte budget, so
    every one of them survives — no elision, nothing dropped. This is the case
    the turn cap was silently destroying."""
    from app.prompt_history import render_history_block

    rendered = render_history_block(_turns(40, chars=100))

    for i in range(40):
        assert f"turn-{i:03d}" in rendered
    assert "omitted" not in rendered


def test_long_thread_keeps_head_and_tail_and_marks_the_elision():
    """The deictic case: the earliest topic turn AND the newest turns both
    survive a thread far past the budget, and the gap between them is declared
    in-band rather than left for the model to misread as continuity."""
    from app.prompt_history import render_history_block

    rows = _turns(60, chars=1_000)
    rendered = render_history_block(rows, turn_chars=1_500, char_budget=24_000)

    assert "turn-000" in rendered, "earliest turn (the topic) must survive"
    assert "turn-059" in rendered, "newest turn must survive"
    assert "turn-030" not in rendered, "the middle is what gets elided"

    marker = [ln for ln in rendered.splitlines() if ln.startswith("[...")]
    assert len(marker) == 1, rendered
    assert "the middle is NOT" in marker[0]

    # The marker states the true count, and the numbers reconcile.
    dropped = int(re.search(r"\[\.\.\. (\d+) earlier turns", marker[0]).group(1))
    kept = len([ln for ln in rendered.splitlines() if ln.startswith("User: ")])
    assert dropped + kept == 60


def test_the_newest_turn_is_never_dropped():
    """Invariant guard: even a budget too small for one clamped turn keeps the
    last one, because a history block whose newest turn vanished is the worst
    possible outcome of a function that exists to preserve context."""
    from app.prompt_history import compact_rows

    body = compact_rows(["A: " + "x" * 5_000, "B: newest"], char_budget=10)

    assert body[-1] == "B: newest"


def test_head_and_tail_never_overlap():
    """A turn must not be rendered twice across the elision — that would let the
    model read one exchange as two and double-count a requirement."""
    from app.prompt_history import compact_rows

    rows = [f"User: turn-{i:03d} " + "z" * 900 for i in range(60)]
    body = compact_rows(rows, char_budget=24_000)

    kept = [ln for ln in body if not ln.startswith("[...")]
    assert len(kept) == len(set(kept))


def test_empty_and_malformed_turns_are_skipped_not_rendered():
    """An empty turn carries nothing; a bare "User: " line is prompt noise. A
    None role falls back to `user` rather than raising."""
    from app.prompt_history import render_history_block

    rendered = render_history_block(
        [
            {"role": "user", "content": ""},
            {"role": None, "content": "kept"},
            "not-a-dict",
            {"role": "assistant", "content": None},
        ]
    )

    assert rendered == "Conversation so far:\nUser: kept\n\n"


def test_no_turns_at_all_renders_nothing():
    from app.prompt_history import render_history_block

    for empty in ([], None, "", [{"role": "user", "content": ""}]):
        assert render_history_block(empty) == ""
