"""Tests for roadmap → KG ingest (app.kg_ingest.roadmap).

The uploaded roadmap used to stop at the brief PROMPT; now it reaches the
knowledge graph from BOTH entry points (the upload endpoint's kickoff, and the
incremental brief seed as grandfather + retry). What these tests pin down:

  1. happy path — channel="roadmap" provenance, pm_manual default, merited
     evidence source_types kept, and NO origin (the design decision below)
  2. the endpoint hook fires the kickoff exactly once, non-blocking
  3. the content-hash ledger makes a second ingest of the same file free
  4. REPLACE semantics — v1 bets A+B → v2 bets B+C staled A, kept B, wrote C
  5. empty / unextractable uploads: 400 on empty, no-op (and NO ledger row) on
     a scanned-image PDF, never a crash
  6. size limits — 413 over 20 MB, and a 20 MB roadmap is chunk-capped
  7. real docx / csv / xlsx / pdf bytes through the shared converter
  8. an abandoned onboarding still ingests (the upload fires at file-pick time)
  9. an extraction failure (bad OPENAI_API_KEY) leaves NO ledger row so the next
     seed retries, and the upload response is still ok:true
 10. grandfather — a roadmap uploaded before this shipped backfills on the next
     brief seed, and the seed after that is a no-op
 11. tenancy — company/workspace A replacing its roadmap never stales B's signals
 12. GATE INVARIANCE — a roadmap-only tenant still gets NO brief

DESIGN DECISION under test (12): roadmap signals are stamped with
``origin=None`` on purpose. A roadmap is the company's own plan, not evidence
that a problem exists, so it must not satisfy the brief evidence gate — a tenant
whose only data is a roadmap must still be refused a brief rather than handed one
composed of its own stated intentions.
"""
from __future__ import annotations

import io
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app.graph.extractor as ex
from app.graph.facade import GraphFacade
from app.graph.gateway import LLMResult
from app.kg_ingest import auto_sync
from app.kg_ingest import roadmap as rm

# ── extraction fake ─────────────────────────────────────────────────────────
# The extractor is exercised for real (source_type coercion, provenance
# stamping, content-keyed ids, the additive signal_ids key); only the model +
# embedding calls are faked. The fake reads the document text it is handed and
# emits one signal per "BET <X>" marker it finds, so a chunked or replaced
# roadmap produces exactly the signals its text implies.

_BETS = {
    "A": ("Ship self-serve onboarding in Q3", "Onboarding"),
    "B": ("Ship AI authoring in Q4", "AI authoring"),
    "C": ("Ship usage analytics in Q1", "Analytics"),
    "D": ("Ship SSO in Q2", "SSO"),
}


def _llm_result(items: list[dict]) -> LLMResult:
    return LLMResult(
        output={"signals": items}, model="m", prompt_version=ex.PROMPT_VERSION,
        input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.0, latency_ms=0,
        stop_reason="end_turn",
    )


def _item(content: str, theme: str, source_type: str = "pm_manual") -> dict:
    return {"kind": "finding", "content": content, "source_type": source_type,
            "theme": theme, "relationship": "SUPPORTS", "confidence": 0.9}


