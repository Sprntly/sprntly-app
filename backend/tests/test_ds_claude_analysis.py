"""Chat data analysis via Claude + server-side code execution (app.ds.claude_analysis).

Hermetic: a fake Anthropic client (the `llm.get_client` patch pattern used by
test_llm_streaming / agent_chat) scripts the `messages.stream` →
`get_final_message()` protocol, the Files API upload/download/delete calls, and
the `pause_turn` resume handshake. No network, no DB — the dataset lookup and the
decision log are patched, exactly as in test_ds_chat_analysis.

Covers the plan's test groups: flag gating + fallback (1), happy path with an
embedded chart (2), malformed data / failing executions (3), oversized + empty
staging (4, 5), turn / pause / wall-clock caps (6, 10), cancellation (7),
container tenancy + file cleanup (8), routing negatives (9), and usage
accumulation + fail-closed model override (11).
"""
from __future__ import annotations

import base64
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import llm
import app.ds.claude_analysis as ca
import app.ds.chat_analysis as legacy
from app.qa_agent import AskCancelled
from app.skill_router import is_data_analysis_request

COMPANY = "00000000-0000-0000-0000-000000000001"
OTHER_COMPANY = "00000000-0000-0000-0000-0000000000ff"
PNG = b"\x89PNG\r\n\x1a\n" + b"pretend-pixels"


# ── fake Anthropic client ────────────────────────────────────────────────────

def _usage(inp: int = 10, out: int = 20) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=inp,
        output_tokens=out,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=5,
    )


