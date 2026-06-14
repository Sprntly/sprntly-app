"""Tests for app.ingest — file conversion + helpers.

docx/xlsx/pdf tests build real binary inputs with the upstream libs to make
sure our converters survive the round-trip. If a library isn't installed we
skip rather than fail — CI installs them via requirements.txt.
"""
from __future__ import annotations

import io

import pytest

from app import ingest


def test_slugify_basic():
    assert ingest.slugify("Acme Corp Quarterly Plan") == "acme_corp_quarterly_plan"
    assert ingest.slugify("UPPER") == "upper"
    assert ingest.slugify("with-hyphens") == "with_hyphens"
    assert ingest.slugify("   spaced  ") == "spaced"
    assert ingest.slugify("!!!") == "untitled"
    assert ingest.slugify("") == "untitled"


def test_md_filename_preserves_stem():
    assert ingest.md_filename("Customer Data.docx") == "customer_data.md"
    assert ingest.md_filename("path/to/Foo Bar.PDF") == "foo_bar.md"
    assert ingest.md_filename("already_a_slug.txt") == "already_a_slug.md"


def test_txt_to_md_passthrough():
    out = ingest.txt_to_md(b"hello\nworld")
    assert "hello" in out
    assert "world" in out


def test_txt_handles_non_utf8():
    out = ingest.txt_to_md(b"\xff\xfecaf\xe9")
    # Doesn't raise; replacement chars OK.
    assert isinstance(out, str)


def test_convert_unknown_textual_passes_through():
    # yaml/json/etc. have no dedicated converter but decode cleanly → text.
    out = ingest.convert("config.yaml", b"name: acme\nenv: prod\n")
    assert "name: acme" in out
    assert "env: prod" in out


def test_convert_unknown_binary_becomes_stub():
    # Binary content (e.g. audio) is stored but emits a placeholder stub.
    out = ingest.convert("memo.m4a", b"\x00\x01\x02binary\xff\xfe")
    assert "memo.m4a" in out
    assert "not yet parsed" in out


def test_convert_never_raises_for_unknown_type():
    # The old UnsupportedFileType path is gone — nothing is rejected.
    assert isinstance(ingest.convert("foo.exe", b"\x00\x01"), str)


def test_convert_routes_by_extension_case_insensitive():
    # .TXT and .txt both route to txt_to_md
    out = ingest.convert("notes.TXT", b"hi")
    assert out == "hi"


def test_docx_to_md():
    try:
        import docx
    except ImportError:
        pytest.skip("python-docx not installed")
    buf = io.BytesIO()
    d = docx.Document()
    d.add_heading("Title", level=1)
    d.add_paragraph("Body paragraph.")
    d.add_heading("Section", level=2)
    d.add_paragraph("Second body.")
    d.save(buf)
    out = ingest.docx_to_md(buf.getvalue())
    assert "# Title" in out
    assert "Body paragraph." in out
    assert "## Section" in out


def test_xlsx_to_md():
    try:
        import openpyxl
    except ImportError:
        pytest.skip("openpyxl not installed")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Funnel"
    ws.append(["step", "users"])
    ws.append(["signup", 100])
    ws.append(["activate", 60])
    buf = io.BytesIO()
    wb.save(buf)
    out = ingest.xlsx_to_md(buf.getvalue())
    assert "## Funnel" in out
    assert "signup" in out
    assert "100" in out


def test_xlsx_to_md_truncates_long_sheets():
    try:
        import openpyxl
    except ImportError:
        pytest.skip("openpyxl not installed")
    wb = openpyxl.Workbook()
    ws = wb.active
    for i in range(100):
        ws.append([f"row{i}", i])
    buf = io.BytesIO()
    wb.save(buf)
    out = ingest.xlsx_to_md(buf.getvalue(), max_rows=10)
    assert "truncated" in out


def test_pdf_to_md():
    try:
        import pypdf
        from pypdf import PdfWriter
    except ImportError:
        pytest.skip("pypdf not installed")
    # Easiest portable way: write an empty page (we don't need real text — we
    # only verify the converter doesn't crash and returns a string).
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    out = ingest.pdf_to_md(buf.getvalue())
    assert isinstance(out, str)


def test_convert_md_passthrough():
    out = ingest.convert("notes.md", b"# heading\n\nbody")
    assert out.startswith("# heading")
