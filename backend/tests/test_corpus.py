"""Tests for app.corpus — corpus loading + template loading.

We don't copy the real asurion dataset; instead we build tiny synthetic
datasets under `tmp_data_dir` so the tests run in microseconds and don't
break when the real corpus changes.
"""
from __future__ import annotations

import pytest


def _build_dataset(data_dir, name: str, files: dict[str, str]) -> None:
    """Helper: write a dataset folder with the given file → content map."""
    ds = data_dir / name
    ds.mkdir()
    for filename, body in files.items():
        (ds / filename).write_text(body)


def test_load_corpus_reads_md_files(isolated_settings):
    corpus_mod = isolated_settings["corpus"]
    data_dir = isolated_settings["data_dir"]
    _build_dataset(
        data_dir,
        "demo",
        {
            "alpha.md": "alpha body",
            "beta.md": "beta body",
        },
    )
    c = corpus_mod.load_corpus("demo")
    names = sorted(d.name for d in c.docs)
    assert names == ["alpha", "beta"]
    assert c.dataset == "demo"


def test_load_corpus_skips_underscore_files(isolated_settings):
    """`_reference/` and `_*.md` files are the answer key — never feed
    them to the LLM. Filter is by filename leading underscore."""
    corpus_mod = isolated_settings["corpus"]
    data_dir = isolated_settings["data_dir"]
    _build_dataset(
        data_dir,
        "demo",
        {
            "real.md": "use me",
            "_secret.md": "answer key — do not leak",
        },
    )
    c = corpus_mod.load_corpus("demo")
    assert [d.name for d in c.docs] == ["real"]


def test_load_corpus_ignores_non_md_files(isolated_settings):
    corpus_mod = isolated_settings["corpus"]
    data_dir = isolated_settings["data_dir"]
    _build_dataset(
        data_dir,
        "demo",
        {
            "a.md": "yes",
            "b.txt": "no",
            "c.json": "no",
        },
    )
    c = corpus_mod.load_corpus("demo")
    assert [d.name for d in c.docs] == ["a"]


def test_load_corpus_missing_dataset_raises_file_not_found(isolated_settings):
    corpus_mod = isolated_settings["corpus"]
    with pytest.raises(FileNotFoundError):
        corpus_mod.load_corpus("nope-does-not-exist")


def test_load_corpus_empty_dataset_dir_raises_runtime_error(isolated_settings):
    corpus_mod = isolated_settings["corpus"]
    data_dir = isolated_settings["data_dir"]
    (data_dir / "empty").mkdir()
    with pytest.raises(RuntimeError):
        corpus_mod.load_corpus("empty")


def test_load_corpus_only_underscore_files_raises_runtime_error(isolated_settings):
    """A dir with only `_*.md` files is effectively empty — same error."""
    corpus_mod = isolated_settings["corpus"]
    data_dir = isolated_settings["data_dir"]
    _build_dataset(data_dir, "answers", {"_key.md": "leaked"})
    with pytest.raises(RuntimeError):
        corpus_mod.load_corpus("answers")


def test_corpus_total_chars_sums_doc_lengths(isolated_settings):
    corpus_mod = isolated_settings["corpus"]
    data_dir = isolated_settings["data_dir"]
    _build_dataset(
        data_dir,
        "demo",
        {
            "a.md": "xxxx",     # 4
            "b.md": "yyyyyy",   # 6
        },
    )
    c = corpus_mod.load_corpus("demo")
    assert c.total_chars() == 10


def test_corpus_joined_wraps_each_doc_with_source_delimiters(isolated_settings):
    corpus_mod = isolated_settings["corpus"]
    data_dir = isolated_settings["data_dir"]
    _build_dataset(
        data_dir,
        "demo",
        {
            "alpha.md": "hello world",
        },
    )
    c = corpus_mod.load_corpus("demo")
    joined = c.joined()
    assert "<<< SOURCE: alpha >>>" in joined
    assert "<<< END SOURCE >>>" in joined
    assert "hello world" in joined


def test_corpus_joined_separates_multiple_docs(isolated_settings):
    corpus_mod = isolated_settings["corpus"]
    data_dir = isolated_settings["data_dir"]
    _build_dataset(
        data_dir,
        "demo",
        {"a.md": "body-a", "b.md": "body-b"},
    )
    c = corpus_mod.load_corpus("demo")
    joined = c.joined()
    # Both source headers appear; the bodies are in.
    assert joined.count("<<< SOURCE:") == 2
    assert "body-a" in joined and "body-b" in joined


def test_load_prd_template_returns_string(isolated_settings):
    corpus_mod = isolated_settings["corpus"]
    out = corpus_mod.load_prd_template()
    assert isinstance(out, str)
    assert len(out) > 0


def test_load_evidence_template_returns_string(isolated_settings):
    corpus_mod = isolated_settings["corpus"]
    out = corpus_mod.load_evidence_template()
    assert isinstance(out, str)
    assert len(out) > 0
