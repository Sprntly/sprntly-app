"""Shared SSRF guard for user-supplied URLs fed to external fetchers.

Any agent fetch where the host comes from user input (design-agent
``website_url``, competitor/marketing URLs) MUST pass through
:func:`assert_public_url` before the request leaves the process. The guard:

- requires the scheme to be ``http`` or ``https`` (blocks ``file://``,
  ``gopher://``, ``ftp://``, ``data:``, schemeless, etc.);
- resolves the hostname via ``socket.getaddrinfo`` and rejects the URL if ANY
  resolved address is private / loopback / link-local / reserved / multicast /
  unspecified (this catches DNS-rebinding-to-internal and raw-IP targets alike);
- explicitly blocks the cloud-metadata endpoints
  (``169.254.169.254`` and ``metadata.google.internal``).

For redirect-following clients the same check must run on every hop — see
:func:`guarded_redirect_hook` (httpx) and the disable-auto-redirect pattern in
``scraper.fetch_page``.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

__all__ = ["UnsafeURLError", "assert_public_url", "is_blocked_ip"]

_ALLOWED_SCHEMES = {"http", "https"}

# Hostnames that must always be rejected regardless of DNS resolution. The IP
# form is also caught by the link-local check below, but blocking the name too
# defends against resolvers that hand back a non-link-local answer for it.
_BLOCKED_HOSTNAMES = {
    "metadata.google.internal",
    "169.254.169.254",
}


class UnsafeURLError(ValueError):
    """Raised when a URL targets a non-public / disallowed host or scheme."""


def is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if ``ip`` is not a routable public address we are willing to fetch.

    Covers private ranges (RFC 1918 / ULA), loopback, link-local (incl. the
    169.254.0.0/16 cloud-metadata range), reserved, multicast, and the
    unspecified address (0.0.0.0 / ::).
    """
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _resolve_ips(host: str) -> list[str]:
    """Return every IP ``host`` resolves to (raw IPs short-circuit getaddrinfo)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"could not resolve host: {host!r}") from exc
    # sockaddr[0] is the IP string for both AF_INET and AF_INET6.
    return [info[4][0] for info in infos if info[4]]


def assert_public_url(url: str) -> None:
    """Raise :class:`UnsafeURLError` unless ``url`` is a public http(s) target.

    Validates scheme, then resolves the hostname and rejects the URL if any
    resolved address is non-public. Call this immediately before every request
    against a user-influenced URL — and on every redirect hop.
    """
    parts = urlsplit((url or "").strip())
    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(f"scheme not allowed: {scheme!r}")

    host = (parts.hostname or "").strip().rstrip(".").lower()
    if not host:
        raise UnsafeURLError("URL has no host")

    if host in _BLOCKED_HOSTNAMES:
        raise UnsafeURLError(f"host is blocked: {host!r}")

    for ip_str in _resolve_ips(host):
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            # An address we cannot parse is, by definition, not a verified
            # public target — refuse it rather than fetch blind.
            raise UnsafeURLError(f"unparseable resolved address: {ip_str!r}")
        if is_blocked_ip(ip):
            raise UnsafeURLError(
                f"host {host!r} resolves to non-public address {ip_str}"
            )


def guarded_redirect_hook(response):
    """httpx event hook: re-validate the Location target of every redirect.

    Attach via ``event_hooks={"response": [guarded_redirect_hook]}``. httpx
    invokes it on each hop BEFORE following the redirect, so a 3xx pointing at
    an internal host raises :class:`UnsafeURLError` and the redirect is never
    followed. Non-redirect responses pass through untouched.
    """
    if response.is_redirect:
        location = response.headers.get("location")
        if location:
            target = str(response.url.join(location))
            assert_public_url(target)