def _text(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _exec_result(
    *, file_ids: tuple[str, ...] = (), stdout: str = "", stderr: str = "", return_code: int = 0
) -> SimpleNamespace:
    """A code-execution tool-result block, SDK shape."""
    return SimpleNamespace(
        type="bash_code_execution_tool_result",
        tool_use_id="srvtoolu_1",
        content=SimpleNamespace(
            type="bash_code_execution_result",
            stdout=stdout,
            stderr=stderr,
            return_code=return_code,
            content=[
                SimpleNamespace(type="bash_code_execution_output", file_id=fid)
                for fid in file_ids
            ],
        ),
    )


def _exec_error(error_code: str = "unavailable") -> SimpleNamespace:
    return SimpleNamespace(
        type="bash_code_execution_tool_result",
        tool_use_id="srvtoolu_err",
        content=SimpleNamespace(type="bash_code_execution_tool_error_result", error_code=error_code),
    )


def _message(content, *, stop_reason: str = "end_turn", container: str | None = "ctr_1", usage=None):
    return SimpleNamespace(
        content=list(content),
        stop_reason=stop_reason,
        container=SimpleNamespace(id=container) if container else None,
        usage=usage if usage is not None else _usage(),
        model="claude-sonnet-4-6",
    )


class _StreamCtx:
    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._message


class FakeAnthropic:
    """Scripts one message per turn; records every Files API + stream call."""

    def __init__(self, messages, *, download_bytes=PNG, delete_raises=False):
        self._script = list(messages)
        self._download_bytes = download_bytes
        self._delete_raises = delete_raises
        self.stream_kwargs: list[dict] = []
        self.uploaded: list[str] = []
        self.downloaded: list[str] = []
        self.deleted: list[str] = []
        outer = self

        class _Files:
            def upload(self, *, file):
                name = file[0] if isinstance(file, tuple) else getattr(file, "name", "?")
                outer.uploaded.append(name)
                return SimpleNamespace(id=f"file_up_{len(outer.uploaded)}")

            def download(self, file_id):
                outer.downloaded.append(file_id)
                return SimpleNamespace(read=lambda: outer._download_bytes)

            def delete(self, file_id):
                if outer._delete_raises:
                    raise RuntimeError("delete boom")
                outer.deleted.append(file_id)

        class _Messages:
            def stream(self, **kwargs):
                outer.stream_kwargs.append(kwargs)
                if not outer._script:
                    raise AssertionError("model called more times than scripted")
                return _StreamCtx(outer._script.pop(0))

        self.beta = SimpleNamespace(files=_Files())
        self.messages = _Messages()

    @property
    def turns(self) -> int:
        return len(self.stream_kwargs)


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """Point the analysis at a temp dataset raw/ dir for COMPANY."""
    raw = tmp_path / "acme" / "raw"
    raw.mkdir(parents=True)
    monkeypatch.setattr("app.db.companies.slug_for_company_id", lambda cid: "acme")
    monkeypatch.setattr("app.datasets.raw_path", lambda slug: tmp_path / slug / "raw")
    return raw


@pytest.fixture()
def logged(monkeypatch):
    """Capture decision-log rows instead of writing to Supabase."""
    rows: list[dict] = []
    monkeypatch.setattr(
        "app.graph.decision_log.log_agent_decision",
        lambda **kw: rows.append(kw),
    )
    return rows


def _client(monkeypatch, *messages, **kw) -> FakeAnthropic:
    fake = FakeAnthropic(messages, **kw)
    monkeypatch.setattr(llm, "get_client", lambda: fake)
    return fake


def _csv(raw: Path, name: str = "users.csv", body: str | None = None) -> None:
    (raw / name).write_text(
        body
        if body is not None
        else "user_id,plan,retained\nu1,pro,1\nu2,free,0\nu3,pro,1\n"
    )


# ── 2. happy path ────────────────────────────────────────────────────────────

def test_happy_path_renders_html_report_with_embedded_chart(workspace, logged, monkeypatch):
    _csv(workspace)
    _csv(workspace, "events.csv", "user_id,event\nu1,export\n")
    fake = _client(
        monkeypatch,
        _message([
            _text("## Export users retain 2.3x longer\nThe gap is 34pp."),
            _exec_result(file_ids=("file_chart_1",), stdout="0.34\n"),
            _text("**TL;DR** Ship the export nudge — HIGH confidence."),
        ]),
    )

    out = ca.answer(enterprise_id=COMPANY, question="which cohort retains best?")

    # Ask-payload contract + engine attribution
    for field in ("answer", "key_points", "citations", "confidence", "unanswered"):
        assert field in out
    assert out["_skill"] == "ds-agent"
    assert out["_skill_source"] == "ds-claude"

    # HTML report on the iframe path (looksLikeHtmlBrief sniffs a leading doctype)
    answer = out["answer"]
    assert answer.lstrip().lower().startswith("<!doctype html")
    assert "Export users retain 2.3x longer" in answer
    assert "<h2>" in answer and "<strong>TL;DR</strong>" in answer
    # the chart Claude saved, embedded as base64 PNG in the order it was produced
    assert f'src="data:image/png;base64,{base64.b64encode(PNG).decode()}"' in answer
    assert answer.index("Export users retain") < answer.index("data:image/png")

    # one upload per staged CSV, exactly once each
    assert sorted(fake.uploaded) == ["events.csv", "users.csv"]
    assert fake.downloaded == ["file_chart_1"]
    # the question rides the user turn (the v5.8 engine never reads it)
    first_text = fake.stream_kwargs[0]["messages"][0]["content"][0]["text"]
    assert "which cohort retains best?" in first_text
    # each file attached as a container_upload block
    uploads = [b for b in fake.stream_kwargs[0]["messages"][0]["content"]
               if b.get("type") == "container_upload"]
    assert len(uploads) == 2
    assert fake.stream_kwargs[0]["tools"] == [
        {"type": "code_execution_20260120", "name": "code_execution"}
    ]
    assert fake.stream_kwargs[0]["extra_headers"] == {
        "anthropic-beta": "files-api-2025-04-14"
    }

    # decision-log row with model + accumulated usage
    assert len(logged) == 1
    row = logged[0]
    assert row["agent"] == "ds"
    assert row["decision_type"] == "chat_data_analysis_claude"
    assert row["model"] == "claude-sonnet-4-6"
    assert row["factors"]["files"] == 2
    assert row["factors"]["charts_rendered"] == 1
    assert row["factors"]["charts_dropped"] == 0
    assert row["factors"]["turns"] == 1
    assert row["factors"]["input_tokens"] == 10
    assert row["factors"]["output_tokens"] == 20


def test_history_is_folded_into_the_question(workspace, logged, monkeypatch):
    _csv(workspace)
    fake = _client(monkeypatch, _message([_text("done")]))

    ca.answer(
        enterprise_id=COMPANY,
        question="and by plan?",
        history=[{"role": "user", "content": "how is retention?"}],
    )

    prompt = fake.stream_kwargs[0]["messages"][0]["content"][0]["text"]
    assert "how is retention?" in prompt and "and by plan?" in prompt


def test_html_report_escapes_model_markup(workspace, logged, monkeypatch):
    """The report renders in a script-less iframe, but the model never gets to
    write markup regardless — everything it emits is escaped."""
    _csv(workspace)
    _client(monkeypatch, _message([_text("<script>alert(1)</script> findings")]))

    out = ca.answer(enterprise_id=COMPANY, question="analyze my data")

    assert "<script>alert(1)</script>" not in out["answer"]
    assert "&lt;script&gt;" in out["answer"]


# ── 3. malformed data / failing executions ───────────────────────────────────

def test_malformed_csv_is_still_uploaded_and_execution_errors_dont_abort(
    workspace, logged, monkeypatch
):
    """A broken export is Claude's problem to notice, not ours to pre-screen: it
    uploads, and a non-zero return_code / stderr / tool error keeps the loop
    going so the model can correct itself."""
    _csv(workspace, "broken.csv", 'a,b\n1,"unterminated\n' * 5)
    fake = _client(
        monkeypatch,
        _message(
            [
                _text("First parse attempt failed."),
                _exec_result(stderr="ParserError: EOF inside string", return_code=1),
            ],
            stop_reason="pause_turn",
        ),
        _message([
            _exec_result(file_ids=("file_chart_1",), stdout="recovered"),
            _text("Recovered with on_bad_lines='skip'. 4 usable rows."),
        ]),
    )

    out = ca.answer(enterprise_id=COMPANY, question="analyze my data")

    assert fake.uploaded == ["broken.csv"]
    assert fake.turns == 2, "a failed execution must not end the analysis"
    assert "Recovered with" in out["answer"]
    assert logged[0]["factors"]["pause_resumes"] == 1


def test_tool_error_result_yields_no_chart(workspace, logged, monkeypatch):
    _csv(workspace)
    _client(monkeypatch, _message([_exec_error(), _text("Sandbox was unavailable; retried.")]))

    out = ca.answer(enterprise_id=COMPANY, question="analyze my data")

    assert logged[0]["factors"]["charts_rendered"] == 0
    assert "data:image/png" not in out["answer"]


def test_non_png_and_oversized_outputs_are_not_embedded(workspace, logged, monkeypatch):
    """Claude writes CSVs to $OUTPUT_DIR too — only real PNGs get embedded."""
    _csv(workspace)
    _client(
        monkeypatch,
        _message([_exec_result(file_ids=("file_csv_out",)), _text("Wrote a summary CSV.")]),
        download_bytes=b"a,b\n1,2\n",
    )

    out = ca.answer(enterprise_id=COMPANY, question="analyze my data")

    assert "data:image/png" not in out["answer"]
    assert "Wrote a summary CSV." in out["answer"]


# ── 4 & 5. nothing to analyze (no API call at all) ───────────────────────────

def test_no_dataset_dir_makes_no_api_call(monkeypatch, logged):
    monkeypatch.setattr("app.db.companies.slug_for_company_id", lambda cid: None)
    fake = _client(monkeypatch)

    out = ca.answer(enterprise_id=COMPANY, question="analyze my data")

    assert "don't see any uploaded data" in out["answer"]
    assert out["confidence"] == 0.0
    assert out["_skill"] == "ds-agent"
    assert fake.turns == 0 and fake.uploaded == []


def test_no_tabular_files_makes_no_api_call(workspace, monkeypatch, logged):
    (workspace / "notes.pdf").write_bytes(b"%PDF-1.4 not a table")
    fake = _client(monkeypatch)

    out = ca.answer(enterprise_id=COMPANY, question="analyze my data")

    assert "none of your uploaded files are tabular" in out["answer"]
    assert fake.turns == 0 and fake.uploaded == []


def test_oversized_file_is_skipped(workspace, monkeypatch, logged):
    """>20MB never came through the upload route; skip it rather than choke —
    and with nothing else stageable there is no analysis to run."""
    big = workspace / "huge.csv"
    big.write_bytes(b"a,b\n" + b"1,2\n" * 10)
    monkeypatch.setattr("app.ds.staging.MAX_FILE_BYTES", 8)
    fake = _client(monkeypatch)

    out = ca.answer(enterprise_id=COMPANY, question="analyze my data")

    assert "none of your uploaded files are tabular" in out["answer"]
    assert fake.uploaded == []


def test_empty_csv_still_runs_and_degrades_gracefully(workspace, logged, monkeypatch):
    (workspace / "empty.csv").write_text("")
    _client(
        monkeypatch,
        _message([_text("The uploaded file is empty — 0 rows. Export it again with data.")]),
    )

    out = ca.answer(enterprise_id=COMPANY, question="analyze my data")

    assert "0 rows" in out["answer"]
    assert out["confidence"] == 0.8


def test_empty_analysis_raises_so_the_caller_can_fall_back(workspace, logged, monkeypatch):
    """No narrative at all is a failed run, not an empty report — raising is what
    hands the ask to the deterministic engine."""
    _csv(workspace)
    _client(monkeypatch, _message([_exec_result(stdout="silent")]))

    with pytest.raises(ca.DsClaudeAnalysisError):
        ca.answer(enterprise_id=COMPANY, question="analyze my data")


def test_upload_failure_for_every_file_raises(workspace, logged, monkeypatch):
    _csv(workspace)
    fake = FakeAnthropic([])

    def _boom(*, file):  # noqa: ARG001
        raise RuntimeError("upload boom")

    fake.beta.files.upload = _boom
    monkeypatch.setattr(llm, "get_client", lambda: fake)

    with pytest.raises(ca.DsClaudeAnalysisError):
        ca.answer(enterprise_id=COMPANY, question="analyze my data")
    assert fake.turns == 0


# ── 6 & 10. caps ─────────────────────────────────────────────────────────────

def test_pause_turn_resumes_are_capped_at_five(workspace, logged, monkeypatch):
    """`_MAX_PAUSE_RESUMES` means 5 RESUMES — 6 model calls — not 5 calls."""
    _csv(workspace)
    fake = _client(
        monkeypatch,
        *[
            _message([_text(f"step {i}")], stop_reason="pause_turn")
            for i in range(ca._MAX_PAUSE_RESUMES + 1)
        ],
    )

    out = ca.answer(enterprise_id=COMPANY, question="analyze my data")

    assert fake.turns == ca._MAX_PAUSE_RESUMES + 1 == 6
    assert logged[0]["factors"]["pause_resumes"] == ca._MAX_PAUSE_RESUMES == 5
    assert "step 0" in out["answer"] and "step 5" in out["answer"]
    assert "reached my limit" in out["answer"]
    assert logged[0]["factors"]["stopped_reason"] == "pause_cap"


def test_runaway_loop_stops_at_the_turn_cap(workspace, logged, monkeypatch):
    _csv(workspace)
    monkeypatch.setattr(ca, "_MAX_PAUSE_RESUMES", 999)
    fake = _client(
        monkeypatch,
        *[_message([_text(f"step {i}")], stop_reason="pause_turn") for i in range(20)],
    )

    out = ca.answer(enterprise_id=COMPANY, question="analyze my data")

    assert fake.turns == ca._MAX_TURNS == 12
    assert "reached my limit" in out["answer"]
    assert logged[0]["factors"]["stopped_reason"] == "turn_cap"


def test_wall_clock_budget_wraps_up(workspace, logged, monkeypatch):
    _csv(workspace)
    monkeypatch.setattr(ca, "_WALL_CLOCK_BUDGET_S", 0.0)
    fake = _client(
        monkeypatch,
        _message([_text("partial findings")], stop_reason="pause_turn"),
        _message([_text("never reached")]),
    )

    out = ca.answer(enterprise_id=COMPANY, question="analyze my data")

    assert fake.turns == 1, "budget is checked before spending the next turn"
    assert "partial findings" in out["answer"]
    assert "keep the response timely" in out["answer"]
    assert logged[0]["factors"]["stopped_reason"] == "timeout"


# ── 7. cancellation ─────────────────────────────────────────────────────────

def test_cancellation_mid_loop_raises_before_the_next_call(workspace, logged, monkeypatch):
    _csv(workspace)
    fake = _client(
        monkeypatch,
        _message([_text("turn one")], stop_reason="pause_turn"),
        _message([_text("must not run")]),
    )
    calls = {"n": 0}

    def is_cancelled() -> bool:
        calls["n"] += 1
        return calls["n"] > 2  # pre-upload + turn 1 proceed; turn 2 is cancelled

    with pytest.raises(AskCancelled):
        ca.answer(enterprise_id=COMPANY, question="analyze my data", is_cancelled=is_cancelled)

    assert fake.turns == 1, "the second (expensive) turn was never spent"
    # cancellation still cleans up the tenant's data in the Files API
    assert fake.deleted


def test_cancellation_before_upload_makes_no_api_call(workspace, logged, monkeypatch):
    _csv(workspace)
    fake = _client(monkeypatch)

    with pytest.raises(AskCancelled):
        ca.answer(enterprise_id=COMPANY, question="analyze my data", is_cancelled=lambda: True)

    assert fake.turns == 0 and fake.uploaded == []


# ── 8. container tenancy + cleanup ───────────────────────────────────────────

def test_container_scope_keys_never_collide_across_tenants_or_asks():
    a1 = ca.container_scope_key(COMPANY, "run-1")
    a2 = ca.container_scope_key(COMPANY, "run-2")
    b1 = ca.container_scope_key(OTHER_COMPANY, "run-1")
    assert a1 != a2 != b1 and a1 != b1


def test_container_is_only_replayed_under_its_own_scope_key():
    scope = ca.ContainerScope(key=ca.container_scope_key(COMPANY, "run-1"))
    scope.capture(SimpleNamespace(container=SimpleNamespace(id="ctr_abc")))

    assert scope.resume_kwargs(scope.key) == {"container": "ctr_abc"}
    # another tenant's / another ask's key gets nothing — no cross-ask leakage
    assert scope.resume_kwargs(ca.container_scope_key(OTHER_COMPANY, "run-1")) == {}
    assert scope.resume_kwargs(ca.container_scope_key(COMPANY, "run-2")) == {}


def test_fresh_container_per_ask_and_reuse_within_one_ask(workspace, logged, monkeypatch):
    _csv(workspace)
    fake = _client(
        monkeypatch,
        _message([_text("one")], stop_reason="pause_turn", container="ctr_run_a"),
        _message([_text("two")], container="ctr_run_a"),
    )

    ca.answer(enterprise_id=COMPANY, question="analyze my data")

    # first turn asks for a NEW container; the resume rides the same one
    assert "container" not in fake.stream_kwargs[0]
    assert fake.stream_kwargs[1]["container"] == "ctr_run_a"

    # a second ask starts clean — no container carried over
    fake2 = _client(monkeypatch, _message([_text("fresh")]))
    ca.answer(enterprise_id=COMPANY, question="analyze my data again")
    assert "container" not in fake2.stream_kwargs[0]


def test_uploads_and_charts_are_deleted_after_render(workspace, logged, monkeypatch):
    _csv(workspace)
    fake = _client(
        monkeypatch,
        _message([_exec_result(file_ids=("file_chart_1",)), _text("findings")]),
    )

    out = ca.answer(enterprise_id=COMPANY, question="analyze my data")

    assert fake.deleted == ["file_up_1", "file_chart_1"]
    # deleted only AFTER the chart was downloaded and embedded
    assert "data:image/png" in out["answer"]


def test_cleanup_failure_is_swallowed(workspace, logged, monkeypatch):
    _csv(workspace)
    _client(monkeypatch, _message([_text("findings")]), delete_raises=True)

    out = ca.answer(enterprise_id=COMPANY, question="analyze my data")

    assert "findings" in out["answer"], "a failed delete must not fail the analysis"


# ── history safety: a chart-bearing report must not poison the thread ────────
# The answer is persisted verbatim as a conversation turn (web chatPersistence)
# and replayed into EVERY later prompt in that thread — routes/ask._load_history
# caps nothing, and qa_agent._render_history folds it into both the router and
# the answer call. Uncapped base64 charts there are a non-retryable 400 on every
# subsequent ask, so both ends are pinned: the embed budget and the fold clamp.

def _chart_report(
    workspace, monkeypatch, logged, *, n_charts: int = 3, download_bytes: bytes = PNG
) -> str:
    _csv(workspace)
    blocks: list = [_text("## Findings\nCharts follow.")]
    for i in range(n_charts):
        blocks.append(_exec_result(file_ids=(f"file_chart_{i}",)))
    blocks.append(_text("**TL;DR** all good."))
    _client(monkeypatch, _message(blocks), download_bytes=download_bytes)
    return ca.answer(enterprise_id=COMPANY, question="analyze my data")["answer"]


def test_embedded_charts_stay_within_the_history_safe_budget(workspace, logged, monkeypatch):
    """Three individually-legal 40KB charts would be ~160KB of base64; the TOTAL
    budget is what stops the report from becoming a thread-poisoning turn."""
    answer = _chart_report(
        workspace, monkeypatch, logged, download_bytes=PNG + b"\x00" * 40_000
    )

    embedded = re.findall(r"base64,([A-Za-z0-9+/=]+)", answer)
    b64_bytes = sum(len(m) for m in embedded)
    assert embedded, "in-budget charts must still be embedded"
    assert b64_bytes <= ca._MAX_TOTAL_CHART_B64_BYTES, (
        f"embedded charts total {b64_bytes} bytes — a report this size is replayed "
        "into every later prompt in the thread"
    )
    assert answer.count("<figure>") < 3, "the total budget must have stopped one"
    assert "TL;DR" in answer, "the narrative is never dropped for budget"


def test_chart_count_is_capped(workspace, logged, monkeypatch):
    answer = _chart_report(workspace, monkeypatch, logged, n_charts=8)
    assert answer.count("<figure>") == ca._MAX_CHARTS == 3


def test_oversized_unshrinkable_chart_is_dropped(workspace, logged, monkeypatch):
    """Padding bytes aren't a decodable image, so recompression can't help — the
    chart is dropped rather than embedded over budget."""
    _csv(workspace)
    _client(
        monkeypatch,
        _message([_exec_result(file_ids=("file_chart_1",)), _text("findings")]),
        download_bytes=PNG + b"\x00" * 200_000,
    )

    out = ca.answer(enterprise_id=COMPANY, question="analyze my data")

    assert "findings" in out["answer"]
    assert "base64," not in out["answer"]


def _oversized_png(seed: int = 7) -> bytes:
    """A decodable PNG over the per-chart budget: 4-colour blocky texture.

    Deliberately noisy — re-encoding it can INFLATE it, which is exactly the case
    `_shrink_png` has to not make worse.
    """
    import io
    import random

    from PIL import Image

    rng = random.Random(seed)
    palette = [(255, 255, 255), (31, 111, 82), (187, 70, 60), (64, 64, 64)]
    img = Image.new("RGB", (1600, 900), "white")
    px = img.load()
    for by in range(0, 900, 4):
        for bx in range(0, 1600, 4):
            colour = palette[rng.randrange(len(palette))]
            for y in range(by, min(by + 4, 900)):
                for x in range(bx, min(bx + 4, 1600)):
                    px[x, y] = colour
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_shrink_png_never_inflates_a_chart(monkeypatch):
    """Recompression must never hand back MORE bytes than it was given — a resize
    interpolates new colours and JPEG loses to PNG on high-frequency content, so
    the naive "PNG then JPEG" ladder can grow the file.

    Pillow ships in the deployed image but is not a declared requirement, so this
    skips where it is missing (the caller then simply drops the chart).
    """
    pytest.importorskip("PIL.Image", reason="Pillow not installed")
    raw = _oversized_png()
    assert len(raw) > ca._MAX_CHART_BYTES, "fixture must be over budget"

    out, media_type = ca._shrink_png(raw)

    assert len(out) <= len(raw)
    assert media_type in ("image/png", "image/jpeg")


def test_shrink_png_without_pillow_leaves_the_chart_alone(monkeypatch):
    """No Pillow → return the input untouched so the caller drops it, rather than
    raising into the analysis."""
    import builtins

    real_import = builtins.__import__

    def _no_pil(name, *a, **kw):
        if name.startswith("PIL"):
            raise ImportError("no Pillow here")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _no_pil)
    raw = PNG + b"\x00" * 100_000

    assert ca._shrink_png(raw) == (raw, "image/png")


