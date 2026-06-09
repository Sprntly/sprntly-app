"""SSRF guard tests for app.net_guard and its call sites.

All DNS resolution and HTTP/browser I/O is mocked — these tests never touch the
network. They assert that the guard rejects internal/loopback/link-local/
metadata/reserved targets and non-http schemes, allows normal public URLs, and
that each protected fetch site (scraper.fetch_page, WebExtractor.current_version,
website extractor) refuses an unsafe URL including a redirect to an internal host.
"""
from __future__ import annotations

import socket
from unittest.mock import MagicMock, patch

import pytest

from app.net_guard import UnsafeURLError, assert_public_url


def _mock_getaddrinfo(ip: str):
    """Return a getaddrinfo replacement that resolves any host to ``ip``."""
    def _fake(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]
    return _fake


# ─────────────────────── assert_public_url: rejects ───────────────────────


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/x",
    "gopher://example.com/",
    "data:text/plain,hi",
    "//example.com/no-scheme",
    "example.com/no-scheme",
])
def test_rejects_non_http_schemes(url):
    with pytest.raises(UnsafeURLError):
        assert_public_url(url)


def test_rejects_cloud_metadata_ip():
    # 169.254.0.0/16 is link-local; also explicitly blacklisted by hostname.
    with pytest.raises(UnsafeURLError):
        assert_public_url("http://169.254.169.254/latest/meta-data/")


def test_rejects_gcp_metadata_hostname():
    with patch("app.net_guard.socket.getaddrinfo", _mock_getaddrinfo("8.8.8.8")):
        # Even when DNS hands back a public IP, the hostname is blocked.
        with pytest.raises(UnsafeURLError):
            assert_public_url("http://metadata.google.internal/")


def test_rejects_localhost_name():
    with patch("app.net_guard.socket.getaddrinfo", _mock_getaddrinfo("127.0.0.1")):
        with pytest.raises(UnsafeURLError):
            assert_public_url("http://localhost/")


@pytest.mark.parametrize("ip", [
    "127.0.0.1",      # loopback
    "10.0.0.5",       # private (RFC1918)
    "192.168.1.10",   # private
    "172.16.5.5",     # private
    "0.0.0.0",        # unspecified
    "169.254.1.1",    # link-local
    "224.0.0.1",      # multicast
    "240.0.0.1",      # reserved
])
def test_rejects_raw_internal_ip(ip):
    with patch("app.net_guard.socket.getaddrinfo", _mock_getaddrinfo(ip)):
        with pytest.raises(UnsafeURLError):
            assert_public_url(f"http://host.example/")


def test_rejects_hostname_resolving_to_private_ip():
    # DNS-rebinding style: benign-looking host, private answer.
    with patch("app.net_guard.socket.getaddrinfo", _mock_getaddrinfo("10.1.2.3")):
        with pytest.raises(UnsafeURLError):
            assert_public_url("http://totally-legit.example.com/page")


def test_rejects_unresolvable_host():
    def _boom(host, *a, **k):
        raise socket.gaierror("nope")
    with patch("app.net_guard.socket.getaddrinfo", _boom):
        with pytest.raises(UnsafeURLError):
            assert_public_url("http://does-not-exist.invalid/")


def test_rejects_ipv6_loopback():
    with patch("app.net_guard.socket.getaddrinfo", _mock_getaddrinfo("::1")):
        with pytest.raises(UnsafeURLError):
            assert_public_url("http://[::1]/")


# ─────────────────────── assert_public_url: allows ───────────────────────


def test_allows_public_http_url():
    with patch("app.net_guard.socket.getaddrinfo", _mock_getaddrinfo("93.184.216.34")):
        assert_public_url("http://example.com/")  # no raise


def test_allows_public_https_url_with_path_and_port():
    with patch("app.net_guard.socket.getaddrinfo", _mock_getaddrinfo("8.8.8.8")):
        assert_public_url("https://example.com:8443/pricing?ref=1")  # no raise


# ─────────────────────── scraper.fetch_page integration ───────────────────────


