"""The webhook ip_allowlist must be decided on the real socket peer.

uvicorn ships with ``--proxy-headers`` on and trusts X-Forwarded-For from
127.0.0.1, rewriting ``scope["client"]`` before the app runs and keeping no
copy of the original. A caller on loopback could therefore claim any address
and pass an allowlist — even with the node's own ``trust_proxy`` off, which is
the setting that says not to believe that header.
"""

import pytest

from app.services.triggers import _webhook_ip_allowed

ALLOWLIST = {"ip_allowlist": "203.0.113.0/24", "trust_proxy": "off"}
XFF_SPOOF = {"X-Forwarded-For": "203.0.113.9"}


@pytest.fixture
def proxy_headers(monkeypatch):
    """Pretend the server was started the unsafe way."""

    def _set(enabled: bool):
        monkeypatch.setattr(
            "app.state.proxy_headers_enabled", lambda: enabled, raising=True
        )

    return _set


def test_allowlist_permits_a_caller_inside_the_range(proxy_headers) -> None:
    proxy_headers(False)

    assert _webhook_ip_allowed(ALLOWLIST, "203.0.113.9", {}) is True


def test_allowlist_refuses_a_caller_outside_the_range(proxy_headers) -> None:
    proxy_headers(False)

    assert _webhook_ip_allowed(ALLOWLIST, "127.0.0.1", {}) is False


def test_a_spoofed_header_cannot_grant_access(proxy_headers) -> None:
    """With proxy headers off, the header is ignored — the peer decides."""
    proxy_headers(False)

    assert _webhook_ip_allowed(ALLOWLIST, "127.0.0.1", XFF_SPOOF) is False


def test_a_spoofed_header_cannot_revoke_access(proxy_headers) -> None:
    proxy_headers(False)

    assert (
        _webhook_ip_allowed(ALLOWLIST, "203.0.113.9", {"X-Forwarded-For": "8.8.8.8"})
        is True
    )


def test_allowlist_fails_closed_when_the_address_may_be_forged(proxy_headers) -> None:
    """The peer is unrecoverable once uvicorn rewrites it, so refuse.

    Deciding on a possibly-forged address is worse than refusing: the operator
    asked for an allowlist, and a closed door plus a log line is recoverable
    where a silent bypass is not.
    """
    proxy_headers(True)

    assert _webhook_ip_allowed(ALLOWLIST, "127.0.0.1", XFF_SPOOF) is False
    assert _webhook_ip_allowed(ALLOWLIST, "203.0.113.9", XFF_SPOOF) is False


def test_no_allowlist_is_unaffected_by_the_launch_flags(proxy_headers) -> None:
    """A node with no allowlist never consults the address at all."""
    proxy_headers(True)

    assert _webhook_ip_allowed({"ip_allowlist": ""}, "127.0.0.1", XFF_SPOOF) is True


def test_trust_proxy_on_still_reads_the_header(proxy_headers) -> None:
    """An operator who has a real proxy in front opts in per node."""
    proxy_headers(True)
    params = {"ip_allowlist": "203.0.113.0/24", "trust_proxy": "on"}

    assert _webhook_ip_allowed(params, "127.0.0.1", XFF_SPOOF) is True
    assert (
        _webhook_ip_allowed(params, "127.0.0.1", {"X-Forwarded-For": "8.8.8.8"}) is False
    )


def test_an_unparseable_allowlist_fails_closed(proxy_headers) -> None:
    proxy_headers(False)
    params = {"ip_allowlist": "not-an-address", "trust_proxy": "off"}

    assert _webhook_ip_allowed(params, "203.0.113.9", {}) is False
