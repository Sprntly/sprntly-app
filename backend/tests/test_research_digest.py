"""Tests for the Research Agent's weekly competitive digest (P0-7).

Coverage:
- Pydantic models reject malformed inputs and enforce length caps.
- App store fetcher correctly parses the iTunes RSS JSON envelope and
  degrades to [] on every error class (HTTP error, JSON decode error,
  schema mismatch, Android store).
- Changelog fetcher parses realistic HTML, falls through candidate
  paths, and degrades on network failure.
- G2 stub returns [] and emits the deprecation log line.
- generate_weekly_digest produces a valid CompetitiveDigest even when
  every source fails, with the "no notable activity" fallback bullet.
- The /v1/research/digest route returns 404 for unknown datasets, 200
  for known ones, and is auth-gated.

Network is mocked at the `requests.get` level — no real I/O.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
import requests
from pydantic import ValidationError

from app.research.digest import generate_weekly_digest
from app.research.models import (
    ChangelogSignal,
    CompetitiveDigest,
    CompetitorPulse,
    ReviewSignal,
)
from app.research.sources.app_store import fetch_recent_reviews
from app.research.sources.changelog import fetch_recent_changelog_items
from app.research.sources.review_sites import fetch_g2_signals


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


def _mock_response(
    *,
    status: int = 200,
    json_data=None,
    text: str = "",
    content_type: str = "application/json",
) -> MagicMock:
    """Build a `requests.Response`-like MagicMock."""
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.headers = {"Content-Type": content_type}
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("no json")
    return resp


_ITUNES_OK = {
    "feed": {
        "entry": [
            # First entry is the app metadata — no rating; should be skipped.
            {
                "title": {"label": "Acme App"},
                "im:name": {"label": "Acme"},
            },
            {
                "im:rating": {"label": "5"},
                "title": {"label": "Great app"},
                "content": {"label": "Love the new dashboard widget."},
                "updated": {"label": "2026-05-24T12:00:00-07:00"},
                "author": {"name": {"label": "happyuser"}},
            },
            {
                "im:rating": {"label": "1"},
                "title": {"label": "Broken after update"},
                "content": {"label": "App crashes on launch. Wants a refund."},
                "updated": {"label": "2026-05-23T08:30:00-07:00"},
                "author": {"name": {"label": "angryuser"}},
            },
            # Missing rating — should be silently skipped, not crash the batch.
            {
                "title": {"label": "no rating here"},
                "content": {"label": "..."},
                "updated": {"label": "2026-05-22T00:00:00-07:00"},
            },
        ]
    }
}


# --------------------------------------------------------------------------
# Pydantic models
# --------------------------------------------------------------------------


class TestModels:
    def test_review_signal_happy_path(self):
        r = ReviewSignal(
            competitor="Acme",
            store="ios",
            rating=4,
            title="Good",
            body="Nice product",
            published_at="2026-05-24T00:00:00Z",
        )
        assert r.rating == 4
        assert r.competitor == "Acme"

    def test_review_signal_rejects_out_of_range_rating(self):
        with pytest.raises(ValidationError):
            ReviewSignal(
                competitor="Acme",
                store="ios",
                rating=7,
                title="x",
                body="y",
                published_at="2026-05-24T00:00:00Z",
            )

    def test_review_signal_rejects_unknown_store(self):
        with pytest.raises(ValidationError):
            ReviewSignal(
                competitor="Acme",
                store="windows",
                rating=4,
                title="x",
                body="y",
                published_at="2026-05-24T00:00:00Z",
            )

    def test_review_signal_truncates_long_body(self):
        long_body = "A" * 2000
        r = ReviewSignal(
            competitor="Acme",
            store="ios",
            rating=3,
            title="t",
            body=long_body,
            published_at="2026-05-24T00:00:00Z",
        )
        # Body must be capped (500 with ellipsis).
        assert len(r.body) <= 500
        assert r.body.endswith("…")

    def test_review_signal_rejects_empty_competitor(self):
        with pytest.raises(ValidationError):
            ReviewSignal(
                competitor="",
                store="ios",
                rating=3,
                title="t",
                body="b",
                published_at="2026-05-24T00:00:00Z",
            )

    def test_changelog_signal_summary_cap(self):
        long_summary = "word " * 500  # well over 280 chars
        c = ChangelogSignal(
            competitor="Acme",
            source="changelog",
            title="v1.2",
            url="https://example.com/changelog#v1.2",
            published_at="2026-05-24T00:00:00Z",
            summary=long_summary,
        )
        assert len(c.summary) <= 280
        assert c.summary.endswith("…")

    def test_changelog_signal_rejects_empty_title(self):
        with pytest.raises(ValidationError):
            ChangelogSignal(
                competitor="Acme",
                source="blog",
                title="",
                url="https://example.com",
            )

    def test_competitor_pulse_defaults(self):
        p = CompetitorPulse(competitor_name="Acme")
        assert p.app_store_signals == []
        assert p.changelog_signals == []
        assert p.review_signals == []
        assert p.notable is False

    def test_competitor_pulse_rejects_empty_name(self):
        with pytest.raises(ValidationError):
            CompetitorPulse(competitor_name="   ")

    def test_competitive_digest_caps_highlights(self):
        # max_length=5 — Pydantic should reject 6+.
        with pytest.raises(ValidationError):
            CompetitiveDigest(
                workspace_id="w1",
                generated_at="2026-05-26T00:00:00Z",
                top_highlights=["a", "b", "c", "d", "e", "f"],
            )


# --------------------------------------------------------------------------
# App store fetcher
# --------------------------------------------------------------------------


class TestAppStore:
    def test_parses_itunes_json_correctly(self):
        with patch(
            "app.research.sources.app_store.requests.get",
            return_value=_mock_response(json_data=_ITUNES_OK),
        ) as m:
            out = fetch_recent_reviews("123456", "ios", competitor="Acme")

        assert m.call_count == 1
        # Two reviews (first entry skipped as metadata, last skipped
        # for missing rating).
        assert len(out) == 2
        first, second = out
        assert first.rating == 5
        assert first.title == "Great app"
        assert first.competitor == "Acme"
        assert first.store == "ios"
        assert "dashboard widget" in first.body
        assert second.rating == 1

    def test_android_is_stubbed_and_logged(self, caplog):
        with caplog.at_level(logging.WARNING, logger="app.research.sources.app_store"):
            out = fetch_recent_reviews("com.example.app", "android", competitor="Acme")
        assert out == []
        assert any("Google Play" in r.message for r in caplog.records)

    def test_http_error_returns_empty(self):
        with patch(
            "app.research.sources.app_store.requests.get",
            return_value=_mock_response(status=503),
        ):
            assert fetch_recent_reviews("123", "ios") == []

    def test_network_exception_returns_empty(self):
        with patch(
            "app.research.sources.app_store.requests.get",
            side_effect=requests.ConnectionError("boom"),
        ):
            assert fetch_recent_reviews("123", "ios") == []

    def test_malformed_json_returns_empty(self):
        with patch(
            "app.research.sources.app_store.requests.get",
            return_value=_mock_response(json_data=None),
        ):
            assert fetch_recent_reviews("123", "ios") == []

    def test_empty_feed_returns_empty(self):
        with patch(
            "app.research.sources.app_store.requests.get",
            return_value=_mock_response(json_data={"feed": {}}),
        ):
            assert fetch_recent_reviews("123", "ios") == []

    def test_single_entry_as_dict_is_handled(self):
        # Apple sometimes returns `entry` as a dict (not list) when
        # there's exactly one review. Make sure we don't crash.
        payload = {
            "feed": {
                "entry": {
                    "im:rating": {"label": "4"},
                    "title": {"label": "ok"},
                    "content": {"label": "fine"},
                    "updated": {"label": "2026-05-25T00:00:00Z"},
                }
            }
        }
        with patch(
            "app.research.sources.app_store.requests.get",
            return_value=_mock_response(json_data=payload),
        ):
            out = fetch_recent_reviews("123", "ios")
        assert len(out) == 1
        assert out[0].rating == 4


# --------------------------------------------------------------------------
# Changelog fetcher
# --------------------------------------------------------------------------


_CHANGELOG_HTML = """
<html><body>
  <main>
    <article>
      <h2>v3.4.0 — Smarter sync</h2>
      <time datetime="2026-05-24T00:00:00Z">May 24, 2026</time>
      <p>Real-time sync between devices, with conflict-free updates.</p>
    </article>
    <article>
      <h2>v3.3.1 — Bug fixes</h2>
      <time datetime="2026-05-17T00:00:00Z">May 17, 2026</time>
      <p>Fixed a crash in the export pipeline.</p>
    </article>
  </main>
