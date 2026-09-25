"""Customer-side licence renewal.

The failure this prevents is specific and expensive: a paying customer's key
expires, nobody notices, and a production instance silently drops to Community —
sandbox caps, seat limits and features gone mid-run.

Equally important is what it must *not* do. A licence server that is down, slow,
or returning nonsense must never degrade the instance it serves, and an
air-gapped deployment must never phone home at all.
"""

from __future__ import annotations

import time

import httpx
import pytest

from app.config import settings
from app.services import license_refresh, licensing

from ._license_keys import TEST_PUBLIC_KEY_PEM, make_key

SERVER = "https://licences.nodyra.test"
TOKEN = "refresh-token-value-long-enough"


@pytest.fixture(autouse=True)
def _customer(monkeypatch):
    monkeypatch.setattr(settings, "license_public_key", TEST_PUBLIC_KEY_PEM)
    monkeypatch.setattr(settings, "license_server_url", SERVER)
    monkeypatch.setattr(settings, "license_refresh_token", TOKEN)
    monkeypatch.setattr(settings, "license_key", "")
    monkeypatch.setattr(settings, "license_refresh_window_days", 14)
    licensing.invalidate_license_cache()
    yield
    licensing.invalidate_license_cache()


def _key(days_until_expiry: float, tier: str = "pro") -> str:
    return make_key(
        {
            "tier": tier,
            "customer": "Acme Inc",
            "issued_at": int(time.time()),
            "expires_at": int(time.time() + days_until_expiry * 86400),
        }
    )


class _Response:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _client_returning(response, *, captured: dict | None = None):
    """Patch httpx.AsyncClient so no test ever makes a real request."""

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, **kwargs):
            if captured is not None:
                captured["url"] = url
                captured.update(kwargs)
            if isinstance(response, Exception):
                raise response
            return response

    return _Client


class _StoredKey(dict):
    """Stands in for ``system_settings.license_key`` — the DB row a renewed key
    is written to in production, which is also where the current one is read
    from. Modelling it as the env var instead would make every test hit the
    environment-pinned short circuit."""

    def install(self, key: str) -> None:
        self["key"] = key
        licensing.invalidate_license_cache()

    @property
    def written(self) -> str | None:
        return self.get("written")


@pytest.fixture
def stored(monkeypatch):
    state = _StoredKey()

    async def _db_key() -> str | None:
        return state.get("key")

    async def _store(key: str) -> None:
        state["written"] = key
        state.install(key)

    monkeypatch.setattr(licensing, "_db_license_key", _db_key)
    monkeypatch.setattr(license_refresh, "_store_key", _store)
    return state


# ── The renewal itself ─────────────────────────────────────────────────────


async def test_a_key_inside_the_window_is_renewed(monkeypatch, stored):
    stored.install(_key(days_until_expiry=3))
    renewed = _key(days_until_expiry=45)

    captured: dict = {}
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        _client_returning(_Response(200, {"license_key": renewed}), captured=captured),
    )

    assert await license_refresh.refresh_once() is True
    assert stored.written == renewed
    assert captured["url"] == f"{SERVER}/billing/license"
    assert captured["json"] == {"refresh_token": TOKEN}
    # And the instance is now running on the renewed key.
    assert (await licensing.current_license()).edition.value == "pro"


async def test_a_key_outside_the_window_is_left_alone(monkeypatch, stored):
    """Renewing early would multiply load on the licence server for no gain."""
    stored.install(_key(days_until_expiry=90))

    called = {"n": 0}

    class _Boom:
        def __init__(self, *a, **kw):
            called["n"] += 1

        async def __aenter__(self):
            raise AssertionError("the licence server must not be contacted")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(httpx, "AsyncClient", _Boom)
    assert await license_refresh.refresh_once() is False
    assert stored.written is None


async def test_a_community_instance_never_calls_out(monkeypatch, stored):
    """No licence means no expiry, so there is nothing to renew."""
    licensing.invalidate_license_cache()
    monkeypatch.setattr(
        httpx, "AsyncClient", _client_returning(AssertionError("must not be called"))
    )
    assert await license_refresh.refresh_once() is False


