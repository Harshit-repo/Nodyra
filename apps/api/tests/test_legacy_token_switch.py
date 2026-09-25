"""Legacy session tokens are off by default and gated by a setting (F-11).

Pre-JWT tokens (``base64url(json).hex(HMAC)``) were accepted unconditionally
with no kill switch and no removal date. They are signed with the same secret
so they are not forgeable, but they bypass the issuer/audience validation every
JWT gets — an accept path nothing has produced for releases, kept open forever.
"""

import base64
import hashlib
import hmac
import json
import time

import pytest

from app.config import settings
from app.services import crypto


def _mint_legacy(payload: dict) -> str:
    """Reproduce the historical token format exactly."""
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    signature = hmac.new(
        settings.secret_key.encode(), body.encode(), hashlib.sha256
    ).hexdigest()
    return f"{body}.{signature}"


@pytest.fixture
def legacy_token() -> str:
    return _mint_legacy(
        {"sub": "user-1", "typ": "session", "exp": time.time() + 3600}
    )


def test_legacy_tokens_are_rejected_by_default(legacy_token, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_accept_legacy_tokens", False)
    assert crypto.verify_token(legacy_token) is None
    assert crypto.decode_session_token(legacy_token) is None


def test_legacy_tokens_are_accepted_when_explicitly_enabled(
    legacy_token, monkeypatch
) -> None:
    """The one release in which pre-migration tokens are still inside their TTL."""
    monkeypatch.setattr(settings, "auth_accept_legacy_tokens", True)
    assert crypto.verify_token(legacy_token) == "user-1"
    payload = crypto.decode_session_token(legacy_token)
    assert payload is not None and payload["sub"] == "user-1"


def test_an_expired_legacy_token_is_rejected_even_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_accept_legacy_tokens", True)
    stale = _mint_legacy({"sub": "u", "typ": "session", "exp": time.time() - 1})
    assert crypto.verify_token(stale) is None


def test_a_forged_legacy_signature_is_rejected_even_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_accept_legacy_tokens", True)
    token = _mint_legacy({"sub": "u", "typ": "session", "exp": time.time() + 60})
    body, _ = token.split(".")
    assert crypto.verify_token(f"{body}.{'0' * 64}") is None


def test_jwt_sessions_are_unaffected_by_the_switch(monkeypatch) -> None:
    """Turning the legacy path off must not touch the tokens actually in use."""
    monkeypatch.setattr(settings, "auth_accept_legacy_tokens", False)
    token = crypto.create_token("user-9")
    assert crypto.verify_token(token) == "user-9"
    assert crypto.decode_session_token(token)["sub"] == "user-9"


def test_the_default_is_off() -> None:
    """A deployment that never opts in never accepts the legacy format."""
    from app.config import Settings

    assert Settings().auth_accept_legacy_tokens is False