class _Extraction:
    """Context manager: fakes llm_call/embed_texts and counts model calls."""

    def __init__(self, extra_items: list[dict] | None = None,
                 boom: bool = False) -> None:
        self.calls: list[str] = []
        self._extra = extra_items or []
        self._boom = boom
        self._patches: list = []

    def _llm(self, **kwargs):
        text = kwargs.get("input", "")
        self.calls.append(text)
        if self._boom:
            raise RuntimeError("401 incorrect api key provided")
        items = [_item(*_BETS[k]) for k in sorted(_BETS) if f"BET {k}" in text]
        return _llm_result(items + self._extra)

    def __enter__(self):
        self._patches = [
            patch.object(ex, "llm_call", side_effect=lambda **k: self._llm(**k)),
            patch.object(ex, "embed_texts",
                         side_effect=lambda texts, **k: [[0.0] * 4 for _ in texts]),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()
        return False


def _roadmap_text(*bets: str) -> str:
    return "H2 Roadmap\n\n" + "\n".join(
        f"BET {b}: {_BETS[b][0]}" for b in bets
    )


# ── fixtures / helpers ──────────────────────────────────────────────────────

def _seed_company(db, company_id: str, slug: str = "acme") -> None:
    if not db.table("companies").select("id").eq("id", company_id).execute().data:
        db.table("companies").insert(
            {"id": company_id, "slug": slug, "display_name": slug.title()}
        ).execute()


def _store_roadmap(company_id: str, text: str, *, workspace_id=None,
                   filename="roadmap.md", version=1) -> None:
    """Write a roadmap_doc row directly (bypasses the converter — these tests
    control the extracted text)."""
    from app.db.client import require_client

    row = {"company_id": company_id, "filename": filename,
           "extracted_text": text, "content_type": "text/markdown",
           "version": version, "uploaded_at": "2026-07-29T00:00:00+00:00"}
    if workspace_id:
        row["workspace_id"] = workspace_id
        require_client().table("roadmap_doc").upsert(
            row, on_conflict="workspace_id").execute()
    else:
        existing = (require_client().table("roadmap_doc").select("id")
                    .eq("company_id", company_id).execute().data)
        if existing:
            require_client().table("roadmap_doc").update(row).eq(
                "company_id", company_id).execute()
        else:
            require_client().table("roadmap_doc").insert(row).execute()


def _roadmap_signals(facade: GraphFacade, company_id: str) -> dict[str, object]:
    """{content: Signal} over this company's ACTIVE roadmap-channel signals."""
    return {
        s.content: s for s in facade.active_signals(company_id)
        if (s.provenance or {}).get("channel") == rm.ROADMAP_CHANNEL
    }


def _ledger_rows(facade: GraphFacade, company_id: str) -> list:
    return facade.list_sources(company_id, source_type=rm.LEDGER_SOURCE_TYPE)


# ── 1. happy path ───────────────────────────────────────────────────────────

def test_ingest_stamps_roadmap_channel_and_defaults_source_type(isolated_settings):
    db = isolated_settings["supabase"]
    _seed_company(db, "co-1")
    _store_roadmap("co-1", _roadmap_text("A", "B"), workspace_id="ws-1",
                   filename="H2.md", version=3)
    facade = GraphFacade()

    # One extra item whose source_type the model picked on MERIT — a revenue
    # fact quoted inside the roadmap stays revenue, it is not flattened.
    with _Extraction(extra_items=[
        _item("Self-serve is worth $1.2M ARR", "Onboarding", "revenue")
    ]) as fx:
        out = rm.ingest_roadmap("co-1", "ws-1", facade=facade)

    assert out["status"] == "ingested"
    assert out["signals"] == 3 and out["chunks"] == 1 and out["expired"] == 0
    assert out["version"] == 3
    assert len(fx.calls) == 1

    sigs = _roadmap_signals(facade, "co-1")
    assert set(sigs) == {_BETS["A"][0], _BETS["B"][0],
                         "Self-serve is worth $1.2M ARR"}
    bet = sigs[_BETS["A"][0]]
    assert bet.source_type == "pm_manual"              # seeded type kept/defaulted
    assert sigs["Self-serve is worth $1.2M ARR"].source_type == "revenue"  # merit
    prov = bet.provenance
    assert prov["channel"] == "roadmap"
    assert prov["workspace_id"] == "ws-1"
    assert prov["roadmap_version"] == 3
    assert prov["filename"] == "H2.md"
    # THE design decision: no origin, so the brief evidence gate ignores it.
    assert "origin" not in prov

    # Ledger row recorded for this version.
    rows = _ledger_rows(facade, "co-1")
    assert len(rows) == 1
    assert rows[0].config["content_sha"] == out["content_sha"]
    assert rows[0].config["version"] == 3


def test_ingest_no_roadmap_is_a_noop(isolated_settings):
    _seed_company(isolated_settings["supabase"], "co-none")
    facade = GraphFacade()
    with _Extraction() as fx:
        assert rm.ingest_roadmap("co-none", "ws-1", facade=facade) == {
            "status": "no_text"}
    assert fx.calls == []
    assert _ledger_rows(facade, "co-none") == []


# ── 2. endpoint hook ────────────────────────────────────────────────────────

def _route_client(isolated_settings, company_id: str):
    import app.main as main_mod
    import app.routes.company as company_route
    from app.auth import CompanyContext

    db = isolated_settings["supabase"]
    _seed_company(db, company_id)
    main_mod.app.dependency_overrides[company_route.require_company] = (
        lambda: CompanyContext(company_id=company_id, role="owner", user_id="u1")
    )
    return TestClient(main_mod.app), company_route


def _clear(company_route):
    import app.main as main_mod
    main_mod.app.dependency_overrides.pop(company_route.require_company, None)


def test_post_roadmap_doc_fires_kickoff_exactly_once(isolated_settings, monkeypatch):
    client, route = _route_client(isolated_settings, "co-hook")
    calls: list[tuple] = []
    monkeypatch.setattr(route, "kickoff_roadmap_ingest",
                        lambda cid, wid: calls.append((cid, wid)))
    try:
        r = client.post("/v1/company/roadmap-doc", files={
            "file": ("roadmap.md", io.BytesIO(b"# H2\n\nBET A: onboarding"),
                     "text/markdown")})
    finally:
        _clear(route)
    assert r.status_code == 200, r.text
    assert len(calls) == 1
    assert calls[0][0] == "co-hook"


def test_kickoff_roadmap_ingest_starts_daemon_thread(monkeypatch):
    started = {}

    class FakeThread:
        def __init__(self, target=None, args=(), name=None, daemon=None):
            started.update(args=args, name=name, daemon=daemon)

        def start(self):
            started["started"] = True

    monkeypatch.setattr(auto_sync.threading, "Thread", FakeThread)
    assert auto_sync.kickoff_roadmap_ingest("co-7", "ws-2") is True
    assert started["started"] is True
    assert started["args"] == ("co-7", "ws-2")
    assert started["daemon"] is True
    assert started["name"] == "roadmap-ingest"


def test_kickoff_roadmap_ingest_never_raises_on_thread_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no threads today")

    monkeypatch.setattr(auto_sync.threading, "Thread", boom)
    # A thread-spawn failure must never bubble into the upload response.
    assert auto_sync.kickoff_roadmap_ingest("co-7", "ws-2") is False


def test_run_roadmap_ingest_calls_ingest_and_is_error_isolated(monkeypatch):
    import app.kg_ingest.roadmap as rm_mod

    calls = []
    monkeypatch.setattr(auto_sync, "GraphFacade", lambda *a, **k: "FACADE")
    monkeypatch.setattr(rm_mod, "ingest_roadmap",
                        lambda cid, wid, facade=None: calls.append((cid, wid, facade))
                        or {"status": "ingested"})
    auto_sync._run_roadmap_ingest("co-1", "ws-1")
    assert calls == [("co-1", "ws-1", "FACADE")]

    def boom(*a, **k):
        raise RuntimeError("extraction blew up")

    monkeypatch.setattr(rm_mod, "ingest_roadmap", boom)
    auto_sync._run_roadmap_ingest("co-1", "ws-1")   # must swallow


def test_roadmap_ingest_lock_is_per_company():
    a1 = auto_sync._roadmap_ingest_lock("company-a")
    a2 = auto_sync._roadmap_ingest_lock("company-a")
    b = auto_sync._roadmap_ingest_lock("company-b")
    assert a1 is a2          # same company → serialized replaces
    assert a1 is not b


# ── 3. same file, both entry points → one extraction ────────────────────────

def test_second_ingest_of_same_roadmap_is_a_ledger_noop(isolated_settings):
    """The upload kickoff AND the next brief seed both call ingest_roadmap. The
    second call must cost ZERO model calls."""
    db = isolated_settings["supabase"]
    _seed_company(db, "co-2")
    _store_roadmap("co-2", _roadmap_text("A", "B"), workspace_id="ws-1")
    facade = GraphFacade()

    with _Extraction() as fx:
        first = rm.ingest_roadmap("co-2", "ws-1", facade=facade)
        assert first["status"] == "ingested" and first["signals"] == 2
        calls_after_first = len(fx.calls)

        second = rm.ingest_roadmap("co-2", "ws-1", facade=facade)

    assert second["status"] == "unchanged"
    assert second["content_sha"] == first["content_sha"]
    assert len(fx.calls) == calls_after_first == 1     # no extra LLM call
    assert len(_ledger_rows(facade, "co-2")) == 1      # no duplicate ledger row
    assert len(_roadmap_signals(facade, "co-2")) == 2


# ── 4. replace semantics ────────────────────────────────────────────────────

def test_replace_stales_dropped_bets_keeps_survivors_writes_new(isolated_settings):
    """v1 = bets A+B, v2 = bets B+C. A is dropped → staled immediately; B
    survives untouched; C is written. Convergence then sees only B and C."""
    db = isolated_settings["supabase"]
    _seed_company(db, "co-3")
    facade = GraphFacade()

    _store_roadmap("co-3", _roadmap_text("A", "B"), workspace_id="ws-1", version=1)
    with _Extraction():
        v1 = rm.ingest_roadmap("co-3", "ws-1", facade=facade)
    assert v1["signals"] == 2 and v1["expired"] == 0
    b_id_v1 = _roadmap_signals(facade, "co-3")[_BETS["B"][0]].id

    _store_roadmap("co-3", _roadmap_text("B", "C"), workspace_id="ws-1", version=2)
    with _Extraction():
        v2 = rm.ingest_roadmap("co-3", "ws-1", facade=facade)

    assert v2["status"] == "ingested"
    assert v2["signals"] == 1        # only C is new
    assert v2["duplicates"] == 1     # B re-derived the same content-keyed id
    assert v2["expired"] == 1        # A retired

    live = _roadmap_signals(facade, "co-3")
    assert set(live) == {_BETS["B"][0], _BETS["C"][0]}
    assert _BETS["A"][0] not in live
    # B kept its EXISTING signal (not rewritten under a new id).
    assert live[_BETS["B"][0]].id == b_id_v1
    # A's row still exists — bitemporally closed, not deleted.
    all_contents = {
        s["content"] for s in db.table("kg_signal").select("content")
        .eq("enterprise_id", "co-3").execute().data
    }
    assert _BETS["A"][0] in all_contents
    # Two ledger rows: one per version.
    assert len(_ledger_rows(facade, "co-3")) == 2


def test_replace_never_touches_non_roadmap_signals(isolated_settings):
    """A connector/upload signal must survive a roadmap replace untouched."""
    from app.graph.types import Signal

    db = isolated_settings["supabase"]
    _seed_company(db, "co-3b")
    facade = GraphFacade()
    facade.write_signal("co-3b", Signal(
        enterprise_id="co-3b", source_type="customer_voice", kind="bug",
        content="Customers hate the importer",
        provenance={"origin": "connector", "channel": "slack"},
    ))

    _store_roadmap("co-3b", _roadmap_text("A"), workspace_id="ws-1", version=1)
    with _Extraction():
        rm.ingest_roadmap("co-3b", "ws-1", facade=facade)
    _store_roadmap("co-3b", _roadmap_text("C"), workspace_id="ws-1", version=2)
    with _Extraction():
        out = rm.ingest_roadmap("co-3b", "ws-1", facade=facade)

    assert out["expired"] == 1        # bet A only
    contents = {s.content for s in facade.active_signals("co-3b")}
    assert "Customers hate the importer" in contents


# ── 5. malformed / empty / unextractable ────────────────────────────────────

def test_post_rejects_empty_file(isolated_settings):
    client, route = _route_client(isolated_settings, "co-empty")
    try:
        r = client.post("/v1/company/roadmap-doc", files={
            "file": ("empty.md", io.BytesIO(b""), "text/markdown")})
    finally:
        _clear(route)
    assert r.status_code == 400


def test_unextractable_upload_stores_but_ingest_is_a_noop(isolated_settings):
    """A scanned-image PDF (or any binary) converts to no text: the upload still
    succeeds, and ingest no-ops WITHOUT a ledger row so a readable re-upload is
    tried again."""
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)

    client, route = _route_client(isolated_settings, "co-bin")
    try:
        r = client.post("/v1/company/roadmap-doc", files={
            "file": ("scan.pdf", io.BytesIO(buf.getvalue()), "application/pdf")})
    finally:
        _clear(route)
    assert r.status_code == 200, r.text

    facade = GraphFacade()
    with _Extraction() as fx:
        out = rm.ingest_roadmap("co-bin", None, facade=facade)
    assert out == {"status": "no_text"}
    assert fx.calls == []
    assert _ledger_rows(facade, "co-bin") == []       # retried on next upload


