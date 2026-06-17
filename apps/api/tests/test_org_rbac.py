"""A6: org resolution (X-Org-Id) + membership-based RBAC."""

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import models
from app.config import settings
from app.db import Base
from app.security import require_role, resolve_org_for as resolve_org
from app.services.crypto import hash_password
from app.tenancy import DEFAULT_ORG_ID, current_org_id, install_org_filter


@pytest_asyncio.fixture
async def session(tmp_path):
    install_org_filter()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'rbac.db'}", poolclass=NullPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def fixtures(session):
    """Two orgs; alice is admin of X and viewer of Y; bob has no memberships."""
    alice = models.User(email="alice@t.test", password_hash=hash_password("pw"), role="viewer")
    bob = models.User(email="bob@t.test", password_hash=hash_password("pw"), role="owner")
    session.add_all(
        [
            models.Organization(id=DEFAULT_ORG_ID, name="Default", slug="default"),
            models.Organization(id="org-x", name="X", slug="x"),
            models.Organization(id="org-y", name="Y", slug="y"),
            alice,
            bob,
        ]
    )
    await session.flush()
    session.add_all(
        [
            models.Membership(org_id="org-x", user_id=alice.id, role="admin"),
            models.Membership(org_id="org-y", user_id=alice.id, role="viewer"),
        ]
    )
    await session.commit()
    return {"alice": alice, "bob": bob}


@pytest.fixture
def mt_on(monkeypatch):
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    token = current_org_id.set(None)
    yield
    current_org_id.reset(token)


async def test_resolve_org_flag_off_is_none(session, fixtures, monkeypatch):
    monkeypatch.setattr(settings, "multi_tenancy_enabled", False)
    assert await resolve_org("org-x", fixtures["alice"], session) is None


async def test_resolve_org_member_ok_and_sets_context(session, fixtures, mt_on):
    org = await resolve_org("org-x", fixtures["alice"], session)
    assert org == "org-x"
    assert current_org_id.get() == "org-x"


async def test_resolve_org_non_member_403(session, fixtures, mt_on):
    with pytest.raises(HTTPException) as exc:
        await resolve_org("org-x", fixtures["bob"], session)
    assert exc.value.status_code == 403


async def test_resolve_org_unknown_org_404(session, fixtures, mt_on):
    with pytest.raises(HTTPException) as exc:
        await resolve_org("org-nope", fixtures["alice"], session)
    assert exc.value.status_code == 404


async def test_resolve_org_anonymous_only_default(session, fixtures, mt_on):
    assert await resolve_org(None, None, session) == DEFAULT_ORG_ID
    with pytest.raises(HTTPException) as exc:
        await resolve_org("org-x", None, session)
    assert exc.value.status_code == 401


async def test_role_comes_from_membership_not_user(session, fixtures, mt_on):
    """alice's global User.role is viewer, but she is admin of org-x: the
    org membership must win with the flag on."""
    dependency = require_role("admin")
    user = await dependency(
        user=fixtures["alice"],
        org_id=await resolve_org("org-x", fixtures["alice"], session),
        session=session,
    )
    assert user is fixtures["alice"]


async def test_role_is_per_org(session, fixtures, mt_on):
    """The same user is only viewer in org-y: editor-gated work is refused."""
    dependency = require_role("editor")
    with pytest.raises(HTTPException) as exc:
        await dependency(
            user=fixtures["alice"],
            org_id=await resolve_org("org-y", fixtures["alice"], session),
            session=session,
        )
    assert exc.value.status_code == 403


async def test_flag_off_falls_back_to_user_role(session, fixtures, monkeypatch):
    monkeypatch.setattr(settings, "multi_tenancy_enabled", False)
    dependency = require_role("owner")
    user = await dependency(user=fixtures["bob"], org_id=None, session=session)
    assert user is fixtures["bob"]
