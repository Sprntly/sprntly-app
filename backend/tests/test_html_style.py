"""Unit tests for the server-side canonical CSS injector (Phase 2)."""
from app.html_style import inject_canonical_css

CANON = ":root{--green:#1A6B47}\n.page{padding:64px}"


def test_replaces_empty_style_marker():
    html = (
        "<!DOCTYPE html><head><style>/* injected server-side */</style></head>"
        "<body><div class='page'>x</div></body>"
    )
    out = inject_canonical_css(html, CANON)
    assert "/* injected server-side */" not in out
    assert "--green:#1A6B47" in out
    assert ".page{padding:64px}" in out
    # Exactly one <style> block — we replaced, not appended.
    assert out.count("<style>") == 1


def test_replaces_style_the_model_emitted_anyway():
    # Even if the model ignores the instruction and emits CSS, canonical wins.
    html = "<head><style>.page{padding:1px}</style></head><body>x</body>"
    out = inject_canonical_css(html, CANON)
    assert ".page{padding:1px}" not in out
    assert ".page{padding:64px}" in out
    assert out.count("<style>") == 1


def test_only_first_style_replaced():
    html = (
        "<head><style>/* marker */</style></head>"
        "<body><svg><style>.x{fill:red}</style></svg></body>"
    )
    out = inject_canonical_css(html, CANON)
    assert "--green:#1A6B47" in out
    # The inline SVG <style> is left untouched.
    assert ".x{fill:red}" in out


def test_inserts_before_head_close_when_no_style():
    html = "<!DOCTYPE html><head><meta charset='utf-8'></head><body>x</body>"
    out = inject_canonical_css(html, CANON)
    assert "<style>" in out
    assert out.index("<style>") < out.index("</head>")
    assert "--green:#1A6B47" in out


def test_prepends_for_headless_fragment():
    # The evidence brief opens with <meta><style>…<div class="wrap"> — no <head>.
    html = '<meta charset="utf-8"><div class="wrap">x</div>'
    out = inject_canonical_css(html, CANON)
    assert out.startswith("<style>")
    assert "--green:#1A6B47" in out
    assert '<div class="wrap">' in out


def test_idempotent():
    html = "<head><style>/* marker */</style></head><body>x</body>"
    once = inject_canonical_css(html, CANON)
    twice = inject_canonical_css(once, CANON)
    assert once == twice


def test_style_with_attributes_is_replaced():
    html = '<head><style type="text/css">/* m */</style></head><body>x</body>'
    out = inject_canonical_css(html, CANON)
    assert "/* m */" not in out
    assert "--green:#1A6B47" in out
    assert out.count("<style") == 1


def test_backslash_in_css_is_literal():
    css = r".x::before{content:'\2022'}"  # a bullet escape — has a backslash
    html = "<head><style></style></head><body>x</body>"
    out = inject_canonical_css(html, css)
    assert r"content:'\2022'" in out
