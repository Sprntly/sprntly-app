"""Tests for app.datasets — service layer."""
from __future__ import annotations

from pathlib import Path

import pytest


# Helpers ---------------------------------------------------------------

def _datasets_module(isolated_settings):
    import importlib
    import app.datasets as mod
    importlib.reload(mod)
    return mod


# Tests -----------------------------------------------------------------

def test_validate_slug_accepts_clean(isolated_settings):
    ds = _datasets_module(isolated_settings)
    assert ds.validate_slug("acme") == "acme"
    assert ds.validate_slug("ACME") == "acme"
    assert ds.validate_slug("acme_corp_2") == "acme_corp_2"
    assert ds.validate_slug("a-b") == "a-b"


@pytest.mark.parametrize("bad", ["", "a", "_acme", "-acme", "with space", "with/slash", "x" * 64])
def test_validate_slug_rejects(bad, isolated_settings):
    ds = _datasets_module(isolated_settings)
    with pytest.raises(ds.InvalidSlug):
        ds.validate_slug(bad)


def test_create_dataset_creates_dirs_and_row(isolated_settings):
    ds = _datasets_module(isolated_settings)
    out = ds.create_dataset("acme", "Acme Corp")
    assert out["slug"] == "acme"
    base = Path(out["data_dir"])
    assert base.is_dir()
    assert (base / "raw").is_dir()
    assert isolated_settings["db"].dataset_exists("acme")


def test_create_dataset_rejects_duplicate(isolated_settings):
    ds = _datasets_module(isolated_settings)
    ds.create_dataset("acme", "Acme Corp")
    with pytest.raises(ds.DatasetAlreadyExists):
        ds.create_dataset("acme", "Acme 2")


def test_create_dataset_requires_display_name(isolated_settings):
    ds = _datasets_module(isolated_settings)
    with pytest.raises(ds.DatasetError):
        ds.create_dataset("acme", "")


def test_ingest_file_writes_raw_and_md(isolated_settings):
    ds = _datasets_module(isolated_settings)
    ds.create_dataset("acme", "Acme")
    result = ds.ingest_file("acme", "notes.txt", b"hello world")
    assert Path(result.stored_raw_path).read_bytes() == b"hello world"
    assert Path(result.md_path).read_text() == "hello world"
    assert result.original_filename == "notes.txt"
    assert result.md_chars == len("hello world")


def test_ingest_file_disambiguates_raw_collision(isolated_settings):
    ds = _datasets_module(isolated_settings)
    ds.create_dataset("acme", "Acme")
    a = ds.ingest_file("acme", "notes.txt", b"first")
    b = ds.ingest_file("acme", "notes.txt", b"second")
    # Raw paths differ; both preserved.
    assert a.stored_raw_path != b.stored_raw_path
    assert Path(a.stored_raw_path).read_bytes() == b"first"
    assert Path(b.stored_raw_path).read_bytes() == b"second"
    # MD paths also differ.
    assert a.md_path != b.md_path


def test_ingest_file_unknown_dataset_raises(isolated_settings):
    ds = _datasets_module(isolated_settings)
    with pytest.raises(ds.DatasetNotFound):
        ds.ingest_file("ghost", "notes.txt", b"hi")


def test_ingest_file_unsupported_rolls_back_raw(isolated_settings):
    ds = _datasets_module(isolated_settings)
    from app.ingest import UnsupportedFileType
    ds.create_dataset("acme", "Acme")
    raw = ds.raw_path("acme")
    before = list(raw.iterdir()) if raw.exists() else []
    with pytest.raises(UnsupportedFileType):
        ds.ingest_file("acme", "evil.exe", b"\x00\x01\x02")
    after = list(raw.iterdir())
    # No orphan file left behind.
    assert before == after


def test_list_datasets_includes_brief_status(isolated_settings):
    ds = _datasets_module(isolated_settings)
    ds.create_dataset("acme", "Acme")
    ds.ingest_file("acme", "notes.txt", b"data")
    listing = ds.list_datasets()
    assert len(listing) == 1
    only = listing[0]
    assert only["slug"] == "acme"
    assert only["has_brief"] is False
    assert only["raw_file_count"] == 1
    assert only["md_file_count"] == 1


def test_seed_filesystem_datasets_registers_on_disk_corpus(isolated_settings):
    ds = _datasets_module(isolated_settings)
    # Drop a fake dataset directly on disk, no DB row yet.
    base = isolated_settings["data_dir"] / "asurion"
    base.mkdir()
    (base / "context.md").write_text("# asurion")
    seeded = ds.seed_filesystem_datasets()
    assert seeded == 1
    assert isolated_settings["db"].dataset_exists("asurion")
    # Re-running seeds zero (idempotent).
    assert ds.seed_filesystem_datasets() == 0


def test_seed_filesystem_skips_dirs_with_no_corpus(isolated_settings):
    ds = _datasets_module(isolated_settings)
    base = isolated_settings["data_dir"] / "empty"
    base.mkdir()
    (base / "raw").mkdir()
    # No .md files at the dataset root — skip.
    seeded = ds.seed_filesystem_datasets()
    assert seeded == 0
    assert not isolated_settings["db"].dataset_exists("empty")