def test_followup_ask_folds_a_chart_report_into_a_small_history(
    workspace, logged, monkeypatch
):
    """The whole point: after a chart-bearing report, the NEXT ask's prompt must
    not carry the images back to the model."""
    report_html = _chart_report(workspace, monkeypatch, logged)
    assert "data:image" in report_html, "fixture must actually contain a chart"

    history = [
        {"role": "user", "content": "analyze my data"},
        {"role": "assistant", "content": report_html},
    ]

    # (a) the DS path's own history rendering
    ds_rendered = ca._render_history(history)
    assert "data:image" not in ds_rendered and "base64," not in ds_rendered
    assert len(ds_rendered) < 12_000

    # (b) the generic qa_agent fold — the router + every answer call
    import app.qa_agent as qa

    qa_rendered = qa._render_history(history)
    assert "data:image" not in qa_rendered and "base64," not in qa_rendered
    assert len(qa_rendered) < 12_000
    # the narrative survives the clamp, so follow-ups still have context
    assert "Findings" in qa_rendered

    # (c) end to end: the follow-up's user turn is small and image-free
    _csv(workspace)
    fake = _client(monkeypatch, _message([_text("by plan: pro leads.")]))
    ca.answer(enterprise_id=COMPANY, question="and by plan?", history=history)
    prompt = fake.stream_kwargs[0]["messages"][0]["content"][0]["text"]
    assert "base64," not in prompt
    assert len(prompt) < 12_000, f"follow-up prompt is {len(prompt)} chars"