def test_whitespace_only_roadmap_is_a_noop(isolated_settings):
    _seed_company(isolated_settings["supabase"], "co-ws")
    _store_roadmap("co-ws", "   \n\n\t  ", workspace_id="ws-1")
    facade = GraphFacade()
    with _Extraction() as fx:
        assert rm.ingest_roadmap("co-ws", "ws-1", facade=facade) == {
            "status": "no_text"}
    assert fx.calls == []


# ── 6. size limits ──────────────────────────────────────────────────────────

def test_post_rejects_over_20mb(isolated_settings):
    from app.roadmap_doc import ROADMAP_PROMPT_MAX_CHARS  # noqa: F401 (doc anchor)
    from app.routes.company import ROADMAP_MAX_UPLOAD_BYTES

    client, route = _route_client(isolated_settings, "co-big")
    try:
        r = client.post("/v1/company/roadmap-doc", files={
            "file": ("huge.md", io.BytesIO(b"x" * (ROADMAP_MAX_UPLOAD_BYTES + 1)),
                     "text/markdown")})
    finally:
        _clear(route)
    assert r.status_code == 413


def test_20mb_of_text_is_chunk_capped(isolated_settings):
    """A roadmap right at the upload cap must not turn into thousands of model
    calls — chunking is capped, matching the uploads puller."""
    db = isolated_settings["supabase"]
    _seed_company(db, "co-huge")
    huge = ("BET A: " + "filler " * 100 + "\n") * 30_000     # ~20 MB of text
    assert len(huge) > 20 * 1024 * 1024
    _store_roadmap("co-huge", huge, workspace_id="ws-1")

    assert len(rm._chunks(huge)) == rm._MAX_CHUNKS
    facade = GraphFacade()
    with _Extraction() as fx:
        out = rm.ingest_roadmap("co-huge", "ws-1", facade=facade)
    assert out["chunks"] == rm._MAX_CHUNKS
    assert len(fx.calls) == rm._MAX_CHUNKS
    assert all(len(c) <= rm._CHUNK_CHARS + 200 for c in fx.calls)  # +hint/wrapper


