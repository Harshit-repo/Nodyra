import time

import pytest

from app.config import settings
from app.services import licensing
from app.services.licensing import Edition, Feature
from tests._license_keys import TEST_PUBLIC_KEY_PEM, enterprise_key, make_key, pro_key


@pytest.fixture(autouse=True)
def _use_test_pubkey(monkeypatch):
    # Runs after the conftest enterprise-default autouse; override back to "no key".
    monkeypatch.setattr(settings, "license_public_key", TEST_PUBLIC_KEY_PEM)
    monkeypatch.setattr(settings, "license_key", "")
    licensing.invalidate_license_cache()
    yield
    licensing.invalidate_license_cache()


@pytest.mark.asyncio
async def test_no_key_is_community():
    lic = await licensing.current_license()
    assert lic.edition is Edition.COMMUNITY
    assert lic.valid is True
    assert await licensing.has_feature(Feature.SANDBOX)
    assert await licensing.resource_limit("environments") == 3
    assert await licensing.resource_limit("deployments") == 10
    assert await licensing.resource_limit("seats") == 5


@pytest.mark.asyncio
async def test_no_key_falls_back_when_settings_database_is_offline(monkeypatch):
    class OfflineSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc_info):
            return None

        async def get(self, *_args, **_kwargs):
            raise OSError("settings database unavailable")

    monkeypatch.setattr(licensing, "SessionLocal", OfflineSession)

    lic = await licensing.current_license()

    assert lic.edition is Edition.COMMUNITY
    assert lic.valid is True


@pytest.mark.asyncio
async def test_valid_pro_key(monkeypatch):
    monkeypatch.setattr(settings, "license_key", pro_key())
    licensing.invalidate_license_cache()
    lic = await licensing.current_license()
    assert lic.edition is Edition.PRO
    assert await licensing.has_feature(Feature.SANDBOX)
    assert await licensing.resource_limit("environments") == 10
    assert await licensing.resource_limit("deployments") == 0  # unlimited


@pytest.mark.asyncio
async def test_enterprise_unlocks_mt(monkeypatch):
    monkeypatch.setattr(settings, "license_key", enterprise_key())
    licensing.invalidate_license_cache()
    assert await licensing.has_feature(Feature.MULTI_TENANCY)
    assert await licensing.resource_limit("environments") == 0


@pytest.mark.asyncio
async def test_tampered_key_falls_back_to_community(monkeypatch):
    bad = pro_key()[:-4] + "AAAA"
    monkeypatch.setattr(settings, "license_key", bad)
    licensing.invalidate_license_cache()
    lic = await licensing.current_license()
    assert lic.edition is Edition.COMMUNITY
    assert lic.notice is not None


@pytest.mark.asyncio
async def test_expired_key_downgrades(monkeypatch):
    monkeypatch.setattr(
        settings, "license_key",
        make_key({"tier": "pro", "expires_at": int(time.time()) - 10}),
    )
    licensing.invalidate_license_cache()
    lic = await licensing.current_license()
    assert lic.edition is Edition.COMMUNITY
    assert "expired" in (lic.notice or "").lower()


@pytest.mark.asyncio
async def test_explicit_feature_grant_extends_tier(monkeypatch):
    monkeypatch.setattr(settings, "license_key", make_key({"tier": "pro", "features": ["sso"]}))
    licensing.invalidate_license_cache()
    assert await licensing.has_feature(Feature.SSO)
    assert await licensing.has_feature(Feature.SANDBOX)  # still has Pro defaults


@pytest.mark.asyncio
async def test_explicit_limit_override(monkeypatch):
    monkeypatch.setattr(
        settings, "license_key", make_key({"tier": "pro", "limits": {"environments": 25}})
    )
    licensing.invalidate_license_cache()
    assert await licensing.resource_limit("environments") == 25


@pytest.mark.asyncio
async def test_no_public_key_configured_is_community(monkeypatch):
    # Without a verifying key, even a "valid-looking" key can't be trusted.
    monkeypatch.setattr(settings, "license_public_key", "")
    monkeypatch.setattr(settings, "license_key", pro_key())
    licensing.invalidate_license_cache()
    lic = await licensing.current_license()
    assert lic.edition is Edition.COMMUNITY


@pytest.mark.asyncio
async def test_key_resolved_from_db_when_env_blank(client, monkeypatch):
    # client fixture redirects licensing.SessionLocal at the per-test DB.
    from app.models import SystemSetting

    monkeypatch.setattr(settings, "license_key", "")
    async with licensing.SessionLocal() as s:
        row = SystemSetting(id="singleton", license_key=pro_key())
        s.add(row)
        await s.commit()
    licensing.invalidate_license_cache()
    lic = await licensing.current_license()
    assert lic.edition is Edition.PRO


