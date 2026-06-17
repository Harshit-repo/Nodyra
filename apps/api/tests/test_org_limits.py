"""C1: per-org quota settings with NULL=inherit / 0=unlimited semantics."""

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import models
from app.config import settings
from app.db import Base
from app.routers.orgs import get_org_settings, update_org_settings
from app.schemas import OrgSettingsUpdate
from app.services import org_limits
from app.services.crypto import hash_password
from app.tenancy import DEFAULT_ORG_ID, current_org_id, install_org_filter


@pytest_asyncio.fixture
async def session(tmp_path):
    install_org_filter()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'limits.db'}", poolclass=NullPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add(
            models.Organization(id=DEFAULT_ORG_ID, name="Default", slug="default")
        )
        await session.commit()
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def _clear_limits_cache():
    org_limits.invalidate_limits_cache()
    yield
    org_limits.invalidate_limits_cache()


async def test_defaults_inherit_instance_settings(session, monkeypatch):
    monkeypatch.setattr(settings, "max_concurrent_runs", 8)
    limits = await org_limits.effective_limits(session, DEFAULT_ORG_ID)
    assert limits.max_concurrent_runs == 8
    assert limits.executions_per_day == 0  # unlimited by default
    assert limits.max_inflight_subworkflows == 0  # falls back to global cap


async def test_override_wins_and_zero_means_unlimited(session):
    session.add(
        models.OrgSettings(
            org_id=DEFAULT_ORG_ID,
            max_concurrent_runs=3,
            executions_per_day=100,
            max_loop_iterations=0,
        )
    )
    await session.commit()
    org_limits.invalidate_limits_cache()
    limits = await org_limits.effective_limits(session, DEFAULT_ORG_ID)
    assert limits.max_concurrent_runs == 3
    assert limits.executions_per_day == 100
    assert limits.max_loop_iterations == 0  # explicit unlimited


async def test_unknown_org_gets_instance_defaults(session):
    limits = await org_limits.effective_limits(session, "nope")
    assert limits.max_concurrent_runs == settings.max_concurrent_runs


async def test_settings_endpoints_owner_gated(session):
    alice = models.User(
        email="alice@t.test", password_hash=hash_password("pw"), role="viewer"
    )
    bob = models.User(
        email="bob@t.test", password_hash=hash_password("pw"), role="viewer"
    )
    session.add_all([alice, bob])
    await session.flush()
    session.add_all(
        [
            models.Membership(org_id=DEFAULT_ORG_ID, user_id=alice.id, role="owner"),
            models.Membership(org_id=DEFAULT_ORG_ID, user_id=bob.id, role="admin"),
        ]
    )
    await session.commit()

    token = current_org_id.set(DEFAULT_ORG_ID)
    try:
        updated = await update_org_settings(
            DEFAULT_ORG_ID,
            OrgSettingsUpdate(max_concurrent_runs=5, executions_per_day=50),
            alice,
            session,
        )
        assert updated.max_concurrent_runs == 5

        info = await get_org_settings(DEFAULT_ORG_ID, bob, session)
        assert info.executions_per_day == 50

        with pytest.raises(HTTPException) as exc:
            await update_org_settings(
                DEFAULT_ORG_ID, OrgSettingsUpdate(max_concurrent_runs=1), bob, session
            )
        assert exc.value.status_code == 403
    finally:
        current_org_id.reset(token)