# ── Not phoning home ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "server,token",
    [("", TOKEN), (SERVER, ""), ("", "")],
    ids=["no-server", "no-token", "neither"],
)
async def test_refresh_is_off_unless_fully_configured(monkeypatch, server, token):
    """An air-gapped deployment must keep verifying offline and never reach out
    — the whole reason licence verification is offline in the first place."""
    monkeypatch.setattr(settings, "license_server_url", server)
    monkeypatch.setattr(settings, "license_refresh_token", token)
    assert license_refresh.refresh_enabled() is False
    monkeypatch.setattr(
        httpx, "AsyncClient", _client_returning(AssertionError("must not be called"))
    )
    assert await license_refresh.refresh_once() is False


async def test_an_environment_pinned_key_is_never_overwritten(monkeypatch, stored):
    """An operator who pinned NODYRA_LICENSE_KEY made an explicit choice, and
    a DB write they cannot see would not take effect anyway."""
    monkeypatch.setattr(settings, "license_key", _key(days_until_expiry=1))
    licensing.invalidate_license_cache()
    monkeypatch.setattr(
        httpx, "AsyncClient", _client_returning(AssertionError("must not be called"))
    )
    assert await license_refresh.refresh_once() is False
    assert stored.written is None


# ── Failure never degrades the instance ────────────────────────────────────


@pytest.mark.parametrize(
    "response",
    [
        httpx.ConnectError("licence server unreachable"),
        httpx.ReadTimeout("timed out"),
        _Response(500, text="internal error"),
        _Response(403, text="forbidden"),
        _Response(200, None),  # not JSON
        _Response(200, {}),  # JSON with no key
        _Response(200, {"license_key": ""}),
    ],
    ids=["connect", "timeout", "500", "403", "not-json", "no-field", "empty-key"],
)
async def test_a_failing_licence_server_leaves_the_instance_untouched(
    monkeypatch, stored, response
):
    stored.install(_key(days_until_expiry=2))
    assert (await licensing.current_license()).edition.value == "pro"

    monkeypatch.setattr(httpx, "AsyncClient", _client_returning(response))

    assert await license_refresh.refresh_once() is False
    assert stored.written is None


async def test_a_cancelled_subscription_does_not_wipe_the_current_key(monkeypatch, stored):
    """402 means billing lapsed. The installed key still has weeks left, and
    those weeks are the operator's chance to notice — cutting them off now
    would be worse than the problem."""
    stored.install(_key(days_until_expiry=5))
    monkeypatch.setattr(httpx, "AsyncClient", _client_returning(_Response(402)))

    assert await license_refresh.refresh_once() is False
    assert stored.written is None
    # The installed key is untouched and still Pro.
    assert (await licensing.current_license()).edition.value == "pro"


async def test_a_key_signed_by_the_wrong_authority_is_reported(monkeypatch, stored, caplog):
    """A signing-key rotation would otherwise show up as features quietly
    disappearing rather than as an error anyone can act on."""
    import base64
    import json as _json

    stored.install(_key(days_until_expiry=2))

    body = base64.urlsafe_b64encode(_json.dumps({"tier": "pro"}).encode()).rstrip(b"=")
    forged = f"{body.decode()}.{'0' * 64}"
    monkeypatch.setattr(
        httpx, "AsyncClient", _client_returning(_Response(200, {"license_key": forged}))
    )

    with caplog.at_level("ERROR"):
        assert await license_refresh.refresh_once() is False
    assert any("did not verify" in record.message for record in caplog.records)
    assert stored.written is None
    assert (await licensing.current_license()).edition.value == "pro"


async def test_expired_license_recovers_when_renewal_returns(monkeypatch, stored):
    stored.install(_key(-2))
    assert (await licensing.current_license()).edition.value == "community"
    renewed = _key(45)
    monkeypatch.setattr(httpx, "AsyncClient", _client_returning(_Response(200, {"license_key": renewed})))
    assert await license_refresh.refresh_once() is True
    assert stored.written == renewed


@pytest.mark.parametrize("payload", [[], "bad", 12, {"license_key": []}, {"license_key": "invalid"}])
async def test_malformed_renewals_preserve_installed_license(monkeypatch, stored, payload):
    stored.install(_key(2))
    monkeypatch.setattr(httpx, "AsyncClient", _client_returning(_Response(200, payload)))
    assert await license_refresh.refresh_once() is False
    assert stored.written is None
    assert (await licensing.current_license()).edition.value == "pro"
