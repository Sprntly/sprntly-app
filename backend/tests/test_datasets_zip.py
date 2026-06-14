"""Tests for ZIP archive ingestion — app.datasets.ingest_zip.

A .zip uploaded to Sources is expanded and each supported member is ingested
individually. These cover: expansion, junk/dir filtering, unsupported-member
errors, nested-zip skip, per-member size cap, bad/empty archives, and tenancy.
"""
from __future__ import annotations

import io
import zipfile

import pytest


def _datasets_module(isolated_settings):
    import importlib
    import app.datasets as mod
    importlib.reload(mod)
    return mod


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


_BIG = 20 * 1024 * 1024


def test_expands_supported_members_and_flags_unsupported(isolated_settings):
    ds = _datasets_module(isolated_settings)
    ds.create_dataset("acme", "Acme")
    data = _zip({
        "notes.md": b"# Notes\nhello",
        "data.csv": b"a,b\n1,2\n",
        "logo.png": b"\x89PNG not real",          # unsupported → error
        "__MACOSX/._notes.md": b"junk",            # macOS junk → silently ignored
        "subdir/": b"",                             # directory entry → ignored
    })
    ingested, errors = ds.ingest_zip("acme", "bundle.zip", data, per_member_max_bytes=_BIG)

    assert sorted(f.original_filename for f in ingested) == ["data.csv", "notes.md"]
    assert all(f.md_chars > 0 for f in ingested)            # actually converted
    assert any(e["filename"] == "logo.png" for e in errors)  # unsupported reported
    assert not any(e["filename"].startswith("._") for e in errors)  # junk not reported


def test_skips_nested_zip(isolated_settings):
    ds = _datasets_module(isolated_settings)
    ds.create_dataset("acme", "Acme")
    data = _zip({"a.md": b"a", "nested.zip": _zip({"x.md": b"x"})})
    ingested, errors = ds.ingest_zip("acme", "b.zip", data, per_member_max_bytes=_BIG)
    assert [f.original_filename for f in ingested] == ["a.md"]
    assert any(e["filename"] == "nested.zip" and "Nested" in e["error"] for e in errors)


def test_per_member_size_cap(isolated_settings):
    ds = _datasets_module(isolated_settings)
    ds.create_dataset("acme", "Acme")
    data = _zip({"big.md": b"x" * 5000, "small.md": b"y"})
    ingested, errors = ds.ingest_zip("acme", "b.zip", data, per_member_max_bytes=1000)
    assert [f.original_filename for f in ingested] == ["small.md"]
    assert any(e["filename"] == "big.md" and "limit" in e["error"] for e in errors)


def test_unsupported_only_returns_errors_no_raise(isolated_settings):
    ds = _datasets_module(isolated_settings)
    ds.create_dataset("acme", "Acme")
    ingested, errors = ds.ingest_zip(
        "acme", "b.zip", _zip({"a.png": b"x", "b.exe": b"y"}), per_member_max_bytes=_BIG
    )
    assert ingested == []
    assert len(errors) == 2


def test_bad_archive_raises(isolated_settings):
    ds = _datasets_module(isolated_settings)
    ds.create_dataset("acme", "Acme")
    with pytest.raises(ds.DatasetError):
        ds.ingest_zip("acme", "broken.zip", b"not a zip at all", per_member_max_bytes=_BIG)


def test_empty_archive_raises(isolated_settings):
    ds = _datasets_module(isolated_settings)
    ds.create_dataset("acme", "Acme")
    with pytest.raises(ds.DatasetError):
        ds.ingest_zip("acme", "empty.zip", _zip({}), per_member_max_bytes=_BIG)


def test_unknown_dataset_raises(isolated_settings):
    ds = _datasets_module(isolated_settings)
    with pytest.raises(ds.DatasetNotFound):
        ds.ingest_zip("ghost", "b.zip", _zip({"a.md": b"a"}), per_member_max_bytes=_BIG)


def test_path_traversal_member_is_flattened(isolated_settings):
    """A member named '../evil.md' must land under the dataset by basename only."""
    ds = _datasets_module(isolated_settings)
    out = ds.create_dataset("acme", "Acme")
    data = _zip({"../../evil.md": b"pwned"})
    ingested, _ = ds.ingest_zip("acme", "b.zip", data, per_member_max_bytes=_BIG)
    assert [f.original_filename for f in ingested] == ["evil.md"]
    # raw original written inside the dataset's raw/ dir, not outside it
    from pathlib import Path
    base = Path(out["data_dir"]).resolve()
    assert base in Path(ingested[0].stored_raw_path).resolve().parents
