"""Tests for runtime HTTP safety (SSRF guard + redirect re-validation, C2)."""

from __future__ import annotations

import pytest

from noodle_nodes.http_security import (
    UnsafeHttpTargetError,
    assert_public_http_url,
    safe_request,
)


class _FakeResp:
    def __init__(self, status_code: int, headers: dict | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}


def test_literal_private_targets_are_blocked() -> None:
    for url in (
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:8000/internal",
        "http://127.0.0.1/",
        "http://10.0.0.5/",
    ):
        with pytest.raises(UnsafeHttpTargetError):
            assert_public_http_url(url)


def test_redirect_to_private_target_is_blocked() -> None:
    """A public URL that 302-redirects to a private host must NOT be followed."""
    calls: list[str] = []

    def fake_request(method, url, **kwargs):
        calls.append(url)
        assert kwargs.get("allow_redirects") is False
        return _FakeResp(302, {"location": "http://169.254.169.254/latest/meta-data/"})

    with pytest.raises(UnsafeHttpTargetError):
        safe_request(
            "GET", "https://public.example.com/start", request_fn=fake_request
        )
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

    resp = safe_request(
        "GET", "https://public.example.com/start", request_fn=fake_request
    )
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