# ── the decision log must count the same charts the report shows ─────────────
# Staging QA (2026-07-30): header said "2 charts", two rendered, the log said 3.
# The log was counting what the model PRODUCED; the header counts what survived
# the embed budget. Two explicit fields now, so neither number is ambiguous.

def test_decision_log_splits_rendered_from_dropped_charts(workspace, logged, monkeypatch):
    _csv(workspace)
    # Three produced; each ~53KB of base64, so the 100KB total budget embeds two.
    _client(
        monkeypatch,
        _message([
            _exec_result(file_ids=("c1", "c2", "c3")),
            _text("findings"),
        ]),
        download_bytes=PNG + b"\x00" * 40_000,
    )

    out = ca.answer(enterprise_id=COMPANY, question="analyze my data")

    rendered = out["answer"].count("<figure>")
    factors = logged[0]["factors"]
    assert factors["charts_rendered"] == rendered, "log must match what was shown"
    assert factors["charts_dropped"] == 3 - rendered
    assert factors["charts_rendered"] + factors["charts_dropped"] == 3
    # and the header the reader sees agrees with the log
    assert f"{rendered} chart" in out["answer"]


def test_decision_log_reports_zero_dropped_when_all_charts_fit(
    workspace, logged, monkeypatch
):
    _csv(workspace)
    _client(
        monkeypatch,
        _message([_exec_result(file_ids=("c1", "c2")), _text("findings")]),
    )

    out = ca.answer(enterprise_id=COMPANY, question="analyze my data")

    assert logged[0]["factors"]["charts_rendered"] == 2 == out["answer"].count("<figure>")
    assert logged[0]["factors"]["charts_dropped"] == 0


