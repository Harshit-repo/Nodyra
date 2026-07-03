"""SSRF hardening for httpx egress: pin connections to pre-validated IPs."""
from __future__ import annotations

import ipaddress
import socket
from typing import Any, NamedTuple
from urllib.parse import urlsplit, urlunsplit

from nodyra_nodes.http_security import (
    UnsafeHttpTargetError,
    assert_public_http_url,
    private_egress_allowed,
)


class PrivateAddressError(UnsafeHttpTargetError):
    """Raised when a hostname resolves to a non-public address."""


class PinnedURL(NamedTuple):
    url: str
    host: str


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _netloc_for_ip(ip: str, port: int) -> str:
    return f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"


def resolve_pinned(url: str, *, context: str = "HTTP request") -> PinnedURL:
    """Validate *url* and return a URL whose host is a validated IP literal."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if private_egress_allowed():
        return PinnedURL(url=url, host=host)

    try:
        assert_public_http_url(url, context=context)
    except UnsafeHttpTargetError as exc:
        msg = str(exc).lower()
        if "private" in msg or "loopback" in msg or "link-local" in msg:
            raise PrivateAddressError(str(exc)) from exc
        raise
    port = parts.port or (443 if parts.scheme == "https" else 80)

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        netloc = _netloc_for_ip(host, port)
        return PinnedURL(
            url=urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment)),
            host=host,
        )

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        # Let httpx surface the normal DNS/connect failure. There is no second
        # successful resolution to rebind when the pre-connect resolution failed.
        return PinnedURL(url=url, host=host)

    addrs = sorted({info[4][0] for info in infos if info and info[4]})
    if not addrs:
        return PinnedURL(url=url, host=host)

    for addr in addrs:
        ip = ipaddress.ip_address(addr.split("%")[0])
        if not _is_public(ip):
            raise PrivateAddressError(
                f"{context}: {host!r} resolves to non-public address {addr}"
            )

    pinned_ip = addrs[0]
    netloc = _netloc_for_ip(pinned_ip, port)
    return PinnedURL(
        url=urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment)),
        host=host,
    )


def pinned_request_kwargs(pinned: PinnedURL) -> dict[str, Any]:
    """Per-request kwargs that preserve the original hostname for Host/SNI."""
    return {
        "headers": {"Host": pinned.host},
        "extensions": {"sni_hostname": pinned.host},
    }