@pytest.mark.asyncio
async def test_fetch_page_blocks_unsafe_url_no_request():
    """An unsafe URL returns '' and never opens an httpx client."""
    from app.agents import scraper

    with patch("app.net_guard.socket.getaddrinfo", _mock_getaddrinfo("10.0.0.1")):
        with patch.object(scraper.httpx, "AsyncClient") as client_cls:
            out = await scraper.fetch_page("http://internal.example/")
    assert out == ""
    client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_page_blocks_redirect_to_internal_host():
    """A 3xx pointing at an internal host is refused before the next hop."""
    from app.agents import scraper

    # Public first hop, then a redirect to a private host.
    redirect_resp = MagicMock()
    redirect_resp.is_redirect = True
    redirect_resp.headers = {"location": "http://10.0.0.9/secret"}
    redirect_resp.url = MagicMock()
    redirect_resp.url.join.return_value = "http://10.0.0.9/secret"

    fake_client = MagicMock()

    async def _get(url):
        return redirect_resp

    fake_client.get = _get
    cm = MagicMock()
    cm.__aenter__ = _amock(fake_client)
    cm.__aexit__ = _amock(False)

    def _resolve(host, *a, **k):
        # First host public, redirect host private.
        ip = "10.0.0.9" if host == "10.0.0.9" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    with patch("app.net_guard.socket.getaddrinfo", _resolve):
        with patch.object(scraper.httpx, "AsyncClient", return_value=cm):
            out = await scraper.fetch_page("http://public.example/")
    assert out == ""


@pytest.mark.asyncio
async def test_fetch_page_allows_public_and_returns_text():
    """A public URL with a 200 HTML body returns extracted text."""
    from app.agents import scraper

    ok_resp = MagicMock()
    ok_resp.is_redirect = False
    ok_resp.status_code = 200
    ok_resp.text = "<html><body><p>Hello world</p></body></html>"

    fake_client = MagicMock()

    async def _get(url):
        return ok_resp

    fake_client.get = _get
    cm = MagicMock()
    cm.__aenter__ = _amock(fake_client)
    cm.__aexit__ = _amock(False)

    with patch("app.net_guard.socket.getaddrinfo", _mock_getaddrinfo("93.184.216.34")):
        with patch.object(scraper.httpx, "AsyncClient", return_value=cm):
            out = await scraper.fetch_page("http://example.com/")
    assert "Hello world" in out


def _amock(return_value):
    """Build an async function returning ``return_value`` (for __aenter__/__aexit__)."""
    async def _f(*a, **k):
        return return_value
    return _f


# ─────────────────────── WebExtractor.current_version integration ───────────────────────


def test_web_extractor_current_version_blocks_internal():
    """current_version returns None (no HEAD) for an internal URL."""
    from app.design_agent.design_system.adapters import WebExtractor

    with patch("app.net_guard.socket.getaddrinfo", _mock_getaddrinfo("127.0.0.1")):
        from app.connectors import figma_oauth
        with patch.object(figma_oauth.requests, "head") as head:
            result = WebExtractor().current_version("http://localhost/")
    assert result is None
    head.assert_not_called()


def test_web_extractor_current_version_allows_public():
    """A public URL is HEAD-ed and the ETag marker is returned."""
    from app.design_agent.design_system.adapters import WebExtractor
    from app.connectors import figma_oauth

    resp = MagicMock()
    resp.ok = True
    resp.is_redirect = False
    resp.headers = {"ETag": '"abc123"'}

    with patch("app.net_guard.socket.getaddrinfo", _mock_getaddrinfo("93.184.216.34")):
        with patch.object(figma_oauth.requests, "head", return_value=resp) as head:
            result = WebExtractor().current_version("https://example.com/")
    assert result == '"abc123"'
    head.assert_called_once()


# ─────────────────────── website extractor integration ───────────────────────


@pytest.mark.asyncio
async def test_website_extractor_blocks_internal_url():
    """extract_website_design_system returns None and never launches Chromium
    for an internal URL."""
    from app.design_agent.scenarios import website

    with patch("app.net_guard.socket.getaddrinfo", _mock_getaddrinfo("169.254.169.254")):
        with patch.object(website, "_resolve_async_playwright") as resolve:
            out = await website.extract_website_design_system(
                "http://169.254.169.254/"
            )
    assert out is None
    resolve.assert_not_called()