# ── truncation is never rendered as a finished report ────────────────────────

def test_max_tokens_stop_is_flagged_in_the_report(workspace, logged, monkeypatch):
    _csv(workspace)
    _client(
        monkeypatch,
        _message([_text("## Finding one\nRetention is 41% for pro and")],
                 stop_reason="max_tokens"),
    )

    out = ca.answer(enterprise_id=COMPANY, question="analyze my data")

    assert "cut off" in out["answer"], "a truncated report must say so"
    assert logged[0]["factors"]["stopped_reason"] == "max_tokens"


# ── markdown the model actually writes must not leak as literal text ─────────
# Staging QA (2026-07-30) found three constructs rendering raw in the report:
# pipe tables (the model writes its ranked TL;DR as one), `---` rules, and `>`
# blockquotes. The fixtures below are the exact strings from that report.

BUG_TABLE = (
    "| # | Finding | Confidence | Action |\n"
    "|---|---------|------------|--------|\n"
    "| 2 | **Satisfaction predicts…** | HIGH | … |\n"
)


def test_pipe_table_renders_as_a_table_not_literal_text():
    out = ca._md_to_html(BUG_TABLE)

    assert "<table>" in out and "<thead>" in out and "<tbody>" in out
    # the exact row from the bug must not survive as literal pipes
    assert "| 2 | **Satisfaction predicts" not in out
    assert "|---" not in out
    assert "<th>Confidence</th>" in out
    assert "<td>2</td>" in out
    assert "<strong>Satisfaction predicts…</strong>" in out, "inline md still applies"
    assert "<td>HIGH</td>" in out


