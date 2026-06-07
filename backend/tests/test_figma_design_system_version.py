from __future__ import annotations

from app.connectors import figma_oauth
from app.design_agent.design_system.adapters import FigmaExtractor


class _FakeResp:
    def __init__(self, payload: dict, ok: bool = True):
        self.ok = ok
        self._payload = payload

    def json(self):
        return self._payload


def test_figma_current_version_reads_meta_endpoint_shape(monkeypatch):
    # The real /meta endpoint nests its fields under a "file" object and names
    # the timestamp "last_touched_at".
    calls: list[str] = []

    def _fake_get(url, **kwargs):
        calls.append(url)
        assert kwargs["headers"] == {"Authorization": "Bearer fig-token"}
        assert kwargs["timeout"] == 10
        return _FakeResp(
            {"file": {"last_touched_at": "2026-06-07T12:34:56Z", "version": "999"}}
        )

    monkeypatch.setattr(figma_oauth.requests, "get", _fake_get)

    extractor = FigmaExtractor()
    extractor.access_token = "fig-token"

    assert extractor.current_version("file-key") == "2026-06-07T12:34:56Z"
    # Only the cheap meta endpoint is hit — never the full file or nodes tree.
    assert calls == ["https://api.figma.com/v1/files/file-key/meta"]
    assert "https://api.figma.com/v1/files/file-key" not in calls
    assert "https://api.figma.com/v1/files/file-key/nodes" not in calls


def test_figma_current_version_falls_back_to_top_level_last_modified(monkeypatch):
    # The full-file endpoint shape exposes "lastModified" at the top level; the
    # marker resolution must still pick it up.
    def _fake_get(url, **kwargs):
        return _FakeResp({"lastModified": "2026-06-07T12:34:56Z"})

    monkeypatch.setattr(figma_oauth.requests, "get", _fake_get)

    extractor = FigmaExtractor()
    extractor.access_token = "fig-token"

    assert extractor.current_version("file-key") == "2026-06-07T12:34:56Z"


def test_figma_current_version_none_when_no_marker(monkeypatch):
    def _fake_get(url, **kwargs):
        return _FakeResp({"file": {"name": "Untitled"}})

    monkeypatch.setattr(figma_oauth.requests, "get", _fake_get)

    extractor = FigmaExtractor()
    extractor.access_token = "fig-token"

    assert extractor.current_version("file-key") is None