# --- capability gates + reconciliation (Task 5) ---

@pytest.mark.asyncio
async def test_require_feature_blocks_without_entitlement(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(settings, "license_key", "")
    licensing.invalidate_license_cache()
    dep = licensing.require_feature(Feature.MULTI_TENANCY)
    with pytest.raises(HTTPException) as exc:
        await dep()
    assert exc.value.status_code == 402


@pytest.mark.asyncio
async def test_require_feature_passes_with_enterprise(monkeypatch):
    monkeypatch.setattr(settings, "license_key", enterprise_key())
    licensing.invalidate_license_cache()
    dep = licensing.require_feature(Feature.MULTI_TENANCY)
    assert await dep() is None


@pytest.mark.asyncio
async def test_apply_and_remove_license_endpoint(client, monkeypatch):
    # Env key must be blank so the DB-stored key (set by the endpoint) is read.
    monkeypatch.setattr(settings, "license_key", "")
    r = await client.put("/system-settings/license", json={"license_key": pro_key()})
    assert r.status_code == 200, r.text
    assert r.json()["edition"] == "pro"

    g = await client.get("/system-settings/license")
    assert g.json()["edition"] == "pro"

    d = await client.delete("/system-settings/license")
    assert d.json()["edition"] == "community"


@pytest.mark.parametrize("seats", [0, 1, 25])
def test_legacy_seat_grant_is_enforced(seats):
    lic = licensing.verify_license_key(make_key({"tier": "pro", "seats": seats}))
    assert lic.valid and lic.limits.seats == seats


@pytest.mark.parametrize("payload", [
    [], "pro", {"tier": "pro", "expires_at": "tomorrow"},
    {"tier": "pro", "limits": {"seats": -1}}, {"tier": "pro", "seats": True},
    {"tier": "pro", "limits": "unlimited"}, {"tier": "pro", "features": "sso"},
])
def test_malformed_signed_payload_fails_closed(payload):
    assert not licensing.verify_license_key(make_key(payload)).valid


@pytest.mark.parametrize("bad_key", ["garbage", make_key({"tier": "pro", "expires_at": 1}), make_key({"tier": "unknown"})])
async def test_bad_replacement_does_not_destroy_paid_license(client, bad_key):
    good = pro_key()
    assert (await client.put("/system-settings/license", json={"license_key": good})).status_code == 200
    rejected = await client.put("/system-settings/license", json={"license_key": bad_key})
    assert rejected.status_code == 400
    assert (await client.get("/system-settings/license")).json()["edition"] == "pro"


async def test_env_managed_license_cannot_be_silently_overwritten(client, monkeypatch):
    monkeypatch.setattr(settings, "license_key", pro_key())
    licensing.invalidate_license_cache()
    assert (await client.get("/system-settings/license")).json()["managed_by_environment"]
    assert (await client.put("/system-settings/license", json={"license_key": enterprise_key()})).status_code == 409
    assert (await client.delete("/system-settings/license")).status_code == 409


@pytest.mark.parametrize("seats,expected", [(None, 10), (0, 0), (27, 27)])
def test_manual_vendor_mint_round_trip_enforces_seats(tmp_path, capsys, seats, expected):
    from argparse import Namespace

    from cryptography.hazmat.primitives import serialization
    from tools.mint_license import _sign

    from tests._license_keys import _PRIV

    private = tmp_path / "throwaway-private.pem"
    private.write_bytes(_PRIV.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    _sign(Namespace(private=str(private), tier="pro", customer="Manual test buyer", seats=seats, days=30, feature=[]))
    license = licensing.verify_license_key(capsys.readouterr().out.strip())
    assert license.valid
    assert license.customer == "Manual test buyer"
    assert license.limits.seats == expected


@pytest.mark.asyncio
async def test_auth_required_exposes_edition(client):
    r = await client.get("/auth/required")
    body = r.json()
    assert body["edition"] == "community"
    assert body["limits"]["environments"] == 3
    assert body["limits"]["deployments"] == 10
    assert body["limits"]["seats"] == 5
    assert "sandbox" in body["entitlements"]


@pytest.mark.asyncio
async def test_reconcile_disables_unlicensed_capabilities(monkeypatch):
    monkeypatch.setattr(settings, "license_key", "")
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    monkeypatch.setattr(settings, "execution_sandbox", "required")
    monkeypatch.setattr(settings, "otel_enabled", True)
    licensing.invalidate_license_cache()
    warnings = await licensing.reconcile_capabilities()
    assert settings.multi_tenancy_enabled is False
    assert settings.execution_sandbox == "required"
    assert settings.otel_enabled is False
    assert len(warnings) == 2
