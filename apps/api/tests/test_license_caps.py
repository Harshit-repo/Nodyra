"""Resource-count cap enforcement under the Community edition.

The suite defaults to an Enterprise license (conftest), so these tests opt back
down to Community to exercise the caps (environments=3, runners=1,
deployments=10, seats=5).
"""
import pytest

from app.config import settings
from app.services import licensing
from app.services.licensing import Edition, LicenseLimitError, enforce_resource_cap


@pytest.fixture(autouse=True)
def _community(monkeypatch):
    monkeypatch.setattr(settings, "license_key", "")
    monkeypatch.setattr(settings, "license_public_key", "")
    licensing.invalidate_license_cache()
    yield
    licensing.invalidate_license_cache()


@pytest.mark.asyncio
async def test_environment_cap_blocks_fourth(client):
    statuses = []
    for i in range(4):
        r = await client.post(
            "/environments", json={"name": f"env{i}", "python_version": "3.12"}
        )
        statuses.append(r.status_code)
    # First three succeed; the fourth crosses the Community cap of 3.
    assert all(s in (200, 201) for s in statuses[:3]), statuses
    assert statuses[3] == 402, statuses
    # The 402 body names the resource, current count, and edition.
    blocked = await client.post(
        "/environments", json={"name": "env-blocked", "python_version": "3.12"}
    )
    detail = blocked.json()["detail"]
    assert detail["resource"] == "environments"
    assert detail["limit"] == 3
    assert detail["edition"] == "community"


@pytest.mark.asyncio
async def test_seats_cap_via_helper(client):
    from app.models import User

    async with licensing.SessionLocal() as s:
        async with s.begin():
            for i in range(5):
                s.add(User(email=f"user{i}@x.com", password_hash="x", role="viewer"))

    async with licensing.SessionLocal() as s:
        # cap = 5, current = 5 → blocked.
        with pytest.raises(LicenseLimitError) as exc:
            await enforce_resource_cap(s, "seats")
    assert exc.value.detail["resource"] == "seats"
    assert exc.value.detail["edition"] == Edition.COMMUNITY.value


@pytest.mark.asyncio
async def test_unlimited_never_blocks(client, monkeypatch):
    from app.models import User
    from tests._license_keys import TEST_PUBLIC_KEY_PEM, enterprise_key

    monkeypatch.setattr(settings, "license_public_key", TEST_PUBLIC_KEY_PEM)
    monkeypatch.setattr(settings, "license_key", enterprise_key())
    licensing.invalidate_license_cache()

    async with licensing.SessionLocal() as s:
        async with s.begin():
            for i in range(5):
                s.add(User(email=f"u{i}@x.com", password_hash="x", role="viewer"))

    async with licensing.SessionLocal() as s:
        await enforce_resource_cap(s, "seats")  # 0 = unlimited → no raise
