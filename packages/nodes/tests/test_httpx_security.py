"""BUG-8: pin httpx egress to the pre-validated public IP."""
from __future__ import annotations

import socket

import pytest

from nodyra_nodes.httpx_security import (
    PrivateAddressError,
    pinned_request_kwargs,
    resolve_pinned,
)


@pytest.fixture(autouse=True)
def _blocked_egress_posture(monkeypatch) -> None:
    """These tests verify the BLOCKED posture; single-tenant API processes
    default to allowing private egress (mirroring workers), so pin the env."""
    monkeypatch.setenv("NODYRA_ALLOW_PRIVATE_EGRESS", "0")


def _fake_getaddrinfo(addrs: list[str]):
    def fake(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, port)) for addr in addrs]

    return fake


def test_resolve_pinned_rewrites_host_to_validated_ip(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["93.184.216.34"]))
    pinned = resolve_pinned("https://mcp.example.com/rpc?x=1", context="test")
    assert pinned.url == "https://93.184.216.34:443/rpc?x=1"
    assert pinned.host == "mcp.example.com"


def test_resolve_pinned_rejects_private_resolution(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["10.0.0.5"]))
    with pytest.raises(PrivateAddressError):
        resolve_pinned("https://rebind.example.com/", context="test")


def test_resolve_pinned_rejects_mixed_public_private(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_getaddrinfo(["93.184.216.34", "169.254.169.254"]),
    )
    with pytest.raises(PrivateAddressError):
        resolve_pinned("https://rebind.example.com/", context="test")


def test_ip_literal_url_passes_through() -> None:
    pinned = resolve_pinned("https://93.184.216.34/x", context="test")
    assert pinned.url == "https://93.184.216.34:443/x"
    assert pinned.host == "93.184.216.34"


def test_private_egress_env_disables_pinning(monkeypatch) -> None:
    monkeypatch.setenv("NODYRA_ALLOW_PRIVATE_EGRESS", "1")
    pinned = resolve_pinned("http://10.0.0.5:8080/local", context="test")
    assert pinned.url == "http://10.0.0.5:8080/local"


def test_pinned_request_kwargs_carries_host_and_sni(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo(["93.184.216.34"]))
    pinned = resolve_pinned("https://mcp.example.com/", context="test")
    kwargs = pinned_request_kwargs(pinned)
    assert kwargs["headers"]["Host"] == "mcp.example.com"
    assert kwargs["extensions"]["sni_hostname"] == "mcp.example.com"