def test_table_alignment_row_is_honoured():
    out = ca._md_to_html("| a | b | c |\n|:--|:-:|--:|\n| 1 | 2 | 3 |\n")
    assert 'style="text-align:left"' in out
    assert 'style="text-align:center"' in out
    assert 'style="text-align:right"' in out


def test_horizontal_rule_renders_as_hr():
    for rule in ("---", "***", "___", "-----"):
        out = ca._md_to_html(f"before\n\n{rule}\n\nafter")
        assert "<hr>" in out, rule
        assert rule not in out, f"{rule} leaked as literal text"


def test_blockquote_renders_as_blockquote():
    out = ca._md_to_html("> Caveat: the sample is small.\n> Treat as early signal.")

    assert "<blockquote>" in out
    assert "&gt;" not in out and ">" in out  # markers consumed, tags emitted
    assert "Caveat: the sample is small." in out
    assert "Treat as early signal." in out


def test_blockquote_can_hold_a_list():
    out = ca._md_to_html("> Caveats:\n> - small n\n> - one week only")
    assert "<blockquote>" in out and "<ul>" in out
    assert "<li>small n</li>" in out


def test_prose_with_a_pipe_is_not_mistaken_for_a_table():
    """No delimiter row → it's a sentence, not a table."""
    out = ca._md_to_html("Revenue | cost split was not available in the export.")
    assert "<table>" not in out
    assert "Revenue | cost split" in out


def test_table_cells_are_still_escaped():
    """Escape-first discipline is unchanged by the new constructs."""
    out = ca._md_to_html("| col |\n|---|\n| <script>alert(1)</script> |\n")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_blockquote_and_hr_content_is_escaped():
    out = ca._md_to_html("> <img src=x onerror=alert(1)>\n\n---\n")
    assert "<img" not in out
    assert "&lt;img" in out


