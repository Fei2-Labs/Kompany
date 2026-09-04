"""Outbound URL guard for LLM-controlled fetches (SSRF defence).

``web_fetch`` / ``browser_navigate`` take a URL chosen by the model. Without
a guard the model can read the loopback API (which may be unauthenticated),
cloud metadata (``169.254.169.254``), or anything on the founder's LAN.

:func:`check_url` allows only ``http``/``https`` to a hostname that resolves
exclusively to public unicast addresses. Loopback, private (RFC1918),
link-local, multicast, reserved, unspecified and IPv6 ULA/site-local ranges
are refused, as are ``localhost``-style names and IP literals in those
ranges. Redirect targets must be re-checked hop by hop
(:func:`fetch_with_guard`).
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any, Callable
from urllib.parse import urlsplit

MAX_REDIRECTS = 5

_BLOCKED_NAMES: frozenset[str] = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "metadata.google.internal"})
_BLOCKED_SUFFIXES: tuple[str, ...] = (".localhost", ".local", ".internal", ".lan", ".home", ".arpa")


class BlockedURL(ValueError):
    """The URL points somewhere an agent must not reach."""


def _ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    v6 = isinstance(ip, ipaddress.IPv6Address)
    return bool(
        ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast
        or ip.is_reserved or ip.is_unspecified
        or (v6 and (ip.is_site_local or ip.teredo is not None))
        or (not v6 and ip in _CGNAT)
    )


_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def check_url(url: str, *, resolver: Callable[[str], list[str]] | None = None) -> str:
    """Return the URL unchanged when safe; raise :class:`BlockedURL` otherwise."""
    parts = urlsplit((url or "").strip())
    if parts.scheme not in ("http", "https"):
        raise BlockedURL(f"scheme {parts.scheme or '(none)'!r} not allowed; use http(s)")
    if parts.username or parts.password:
        raise BlockedURL("credentials in URL are not allowed")
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise BlockedURL("URL has no host")
    if host in _BLOCKED_NAMES or host.endswith(_BLOCKED_SUFFIXES):
        raise BlockedURL(f"host {host!r} is local/internal")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if _ip_blocked(literal):
            raise BlockedURL(f"address {host} is not a public address")
        return url
    addresses = (resolver or _resolve)(host)
    if not addresses:
        raise BlockedURL(f"host {host!r} does not resolve")
    for addr in addresses:
        try:
            if _ip_blocked(ipaddress.ip_address(addr)):
                raise BlockedURL(f"host {host!r} resolves to non-public address {addr}")
        except ValueError:
            raise BlockedURL(f"host {host!r} resolved to an unparsable address {addr!r}") from None
    return url


def _resolve(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return []
    return sorted({info[4][0] for info in infos})


def fetch_with_guard(url: str, *, client_get: Callable[..., Any], **kwargs: Any) -> Any:
    """GET ``url`` following at most :data:`MAX_REDIRECTS`, guarding each hop.

    ``client_get`` is ``httpx.get``-compatible and is always called with
    ``follow_redirects=False`` so no hop can escape the check.
    """
    current = check_url(url)
    for _ in range(MAX_REDIRECTS + 1):
        resp = client_get(current, follow_redirects=False, **kwargs)
        if resp.status_code not in (301, 302, 303, 307, 308):
            return resp
        location = resp.headers.get("location")
        if not location:
            return resp
        from urllib.parse import urljoin

        current = check_url(urljoin(current, location))
    raise BlockedURL(f"too many redirects (> {MAX_REDIRECTS})")


__all__ = ["BlockedURL", "MAX_REDIRECTS", "check_url", "fetch_with_guard"]