</body></html>
"""


class TestChangelog:
    def test_extracts_articles_from_changelog_page(self):
        with patch(
            "app.research.sources.changelog.requests.get",
            return_value=_mock_response(
                status=200,
                text=_CHANGELOG_HTML,
                content_type="text/html; charset=utf-8",
            ),
        ):
            out = fetch_recent_changelog_items(
                "https://example.com/changelog",
                competitor="Acme",
            )
        assert len(out) == 2
        assert out[0].title.startswith("v3.4.0")
        assert out[0].competitor == "Acme"
        assert out[0].published_at == "2026-05-24T00:00:00Z"
        assert out[0].source in {"changelog", "blog", "press"}

    def test_falls_through_to_blog_when_changelog_404s(self):
        responses = {
            "https://example.com/changelog": _mock_response(status=404),
            "https://example.com/releases": _mock_response(status=404),
            "https://example.com/release-notes": _mock_response(status=404),
            "https://example.com/whats-new": _mock_response(status=404),
            "https://example.com/blog": _mock_response(
                status=200,
                text=_CHANGELOG_HTML,
                content_type="text/html",
            ),
        }

        def fake_get(url, **kwargs):  # noqa: ARG001
            return responses.get(url, _mock_response(status=404))

        with patch(
            "app.research.sources.changelog.requests.get",
            side_effect=fake_get,
        ):
            out = fetch_recent_changelog_items(
                "https://example.com",
                competitor="Acme",
            )
        assert len(out) == 2
        # Source should reflect "blog" since that's the path that succeeded.
        assert all(s.source == "blog" for s in out)

    def test_all_paths_404_returns_empty(self):
        with patch(
            "app.research.sources.changelog.requests.get",
            return_value=_mock_response(status=404),
        ):
            out = fetch_recent_changelog_items(
                "https://example.com",
                competitor="Acme",
            )
        assert out == []

    def test_network_error_returns_empty(self):
        with patch(
            "app.research.sources.changelog.requests.get",
            side_effect=requests.Timeout("slow"),
        ):
            assert fetch_recent_changelog_items("https://example.com", competitor="X") == []

    def test_empty_url_returns_empty(self):
        assert fetch_recent_changelog_items("", competitor="X") == []

    def test_caps_at_five_items(self):
        items = "".join(
            f'<article><h2>v{i}</h2><p>Item {i}</p></article>' for i in range(20)
        )
        html = f"<html><body><main>{items}</main></body></html>"
        with patch(
            "app.research.sources.changelog.requests.get",
            return_value=_mock_response(status=200, text=html, content_type="text/html"),
        ):
            out = fetch_recent_changelog_items(
                "https://example.com/changelog",
                competitor="Acme",
            )
        assert len(out) == 5


# --------------------------------------------------------------------------
# G2 stub
# --------------------------------------------------------------------------


class TestG2Stub:
    def test_returns_empty(self):
        assert fetch_g2_signals("Acme", g2_slug="acme") == []

    def test_logs_deferred_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="app.research.sources.review_sites"):
            fetch_g2_signals("Acme")
        assert any("G2 integration deferred" in r.message for r in caplog.records)


# --------------------------------------------------------------------------
# Digest aggregator
# --------------------------------------------------------------------------


class TestDigestAggregator:
    def test_empty_competitors_produces_valid_digest(self):
        d = generate_weekly_digest("workspace-1", [])
        # Don't isinstance-check — the isolated_settings fixture reloads
        # modules, which makes the top-level CompetitiveDigest reference
        # stale between tests. Compare by class name instead.
        assert type(d).__name__ == "CompetitiveDigest"
        assert d.workspace_id == "workspace-1"
        assert d.pulses == []
        assert len(d.top_highlights) == 1
        assert "No notable" in d.top_highlights[0]

    def test_skips_malformed_entries(self):
        # Each of these should be silently dropped rather than raising.
        d = generate_weekly_digest(
            "w1",
            [
                {"url": "https://x.com"},   # no name
                {"name": ""},               # empty name
                "not even a dict",          # type: ignore[list-item]
                {"name": "Real Competitor"},
            ],
        )
        assert len(d.pulses) == 1
        assert d.pulses[0].competitor_name == "Real Competitor"

    def test_resilience_when_every_source_fails(self):
        # `requests` is the same module object in both changelog.py
        # and app_store.py, so a single patch on `requests.get`
        # routes both — patching them independently would clobber.
        with patch(
            "requests.get",
            side_effect=requests.ConnectionError("dead"),
        ):
            d = generate_weekly_digest(
                "ws",
                [
                    {
                        "name": "Acme",
                        "url": "https://acme.com",
                        "ios_app_id": "111",
                    },
                ],
            )
        assert len(d.pulses) == 1
        p = d.pulses[0]
        assert p.competitor_name == "Acme"
        assert p.app_store_signals == []
        assert p.changelog_signals == []
        assert p.review_signals == []
        assert p.notable is False

    def test_changelog_signals_promote_to_notable(self):
        # URL-aware router: changelog calls get HTML, iTunes calls get empty feed.
        def routed_get(url, **kwargs):  # noqa: ARG001
            if "itunes.apple.com" in url:
                return _mock_response(json_data={"feed": {}})
            return _mock_response(
                status=200, text=_CHANGELOG_HTML, content_type="text/html"
            )

        with patch("requests.get", side_effect=routed_get):
            d = generate_weekly_digest(
                "ws",
                [{"name": "Acme", "url": "https://acme.com/changelog"}],
            )
        assert len(d.pulses) == 1
        assert d.pulses[0].notable is True
        # Highlights should now mention the shipped feature, not the
        # "no activity" placeholder.
        assert all("No notable" not in h for h in d.top_highlights)
        assert any("Acme" in h for h in d.top_highlights)

    def test_highlights_capped_at_five(self):
        items = "".join(
            f'<article><h2>v{i}</h2><p>release {i}</p></article>' for i in range(20)
        )
        html = f"<html><body><main>{items}</main></body></html>"
        with patch(
            "requests.get",
            return_value=_mock_response(status=200, text=html, content_type="text/html"),
        ):
            d = generate_weekly_digest(
                "ws",
                [
                    {"name": "A", "url": "https://a.com/changelog"},
                    {"name": "B", "url": "https://b.com/changelog"},
                ],
            )
        assert len(d.top_highlights) <= 5


# --------------------------------------------------------------------------
# HTTP route
# --------------------------------------------------------------------------


class TestRoute:
    def test_404_for_unknown_dataset(self, app_client):
        r = app_client.get("/v1/research/digest?dataset=does-not-exist")
        assert r.status_code == 404

    def test_dataset_param_required(self, app_client):
        r = app_client.get("/v1/research/digest")
        assert r.status_code == 422

    def test_200_with_empty_digest_when_competitors_unconfigured(
        self, app_client, isolated_settings
    ):
        db = isolated_settings["db"]
        db.insert_dataset("acme", "Acme")

        r = app_client.get("/v1/research/digest?dataset=acme")
        assert r.status_code == 200
        body = r.json()
        assert body["workspace_id"] == "acme"
        assert body["pulses"] == []
        # Highlights always populated with at least the fallback.
        assert len(body["top_highlights"]) >= 1

    def test_route_requires_auth(self, unauth_client):
        r = unauth_client.get("/v1/research/digest?dataset=acme")
        assert r.status_code == 401