def test_escaped_pipe_stays_literal_inside_a_cell():
    out = ca._md_to_html("| expr |\n|---|\n| a \\| b |\n")
    assert "<td>a | b</td>" in out


def test_full_report_renders_the_bug_constructs(workspace, logged, monkeypatch):
    """End to end: the same three constructs inside a real analysis answer."""
    _csv(workspace)
    _client(
        monkeypatch,
        _message([_text(
            "## TL;DR\n\n"
            + BUG_TABLE
            + "\n---\n\n> Caveat: one week of data only.\n"
        )]),
    )

    answer = ca.answer(enterprise_id=COMPANY, question="analyze my data")["answer"]

    assert "<table>" in answer and "<hr>" in answer and "<blockquote>" in answer
    for leaked in ("| 2 |", "|---|", "> Caveat"):
        assert leaked not in answer, f"{leaked!r} leaked into the rendered report"


# ── 9. routing negatives stay negative ───────────────────────────────────────

def test_routing_negatives_are_unchanged_by_this_path():
    """The flag changes which ENGINE runs, never which questions route to DS."""
    for q in [
        "generate a PRD for onboarding",
        "prioritize these features",
        "summarize the customer calls from last week",
        "create tickets for the export feature",
        "analyze the survey data",
        "analyze customer feedback data",
        "synthesize the interview data",
        "what did we learn from the call transcripts?",
        "what's our churn rate?",
        "how many users do we have?",
    ]:
        assert not is_data_analysis_request(q), q


# ── 11. model selection + telemetry ──────────────────────────────────────────

def test_default_model_needs_no_env(monkeypatch):
    monkeypatch.delenv("SPRNTLY_DS_ANALYSIS_MODEL", raising=False)
    assert ca.resolve_model() == llm.DEFAULT_MODEL == "claude-sonnet-4-6"


def test_model_override_is_honoured_when_priced(monkeypatch):
    monkeypatch.setenv("SPRNTLY_DS_ANALYSIS_MODEL", "claude-opus-4-7")
    assert ca.resolve_model() == "claude-opus-4-7"


def test_unknown_model_override_fails_closed(monkeypatch):
    """An unpriced model would produce spend est_cost_usd can't cost — ignore it."""
    monkeypatch.setenv("SPRNTLY_DS_ANALYSIS_MODEL", "claude-imaginary-9")
    assert ca.resolve_model() == llm.DEFAULT_MODEL


def test_usage_is_accumulated_across_turns(workspace, logged, monkeypatch):
    _csv(workspace)
    _client(
        monkeypatch,
        _message([_text("one")], stop_reason="pause_turn", usage=_usage(100, 200)),
        _message([_text("two")], usage=_usage(7, 11)),
    )

    ca.answer(enterprise_id=COMPANY, question="analyze my data")

    factors = logged[0]["factors"]
    assert factors["turns"] == 2
    assert factors["input_tokens"] == 107
    assert factors["output_tokens"] == 211
    assert factors["cache_read_input_tokens"] == 10


def test_decision_log_failure_never_breaks_the_answer(workspace, monkeypatch):
    _csv(workspace)
    _client(monkeypatch, _message([_text("findings")]))
    monkeypatch.setattr(
        "app.graph.decision_log.log_agent_decision",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("db down")),
    )

    out = ca.answer(enterprise_id=COMPANY, question="analyze my data")

    assert "findings" in out["answer"]


# ── 1. flag gate + fallback in qa_agent ──────────────────────────────────────

def _flags(monkeypatch, flags) -> None:
    """Patch the STRICT reader the gate uses. `None` = the read itself failed."""
    monkeypatch.setattr("app.entitlements.read_feature_flags", lambda cid: flags)


def _flag(monkeypatch, value: bool) -> None:
    _flags(monkeypatch, {"ds_claude_analysis": value})


LEGACY_SENTINEL = {"answer": "v5.8", "key_points": [], "citations": [],
                   "confidence": 0.9, "unanswered": "", "_skill": "ds-agent",
                   "_skill_source": "ds-engine"}
CLAUDE_SENTINEL = {"answer": "<!doctype html><div>claude</div>", "key_points": [],
                   "citations": [], "confidence": 0.8, "unanswered": "",
                   "_skill": "ds-agent", "_skill_source": "ds-claude"}


def test_explicit_false_uses_the_deterministic_engine(workspace, monkeypatch):
    """The named opt-outs (Freezing Point LLC, Chaostrack Inc.) are stored as an
    explicit false — the ONLY thing that turns the engine off now."""
    import app.qa_agent as qa

    _csv(workspace)  # Part 3 capability gate (AC10): tabular data must exist
    _flag(monkeypatch, False)
    monkeypatch.setattr(legacy, "answer", lambda **kw: LEGACY_SENTINEL)
    monkeypatch.setattr(
        ca, "answer", lambda **kw: pytest.fail("Claude path ran for an opted-out company")
    )

    out = qa.answer(enterprise_id=COMPANY, question="analyze my data", dataset="acme")
    assert out is LEGACY_SENTINEL


