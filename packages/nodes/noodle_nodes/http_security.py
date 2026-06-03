"""Runtime URL safety checks for HTTP-capable nodes."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeHttpTargetError(ValueError):
    """Raised when an HTTP node targets a private or unsupported URL."""


def _is_private_address(value: str) -> bool:
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        return False
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _hostname_is_private(hostname: str) -> bool:
    host = hostname.strip().lower()
    if not host:
        return False
    if host in {"localhost", "broadcasthost"}:
        return True
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return _is_private_address(host)


def assert_public_http_url(url: str, *, context: str = "HTTP request") -> None:
    """Reject URLs that can reach local/private network resources.

    DNS resolution is best-effort: literal private hosts are always blocked.
    When DNS resolves, every returned address must be public. If DNS lookup
    fails, the request layer is allowed to surface the normal connection error.
    """
    raw = str(url or "").strip()
    if not raw:
        raise UnsafeHttpTargetError(f"{context}: url is required")
    try:
        parsed = urlparse(raw)
    except ValueError as exc:
        raise UnsafeHttpTargetError(f"{context}: invalid url") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise UnsafeHttpTargetError(
            f"{context}: only http and https URLs are allowed"
        )
    hostname = parsed.hostname or ""
    if not hostname:
        raise UnsafeHttpTargetError(f"{context}: url must include a hostname")
    if _hostname_is_private(hostname):
        raise UnsafeHttpTargetError(
            f"{context}: private, loopback, or link-local HTTP targets are blocked"
        )
    try:
        infos = socket.getaddrinfo(hostname, parsed.port, type=socket.SOCK_STREAM)
    except OSError:
        return
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        address = str(sockaddr[0])
        if _is_private_address(address):
            raise UnsafeHttpTargetError(
                f"{context}: hostname resolves to a private, loopback, "
                "or link-local address"
            )


__all__ = ["UnsafeHttpTargetError", "assert_public_http_url"]