def test_multi_chunk_roadmap_extracts_every_chunk(isolated_settings):
    """A two-chunk roadmap: the bet only present in chunk 2 still lands."""
    db = isolated_settings["supabase"]
    _seed_company(db, "co-chunk")
    text = "BET A: x\n" + ("pad " * 1200) + "\nBET C: y\n"
    assert len(text) > rm._CHUNK_CHARS
    _store_roadmap("co-chunk", text, workspace_id="ws-1")
    facade = GraphFacade()
    with _Extraction() as fx:
        out = rm.ingest_roadmap("co-chunk", "ws-1", facade=facade)
    assert out["chunks"] == 2 and len(fx.calls) == 2
    assert set(_roadmap_signals(facade, "co-chunk")) == {
        _BETS["A"][0], _BETS["C"][0]}


# ── 7. real file formats through the shared converter ───────────────────────

def _minimal_pdf(line: str) -> bytes:
    """A tiny single-page PDF with one real text run (no reportlab needed)."""
    content = f"BT /F1 24 Tf 72 700 Td ({line}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, xref))
    return bytes(out)


def _docx_bytes(lines: list[str]) -> bytes:
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    for line in lines:
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _xlsx_bytes(rows: list[list[str]]) -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Roadmap"
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.mark.parametrize("filename,builder,content_type", [
    ("roadmap.md", lambda: b"# H2\n\nBET A: onboarding\nBET B: authoring",
     "text/markdown"),
    ("roadmap.csv", lambda: b"bet,quarter\nBET A,Q3\nBET B,Q4\n", "text/csv"),
    ("roadmap.docx", lambda: _docx_bytes(["H2 Roadmap", "BET A: onboarding",
                                          "BET B: authoring"]),
     "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ("roadmap.xlsx", lambda: _xlsx_bytes([["bet", "quarter"], ["BET A", "Q3"],
                                          ["BET B", "Q4"]]),
     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("roadmap.pdf", lambda: _minimal_pdf("BET A: onboarding and BET B: authoring"),
     "application/pdf"),
])
def test_real_formats_reach_the_kg(isolated_settings, filename, builder,
                                  content_type):
    """Spreadsheet and deck roadmaps matter — every format the shared converter
    handles must produce KG signals, not just markdown."""
    from app.roadmap_doc import save_roadmap_doc

    db = isolated_settings["supabase"]
    _seed_company(db, "co-fmt")
    doc = save_roadmap_doc("co-fmt", filename=filename, data=builder(),
                           content_type=content_type, workspace_id="ws-1")
    assert doc.extracted_text.strip(), f"{filename} converted to nothing"

    facade = GraphFacade()
    with _Extraction():
        out = rm.ingest_roadmap("co-fmt", "ws-1", facade=facade)
    assert out["status"] == "ingested"
    assert set(_roadmap_signals(facade, "co-fmt")) == {_BETS["A"][0],
                                                      _BETS["B"][0]}