def test_absent_flag_defaults_on(workspace, monkeypatch):
    """DEFAULT ON (Apurva, 2026-07-30): a company whose flags never mention the
    key — grandfathered or freshly onboarded — gets the Claude engine."""
    import app.qa_agent as qa

    _csv(workspace)  # Part 3 capability gate (AC10): tabular data must exist
    _flags(monkeypatch, {})
    monkeypatch.setattr(ca, "answer", lambda **kw: CLAUDE_SENTINEL)
    monkeypatch.setattr(
        legacy, "answer", lambda **kw: pytest.fail("legacy ran for a default-ON company")
    )

    assert qa.answer(
        enterprise_id=COMPANY, question="analyze my data", dataset="acme"
    ) is CLAUDE_SENTINEL


def test_other_flags_present_but_ours_absent_still_defaults_on(workspace, monkeypatch):
    """A real grandfathered row: full DEFAULT_FEATURE_FLAGS payload, no key of
    ours. The onboarding insert writes exactly this shape."""
    import app.qa_agent as qa

    _csv(workspace)  # Part 3 capability gate (AC10): tabular data must exist
    _flags(monkeypatch, {
        "agents": True, "top_insights": True, "chat_intent_envelope": True,
        "on_demand_analysis": True, "auto_prd_generation": True,
        "engineer_agent": False, "research_agent": False,
        "on_call_agent": False, "claude_code_handoff": False,
    })
    monkeypatch.setattr(ca, "answer", lambda **kw: CLAUDE_SENTINEL)
    monkeypatch.setattr(
        legacy, "answer", lambda **kw: pytest.fail("legacy ran for a default-ON company")
    )

    assert qa.answer(
        enterprise_id=COMPANY, question="analyze my data", dataset="acme"
    ) is CLAUDE_SENTINEL


def test_flag_read_failure_still_defaults_off(workspace, monkeypatch):
    """Deliberately NOT symmetric with the missing-key default. An unknown flag
    state must not ship a tenant's CSVs to the Files API — unlike #893, which
    fails open because it only picks a routing strategy."""
    import app.qa_agent as qa

    _csv(workspace)  # Part 3 capability gate (AC10): tabular data must exist
    _flags(monkeypatch, None)  # read_feature_flags signals failure with None
    monkeypatch.setattr(legacy, "answer", lambda **kw: LEGACY_SENTINEL)
    monkeypatch.setattr(
        ca, "answer", lambda **kw: pytest.fail("Claude path ran on an unknown flag state")
    )

    assert qa.answer(
        enterprise_id=COMPANY, question="analyze my data", dataset="acme"
    ) is LEGACY_SENTINEL


def test_flag_read_raising_defaults_off(workspace, monkeypatch):
    """Belt to the None braces: even if the reader raises instead of returning
    None, the gate resolves OFF rather than propagating."""
    import app.qa_agent as qa

    _csv(workspace)  # Part 3 capability gate (AC10): tabular data must exist

    def _boom(cid):  # noqa: ARG001
        raise RuntimeError("flags column missing")

    monkeypatch.setattr("app.entitlements.read_feature_flags", _boom)
    monkeypatch.setattr(legacy, "answer", lambda **kw: LEGACY_SENTINEL)

    assert qa.answer(
        enterprise_id=COMPANY, question="analyze my data", dataset="acme"
    ) is LEGACY_SENTINEL


def test_flag_on_uses_the_claude_engine(workspace, monkeypatch):
    import app.qa_agent as qa

    _csv(workspace)  # Part 3 capability gate (AC10): tabular data must exist
    _flag(monkeypatch, True)
    seen: dict = {}

    def _claude(**kw):
        seen.update(kw)
        return CLAUDE_SENTINEL

    monkeypatch.setattr(ca, "answer", _claude)
    monkeypatch.setattr(
        legacy, "answer", lambda **kw: pytest.fail("legacy ran with the flag ON")
    )

    out = qa.answer(enterprise_id=COMPANY, question="analyze my data", dataset="acme")
    assert out is CLAUDE_SENTINEL
    assert seen["question"] == "analyze my data"
    assert "is_cancelled" in seen, "cancellation must reach the loop"


def test_flag_on_but_claude_raises_falls_back_and_never_500s(workspace, monkeypatch):
    import app.qa_agent as qa

    _csv(workspace)  # Part 3 capability gate (AC10): tabular data must exist
    _flag(monkeypatch, True)
    monkeypatch.setattr(
        ca, "answer",
        lambda **kw: (_ for _ in ()).throw(ca.DsClaudeAnalysisError("boom")),
    )
    monkeypatch.setattr(legacy, "answer", lambda **kw: LEGACY_SENTINEL)

    out = qa.answer(enterprise_id=COMPANY, question="analyze my data", dataset="acme")

    assert out is LEGACY_SENTINEL
    for field in ("answer", "key_points", "citations", "confidence", "unanswered"):
        assert field in out


def test_cancellation_is_not_swallowed_by_the_fallback(workspace, monkeypatch):
    """A user Stop must abandon the ask, not spend a second full run."""
    import app.qa_agent as qa

    _csv(workspace)  # Part 3 capability gate (AC10): tabular data must exist
    _flag(monkeypatch, True)
    monkeypatch.setattr(
        ca, "answer", lambda **kw: (_ for _ in ()).throw(AskCancelled())
    )
    monkeypatch.setattr(
        legacy, "answer", lambda **kw: pytest.fail("legacy ran after a cancellation")
    )

    with pytest.raises(AskCancelled):
        qa.answer(
            enterprise_id=COMPANY,
            question="analyze my data",
            dataset="acme",
            is_cancelled=lambda: False,
        )
