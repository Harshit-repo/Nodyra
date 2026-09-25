"""Credential connection tests must not become an SSRF primitive (F-25).

"Test connection" fetches a URL that came out of the credential the user just
typed — ``webhook_url`` for a webhook credential, ``base_url`` for a
self-hosted LLM endpoint. Those are attacker-chosen strings, and the request is
made by the API server, from inside the deployment's network.

Every other user-controlled fetch in the product routes through
``assert_public_http_url`` / ``resolve_pinned``; this path used a bare
``httpx.AsyncClient``. An editor could point a credential at
``http://169.254.169.254/…`` and press Test to reach cloud metadata, with the
status code and selected response fields reflected back in the result.
"""

from __future__ import annotations

import pytest

from app.services import credential_tests
from nodyra_nodes.http_security import UnsafeHttpTargetError


@pytest.fixture(autouse=True)
def _block_private_egress(monkeypatch):
    """Multi-tenant posture: private/link-local targets are refused."""
    monkeypatch.setattr(
        credential_tests, "private_egress_allowed", lambda: False, raising=False
    )
    monkeypatch.delenv("NODYRA_ALLOW_PRIVATE_EGRESS", raising=False)
    monkeypatch.delenv("NOODLE_ALLOW_PRIVATE_EGRESS", raising=False)


@pytest.fixture
def no_network(monkeypatch):
    """Fail loudly if a test ever reaches the transport — the guard should
    refuse before any connection is attempted."""

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, *a, **kw):
            raise AssertionError(f"a request escaped the SSRF guard: {a} {kw}")

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "http://127.0.0.1:8000/ops/queue",
        "http://localhost:6379/",
        "http://10.0.0.5/internal",
        "http://192.168.1.1/admin",
        "http://[::1]:8000/",
    ],
    ids=[
        "aws-metadata",
        "loopback-self",
        "loopback-redis",
        "private-10",
        "private-192",
        "loopback-v6",
    ],
)
async def test_private_and_metadata_targets_are_refused(url, no_network):
    with pytest.raises(UnsafeHttpTargetError):
        await credential_tests._request("GET", url)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x/", "ftp://x/"])
async def test_non_http_schemes_are_refused(url, no_network):
    """Scheme validation applies regardless of the egress flag."""
    with pytest.raises(UnsafeHttpTargetError):
        await credential_tests._request("GET", url)


async def test_a_webhook_credential_cannot_reach_metadata(no_network):
    """The realistic path: the URL arrives inside credential data, not as an
    argument, so the guard has to sit in _request rather than at the call site."""
    with pytest.raises(UnsafeHttpTargetError):
        await credential_tests._test_discord(
            {"webhook_url": "http://169.254.169.254/latest/meta-data/"}, {}
        )


async def test_a_public_target_is_still_allowed(monkeypatch):
    """The guard must not break the feature it protects."""
    seen: dict = {}

    class _Response:
        status_code = 200
        text = '{"ok": true}'

        def json(self):
            return {"ok": True}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, url, **kw):
            seen["method"], seen["url"] = method, url
            return _Response()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    result = await credential_tests._request("GET", "https://example.com/health")

    assert result["ok"] is True
    assert seen["method"] == "GET"


async def test_a_self_hosted_deployment_can_opt_back_in(monkeypatch):
    """Single-tenant operators legitimately test an internal endpoint. The
    existing NODYRA_ALLOW_PRIVATE_EGRESS switch must govern this path too,
    rather than a second, separate flag."""
    monkeypatch.setenv("NODYRA_ALLOW_PRIVATE_EGRESS", "1")
    seen: dict = {}

    class _Response:
        status_code = 204
        text = ""

        def json(self):
            raise ValueError("no body")

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, url, **kw):
            seen["url"] = url
            return _Response()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    result = await credential_tests._request("GET", "http://192.168.1.50/health")

    assert seen["url"] == "http://192.168.1.50/health"
    assert result["details"]["status_code"] == 204


async def test_a_hostname_that_resolves_to_a_private_address_is_refused(monkeypatch):
    """DNS is where the real defence happens.

    ``metadata.google.internal`` is not a literal private address — it has to be
    resolved to be judged, and on a GCP host it resolves to 169.254.169.254.
    The guard resolves on every call and refuses if any returned address is
    private, which also closes DNS rebinding.
    """
    import socket

    from nodyra_nodes import http_security

    monkeypatch.setattr(
        http_security.socket,
        "getaddrinfo",
        lambda *a, **kw: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))
        ],
    )
    with pytest.raises(UnsafeHttpTargetError):
        await credential_tests._request(
            "GET", "http://metadata.google.internal/computeMetadata/v1/"
        )


async def test_an_unresolvable_host_is_left_to_the_transport(monkeypatch):
    """Documented behaviour of the shared guard, asserted here so a future
    change to it is a deliberate one: a name that does not resolve cannot reach
    anything, so the connection error is the honest outcome rather than a
    security refusal."""
    import socket

    from nodyra_nodes import http_security

    def _fail(*a, **kw):
        raise socket.gaierror("does not resolve")

    monkeypatch.setattr(http_security.socket, "getaddrinfo", _fail)

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, *a, **kw):
            raise ConnectionError("name does not resolve")

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    with pytest.raises(ConnectionError):
        await credential_tests._request("GET", "http://nonexistent.invalid/x")