# ── 8. abandoned onboarding ─────────────────────────────────────────────────

def test_ingest_completes_for_abandoned_onboarding(isolated_settings, monkeypatch):
    """The roadmap upload fires at file-PICK time, before the wizard finishes.
    A PM who closes the tab right after picking must still get their roadmap in
    the KG — nothing here depends on onboarding completion."""
    client, route = _route_client(isolated_settings, "co-abandon")
    ran: list[dict] = []

    def _sync_kickoff(company_id, workspace_id):
        # Run the kickoff body inline so the test observes the real ingest
        # without depending on daemon-thread timing.
        with _Extraction():
            ran.append(rm.ingest_roadmap(company_id, workspace_id,
                                         facade=GraphFacade()))

    monkeypatch.setattr(route, "kickoff_roadmap_ingest", _sync_kickoff)
    try:
        r = client.post("/v1/company/roadmap-doc", files={
            "file": ("roadmap.md",
                     io.BytesIO(_roadmap_text("A", "B").encode()),
                     "text/markdown")})
    finally:
        _clear(route)

    assert r.status_code == 200, r.text
    assert ran and ran[0]["status"] == "ingested" and ran[0]["signals"] == 2
    # Onboarding state untouched — no company_profile / onboarding row needed.
    assert set(_roadmap_signals(GraphFacade(), "co-abandon")) == {
        _BETS["A"][0], _BETS["B"][0]}


