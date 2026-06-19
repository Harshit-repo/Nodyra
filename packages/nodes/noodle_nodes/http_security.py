"""Runtime URL safety checks for HTTP-capable nodes."""

from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin, urlparse, urlsplit


class UnsafeHttpTargetError(ValueError):
    """Raised when an HTTP node targets a private or unsupported URL."""


# Env-driven egress policy. The nodes package deliberately does not import
# ``app.config`` (it runs in the isolated runtime subprocess), so the policy is
# read from the environment, which the worker propagates into the runtime.
_ALLOW_PRIVATE_ENV = "NOODLE_ALLOW_PRIVATE_EGRESS"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def private_egress_allowed() -> bool:
    """Whether node HTTP/DB egress may target private/loopback/link-local hosts.

    Default ``False`` — the safe choice for multi-tenant hosted deployments,
    where a tenant must never reach internal services or cloud metadata
    (``169.254.169.254``). Single-tenant / self-hosted operators who
    legitimately call internal targets (self-hosted GitLab, an internal API,
    a VPC database) set ``NOODLE_ALLOW_PRIVATE_EGRESS=1`` to opt out.

    Scheme validation (http/https only) always applies regardless of this flag.
    """
    return os.environ.get(_ALLOW_PRIVATE_ENV, "").strip().lower() in _TRUTHY


# 3xx statuses that carry a Location header we must re-validate before following.
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
# Per RFC 7231 these redirects rewrite the method to GET (and drop the body).
_REDIRECT_TO_GET = frozenset({301, 302, 303})
# Headers that carry credentials and must never follow a redirect to a
# different origin (mirrors ``requests.Session.rebuild_auth`` semantics).
_SENSITIVE_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie"})


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


def assert_public_host(
    host: str, port: int | None = None, *, context: str = "connection"
) -> None:
    """Reject a network host that is, or resolves to, a private/loopback/
    link-local address.

    The scheme-agnostic core shared by HTTP nodes and non-HTTP egress (database
    connection nodes). Respects the ``NOODLE_ALLOW_PRIVATE_EGRESS`` opt-out so a
    single-tenant operator can reach an internal/VPC host (SEC-4).
    """
    name = (host or "").strip()
    if not name:
        raise UnsafeHttpTargetError(f"{context}: host is required")
    if private_egress_allowed():
        return
    if _hostname_is_private(name):
        raise UnsafeHttpTargetError(
            f"{context}: private, loopback, or link-local hosts are blocked"
        )
    try:
        infos = socket.getaddrinfo(name, port, type=socket.SOCK_STREAM)
    except OSError:
        # DNS failure — let the connection layer surface the normal error.
        return
    for info in infos:
        sockaddr = info[4]
        if sockaddr and _is_private_address(str(sockaddr[0])):
            raise UnsafeHttpTargetError(
                f"{context}: host resolves to a private, loopback, "
                "or link-local address"
            )


def assert_public_http_url(url: str, *, context: str = "HTTP request") -> None:
    """Reject URLs that can reach local/private network resources.

    DNS resolution is best-effort: literal private hosts are always blocked.
    When DNS resolves, every returned address must be public. If DNS lookup
    fails, the request layer is allowed to surface the normal connection error.

    Resolution is performed fresh on every call. We deliberately do NOT cache
    the public/private verdict: caching it would let a DNS-rebinding attacker
    flip a record to a private/metadata IP after a benign first lookup and have
    that stale "public" verdict honoured for the cache lifetime — widening the
    very rebinding window ``safe_request`` re-validates each hop to narrow.
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
    # Scheme validation above always applies; private-host blocking (and its
    # opt-out) is shared with non-HTTP egress via assert_public_host (SEC-3/4).
    assert_public_host(hostname, parsed.port, context=context)


def _redirect_strips_credentials(old_url: str, new_url: str) -> bool:
    """True when following ``old_url`` -> ``new_url`` must drop auth headers.

    Credentials leak if the redirect target is a different host, or downgrades
    an ``https`` request to plaintext ``http`` on the same host.
    """
    old = urlsplit(old_url)
    new = urlsplit(new_url)
    if (old.hostname or "").lower() != (new.hostname or "").lower():
        return True
    return old.scheme.lower() == "https" and new.scheme.lower() != "https"


def _strip_sensitive_headers(body_kwargs: dict[str, Any]) -> None:
    headers = body_kwargs.get("headers")
    if not headers:
        return
    body_kwargs["headers"] = {
        key: value
        for key, value in dict(headers).items()
        if key.lower() not in _SENSITIVE_HEADERS
    }


def safe_request(
    method: str,
    url: str,
    *,
    request_fn: Callable[..., Any] | None = None,
    max_redirects: int = 5,
    context: str = "HTTP request",
    **kwargs: Any,
) -> Any:
    """Perform an HTTP request with SSRF-safe redirect handling (C2).

    ``requests`` follows redirects automatically and never re-checks the hop
    target, so a public URL that 302-redirects to ``169.254.169.254`` or an
    internal host would defeat a one-shot pre-check. This helper disables
    automatic redirects and re-validates *every* hop with
    :func:`assert_public_http_url` before issuing it. ``request_fn`` is injected
    for testing; in production it defaults to ``requests.request``.

    Credentials (``Authorization``/``Cookie``) are stripped when a redirect
    crosses to a different host or downgrades https->http, so a Location chosen
    by the origin server can never exfiltrate the caller's secrets to another
    host — matching ``requests.Session.rebuild_auth``.

    Residual risk: a sub-second DNS-rebinding attacker can still flip a record
    between validation and connect. ``assert_public_http_url`` validates *all*
    resolved addresses on every hop to narrow that window; true pinning requires
    a custom transport and multi-tenant deployments must additionally rely on
    ``sandbox_network`` egress isolation.
    """
    if request_fn is None:
        import requests  # imported lazily — keeps the module import-light

        request_fn = requests.request

    current_url = url
    current_method = method
    body_kwargs = dict(kwargs)
    for _ in range(max_redirects + 1):
        assert_public_http_url(current_url, context=context)
        response = request_fn(
            current_method, current_url, allow_redirects=False, **body_kwargs
        )
        status = getattr(response, "status_code", None)
        headers = getattr(response, "headers", None) or {}
        location = headers.get("location") or headers.get("Location")
        if status in _REDIRECT_STATUSES and location:
            next_url = urljoin(current_url, location)
            # Drop credentials before crossing to a different origin.
            if _redirect_strips_credentials(current_url, next_url):
                _strip_sensitive_headers(body_kwargs)
            current_url = next_url
            if status in _REDIRECT_TO_GET:
                current_method = "GET"
                # Drop the request body on a method-rewriting redirect.
                for key in ("json", "data", "files"):
                    body_kwargs.pop(key, None)
            continue
        return response
    raise UnsafeHttpTargetError(f"{context}: too many redirects (>{max_redirects})")


__all__ = [
    "UnsafeHttpTargetError",
    "assert_public_host",
    "assert_public_http_url",
    "private_egress_allowed",
    "safe_request",
]
