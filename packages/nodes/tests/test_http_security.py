"""Tests for runtime HTTP safety (SSRF guard + redirect re-validation, C2)."""

from __future__ import annotations

import pytest

from nodyra_nodes.http_security import (
    UnsafeHttpTargetError,
    assert_public_host,
    assert_public_http_url,
    safe_request,
)


class _FakeResp:
    def __init__(self, status_code: int, headers: dict | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}


@pytest.mark.parametrize(
    "target,keep_credentials",
    [
        ("https://api.example.test:8443/result", False),
        ("http://api.example.test/result", False),
        ("https://api.example.test:443/result", True),
        ("https://api.example.test/result", True),
    ],
)
def test_redirect_credentials_are_scoped_to_origin(target, keep_credentials):
    calls = []

    def request(method, url, **kwargs):
        calls.append(kwargs.get("headers", {}))
        return _FakeResp(302, {"location": target}) if len(calls) == 1 else _FakeResp(200)

    safe_request(
        "GET",
        "https://api.example.test/start",
        request_fn=request,
        headers={
            "Authorization": "Bearer synthetic",
            "Cookie": "session=synthetic",
            "Accept": "application/json",
        },
    )
    assert ("Authorization" in calls[1]) is keep_credentials
    assert ("Cookie" in calls[1]) is keep_credentials
    assert calls[1]["Accept"] == "application/json"


def test_literal_private_targets_are_blocked() -> None:
    for url in (
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:8000/internal",
        "http://127.0.0.1/",
        "http://10.0.0.5/",
    ):
        with pytest.raises(UnsafeHttpTargetError):
            assert_public_http_url(url)


def test_assert_public_host_blocks_private_literal() -> None:
    """SEC-4: non-HTTP egress (database hosts) reuses the same private-host
    blocking as HTTP."""
    for host in ("10.0.0.5", "127.0.0.1", "localhost", "169.254.169.254"):
        with pytest.raises(UnsafeHttpTargetError):
            assert_public_host(host, 5432, context="postgres")


def test_assert_public_host_opt_out(monkeypatch) -> None:
    """SEC-4: the same env opt-out lets a self-hosted instance reach an internal
    database."""
    monkeypatch.setenv("NODYRA_ALLOW_PRIVATE_EGRESS", "1")
    assert_public_host("10.0.0.5", 5432, context="postgres")  # no raise


def test_private_egress_allowed_by_env_opt_out(monkeypatch) -> None:
    """SEC-3: self-hosted operators can opt out of private-host blocking so
    legitimate internal targets (self-hosted GitLab, Ollama, internal APIs)
    keep working. The default (env unset) still blocks."""
    # Default: blocked.
    monkeypatch.delenv("NODYRA_ALLOW_PRIVATE_EGRESS", raising=False)
    with pytest.raises(UnsafeHttpTargetError):
        assert_public_http_url("http://10.0.0.5/internal")

    # Opt-out: allowed.
    monkeypatch.setenv("NODYRA_ALLOW_PRIVATE_EGRESS", "1")
    assert_public_http_url("http://10.0.0.5/internal")  # no raise


def test_private_egress_opt_out_still_rejects_non_http_schemes(monkeypatch) -> None:
    """SEC-3: the opt-out relaxes private-IP blocking only — scheme validation
    (http/https only) always applies, so file://, gopher:// etc. stay blocked."""
    monkeypatch.setenv("NODYRA_ALLOW_PRIVATE_EGRESS", "1")
    with pytest.raises(UnsafeHttpTargetError):
        assert_public_http_url("file:///etc/passwd")
    with pytest.raises(UnsafeHttpTargetError):
        assert_public_http_url("gopher://10.0.0.5/")


def test_redirect_to_private_target_is_blocked() -> None:
    """A public URL that 302-redirects to a private host must NOT be followed."""
    calls: list[str] = []

    def fake_request(method, url, **kwargs):
        calls.append(url)
        assert kwargs.get("allow_redirects") is False
        return _FakeResp(302, {"location": "http://169.254.169.254/latest/meta-data/"})

    with pytest.raises(UnsafeHttpTargetError):
        safe_request("GET", "https://public.example.com/start", request_fn=fake_request)
    # The redirect target was validated (and rejected) before any second call.
    assert calls == ["https://public.example.com/start"]


def test_public_redirect_is_followed() -> None:
    seq = [
        _FakeResp(302, {"location": "https://public.example.com/final"}),
        _FakeResp(200, {}),
    ]
    seen: list[str] = []

    def fake_request(method, url, **kwargs):
        seen.append(url)
        return seq[len(seen) - 1]

    resp = safe_request("GET", "https://public.example.com/start", request_fn=fake_request)
    assert resp.status_code == 200
    assert seen == [
        "https://public.example.com/start",
        "https://public.example.com/final",
    ]


def test_redirect_loop_is_bounded() -> None:
    def fake_request(method, url, **kwargs):
        return _FakeResp(302, {"location": "https://public.example.com/loop"})

    with pytest.raises(UnsafeHttpTargetError):
        safe_request(
            "GET",
            "https://public.example.com/loop",
            request_fn=fake_request,
            max_redirects=3,
        )