# ── 9. extraction failure (bad OPENAI/ANTHROPIC key) ────────────────────────

def test_extraction_failure_leaves_no_ledger_row_and_retries(isolated_settings):
    db = isolated_settings["supabase"]
    _seed_company(db, "co-badkey")
    _store_roadmap("co-badkey", _roadmap_text("A", "B"), workspace_id="ws-1")
    facade = GraphFacade()

    with _Extraction(boom=True):
        with pytest.raises(RuntimeError):
            rm.ingest_roadmap("co-badkey", "ws-1", facade=facade)

    assert _ledger_rows(facade, "co-badkey") == []     # NOT recorded
    assert _roadmap_signals(facade, "co-badkey") == {}

    # The next touch retries and succeeds.
    with _Extraction():
        out = rm.ingest_roadmap("co-badkey", "ws-1", facade=facade)
    assert out["status"] == "ingested" and out["signals"] == 2


def test_upload_response_is_ok_even_when_ingest_fails(isolated_settings, monkeypatch):
    """The upload must never surface an ingest failure to the PM."""
    client, route = _route_client(isolated_settings, "co-badkey2")

    def _boom_kickoff(company_id, workspace_id):
        # kickoff_roadmap_ingest swallows everything; emulate the real body.
        auto_sync._run_roadmap_ingest(company_id, workspace_id)

    monkeypatch.setattr(route, "kickoff_roadmap_ingest", _boom_kickoff)
    import app.kg_ingest.roadmap as rm_mod
    monkeypatch.setattr(rm_mod, "ingest_roadmap",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("401")))
    try:
        r = client.post("/v1/company/roadmap-doc", files={
            "file": ("roadmap.md", io.BytesIO(b"BET A: x"), "text/markdown")})
    finally:
        _clear(route)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_seed_leg_is_error_isolated(isolated_settings, monkeypatch):
    """A roadmap failure must never abort a brief's seed."""
    import app.synthesis_brief as sb

    _seed_company(isolated_settings["supabase"], "co-seedfail")
    import app.kg_ingest.roadmap as rm_mod
    monkeypatch.setattr(rm_mod, "ingest_roadmap",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = sb._seed_from_roadmap(GraphFacade(), "co-seedfail", "acme")
    assert out == {"status": "error"}


# ── 10. grandfather via the incremental brief seed ──────────────────────────

def test_grandfather_pre_existing_roadmap_backfills_on_next_seed(isolated_settings):
    """A roadmap uploaded BEFORE this shipped: no ledger row, empty KG. The next
    brief's seed ingests it; the seed after that is a no-op. No migration."""
    import app.synthesis_brief as sb

    db = isolated_settings["supabase"]
    _seed_company(db, "co-grand", slug="grandco")
    db.table("workspaces").insert({
        "id": "ws-grand", "company_id": "co-grand", "name": "Default",
        "slug": "default", "is_default": 1,
    }).execute()
    db.table("datasets").insert({
        "slug": "grandco", "display_name": "Grandco", "workspace_id": "ws-grand",
    }).execute()
    _store_roadmap("co-grand", _roadmap_text("A", "B"), workspace_id="ws-grand")

    facade = GraphFacade()
    assert facade.active_signals("co-grand") == []      # empty KG
    assert _ledger_rows(facade, "co-grand") == []       # never ingested

    with _Extraction() as fx:
        first = sb.seed_incremental(facade, "co-grand", "grandco")
        assert first["roadmap"]["status"] == "ingested"
        assert first["roadmap"]["signals"] == 2
        calls_after_first = len(fx.calls)

        second = sb.seed_incremental(facade, "co-grand", "grandco")

    assert second["roadmap"]["status"] == "unchanged"
    assert len(fx.calls) == calls_after_first           # no re-extraction
    assert set(_roadmap_signals(facade, "co-grand")) == {_BETS["A"][0],
                                                         _BETS["B"][0]}


def test_seed_resolves_non_default_workspace_dataset(isolated_settings):
    """A second workspace's brief slug ('{company}--{workspace}') must resolve to
    THAT workspace's roadmap, not the default one's."""
    import app.synthesis_brief as sb

    db = isolated_settings["supabase"]
    _seed_company(db, "co-multi", slug="multico")
    for wid, wslug, default in (("ws-a", "default", 1), ("ws-b", "team-b", 0)):
        db.table("workspaces").insert({
            "id": wid, "company_id": "co-multi", "name": wslug,
            "slug": wslug, "is_default": default,
        }).execute()
    db.table("datasets").insert({
        "slug": "multico--team-b", "display_name": "Team B",
        "workspace_id": "ws-b"}).execute()

    assert sb._workspace_id_for_slug("co-multi", "multico--team-b") == "ws-b"
    # Bare company slug (no dataset row) falls back to the default workspace.
    assert sb._workspace_id_for_slug("co-multi", "multico") == "ws-a"


# ── 11. tenancy / workspace isolation ──────────────────────────────────────

def test_replace_in_one_workspace_never_stales_another(isolated_settings):
    """Two workspaces in the SAME company + a second company. Replacing
    workspace A's roadmap expires only A's dropped bets."""
    db = isolated_settings["supabase"]
    _seed_company(db, "co-t1", slug="tenant1")
    _seed_company(db, "co-t2", slug="tenant2")
    facade = GraphFacade()

    _store_roadmap("co-t1", _roadmap_text("A", "B"), workspace_id="ws-a")
    _store_roadmap("co-t1", _roadmap_text("D"), workspace_id="ws-b")
    _store_roadmap("co-t2", _roadmap_text("A", "D"), workspace_id="ws-c")
    with _Extraction():
        rm.ingest_roadmap("co-t1", "ws-a", facade=facade)
        rm.ingest_roadmap("co-t1", "ws-b", facade=facade)
        rm.ingest_roadmap("co-t2", "ws-c", facade=facade)

    # Note: signal ids are content-keyed per ENTERPRISE, so co-t1/ws-b's bet D
    # and co-t2's bet D are different rows; within co-t1 the two workspaces have
    # disjoint bets here so provenance is the only discriminator that matters.
    _store_roadmap("co-t1", _roadmap_text("C"), workspace_id="ws-a", version=2)
    with _Extraction():
        out = rm.ingest_roadmap("co-t1", "ws-a", facade=facade)

    assert out["expired"] == 2      # A + B, both workspace ws-a
    live_t1 = _roadmap_signals(facade, "co-t1")
    by_ws = {}
    for content, sig in live_t1.items():
        by_ws.setdefault(sig.provenance.get("workspace_id"), set()).add(content)
    assert by_ws["ws-a"] == {_BETS["C"][0]}
    assert by_ws["ws-b"] == {_BETS["D"][0]}          # untouched sibling workspace
    # The other COMPANY is completely untouched.
    assert set(_roadmap_signals(facade, "co-t2")) == {_BETS["A"][0], _BETS["D"][0]}


def test_ledger_is_per_workspace(isolated_settings):
    """The same roadmap FILE in two workspaces gets two ledger rows + two
    extractions (each workspace's signals carry its own provenance)."""
    db = isolated_settings["supabase"]
    _seed_company(db, "co-t3")
    facade = GraphFacade()
    text = _roadmap_text("A")
    _store_roadmap("co-t3", text, workspace_id="ws-x")
    with _Extraction():
        a = rm.ingest_roadmap("co-t3", "ws-x", facade=facade)
    _store_roadmap("co-t3", text, workspace_id="ws-y")
    with _Extraction():
        b = rm.ingest_roadmap("co-t3", "ws-y", facade=facade)

    assert a["content_sha"] != b["content_sha"]
    assert a["status"] == b["status"] == "ingested"
    assert len(_ledger_rows(facade, "co-t3")) == 2
    # Content-keyed ids are per-ENTERPRISE, so ws-y's identical bet re-derives
    # ws-x's signal id → recorded as a duplicate, not a second row. That is why
    # the keep-set includes duplicates: neither workspace can expire the other's.
    assert b["duplicates"] == 1


# ── 12. GATE INVARIANCE — the design decision ──────────────────────────────

def test_roadmap_only_tenant_still_gets_no_brief(isolated_settings):
    """REGRESSION GUARD for the origin=None decision.

    A company whose ONLY data is an uploaded roadmap must still fail the brief
    evidence gate. If roadmap signals ever start carrying origin="upload", the
    upload-only relaxation fires and this tenant gets a brief composed entirely
    of its own stated plans — which is exactly the failure mode origin=None
    exists to prevent.
    """
    from app.synthesis.convergence import (
        compute_convergence,
        has_sufficient_evidence,
        is_upload_only,
    )

    db = isolated_settings["supabase"]
    _seed_company(db, "co-gate")
    # A roadmap with plenty of bets — volume must not buy its way past the gate.
    _store_roadmap("co-gate", _roadmap_text("A", "B", "C", "D"),
                   workspace_id="ws-1")
    facade = GraphFacade()
    with _Extraction():
        out = rm.ingest_roadmap("co-gate", "ws-1", facade=facade)
    assert out["signals"] == 4

    convergence = compute_convergence(facade, "co-gate")
    assert convergence, "roadmap signals should still form themes"
    # Neither upload nor connector evidence — origin is absent by design.
    assert all(tc.upload_signal_count == 0 for tc in convergence)
    assert all(tc.connector_signal_count == 0 for tc in convergence)
    assert is_upload_only(convergence) is False
    assert has_sufficient_evidence(convergence) is False


def test_roadmap_does_not_add_a_brief_data_source(isolated_settings):
    """brief_gate is unchanged: a roadmap is not an evidence connection and not a
    corpus upload, so a roadmap-only company is still refused up front."""
    from app.brief_gate import has_brief_data_source

    db = isolated_settings["supabase"]
    _seed_company(db, "co-gate2", slug="gateco")
    _store_roadmap("co-gate2", _roadmap_text("A", "B"), workspace_id="ws-1")
    with _Extraction():
        rm.ingest_roadmap("co-gate2", "ws-1", facade=GraphFacade())
    assert has_brief_data_source("co-gate2", "gateco") is False


# ── extractor contract: the additive signal_ids key ────────────────────────

def test_extract_document_returns_signal_ids_including_duplicates(isolated_settings):
    """The keep-set contract roadmap replace semantics rely on: written ids AND
    duplicate-skipped ids, with the pre-existing keys untouched."""
    facade = GraphFacade()
    _seed_company(isolated_settings["supabase"], "ent-k")
    with _Extraction():
        first = ex.extract_document(facade, "ent-k", doc_name="d",
                                    text=_roadmap_text("A", "B"))
        second = ex.extract_document(facade, "ent-k", doc_name="d",
                                     text=_roadmap_text("A", "B"))
    assert first["signals"] == 2 and first["skipped"] == 0
    assert len(first["signal_ids"]) == 2
    expected = {str(uuid.uuid5(ex._NS, f"ent-k|{_BETS[b][0]}")) for b in ("A", "B")}
    assert set(first["signal_ids"]) == expected
    # Re-run: nothing written, but the ids are STILL reported (duplicates count).
    assert second["signals"] == 0 and second["skipped"] == 2
    assert set(second["signal_ids"]) == expected


def test_extract_document_returns_empty_signal_ids_when_nothing_extracted(
        isolated_settings):
    facade = GraphFacade()
    _seed_company(isolated_settings["supabase"], "ent-e")
    with patch.object(ex, "llm_call", return_value=_llm_result([])):
        out = ex.extract_document(facade, "ent-e", doc_name="d", text="nothing here")
    assert out == {"signals": 0, "themes": 0, "skipped": 0, "signal_ids": []}


def test_expire_signals_is_tenant_scoped(isolated_settings):
    """facade.expire_signals must never reach into another enterprise."""
    from app.graph.types import Signal

    db = isolated_settings["supabase"]
    _seed_company(db, "co-e1")
    _seed_company(db, "co-e2", slug="other")
    facade = GraphFacade()
    mine = facade.write_signal("co-e1", Signal(
        enterprise_id="co-e1", source_type="pm_manual", kind="finding",
        content="mine"))
    theirs = facade.write_signal("co-e2", Signal(
        enterprise_id="co-e2", source_type="pm_manual", kind="finding",
        content="theirs"))

    # Asking co-e1 to expire co-e2's signal is a silent no-op.
    assert facade.expire_signals("co-e1", [theirs.id]) == 0
    assert [s.content for s in facade.active_signals("co-e2")] == ["theirs"]

    assert facade.expire_signals("co-e1", [mine.id]) == 1
    assert facade.active_signals("co-e1") == []
    assert facade.expire_signals("co-e1", []) == 0
