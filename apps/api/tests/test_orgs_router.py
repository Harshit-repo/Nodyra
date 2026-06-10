"""Phase B: org CRUD, membership management, /me/orgs, org default env."""

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import models
from app.config import settings
from app.db import Base
from app.routers.orgs import (
    add_member,
    create_org,
    delete_org,
    list_members,
    list_my_orgs,
    remove_member,
    update_member,
)
from app.schemas import OrgCreate, OrgMemberAdd, OrgMemberUpdate
from app.services.crypto import hash_password
from app.tenancy import DEFAULT_ORG_ID, current_org_id, install_org_filter


@pytest_asyncio.fixture
async def session(tmp_path):
    install_org_filter()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'orgs.db'}", poolclass=NullPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def users(session):
    alice = models.User(email="alice@t.test", password_hash=hash_password("pw"), role="viewer")
    bob = models.User(email="bob@t.test", password_hash=hash_password("pw"), role="viewer")
    session.add_all(
        [models.Organization(id=DEFAULT_ORG_ID, name="Default", slug="default"), alice, bob]
    )
    await session.commit()
    return {"alice": alice, "bob": bob}


@pytest.fixture
def mt_on(monkeypatch):
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    token = current_org_id.set(None)
    yield
    current_org_id.reset(token)


async def test_create_org_provisions_owner_env_and_kek(session, users, mt_on):
    info = await create_org(OrgCreate(name="Acme", slug="acme"), users["alice"], session)
    assert info.slug == "acme"
    assert info.role == "owner"

    org = await session.get(models.Organization, info.id)
    assert org.wrapped_org_kek  # Phase E KEK minted at creation

    membership = await session.scalar(
        select(models.Membership)
        .where(models.Membership.org_id == info.id)
        .execution_options(skip_org_filter=True)
    )
    assert membership.user_id == users["alice"].id
    assert membership.role == "owner"

    # X2: the org gets its own default environment — no cross-org sharing of
    # the warm pool through a common global env.
    env = await session.scalar(
        select(models.Environment)
        .where(models.Environment.org_id == info.id)
        .execution_options(skip_org_filter=True)
    )
    assert env is not None and env.is_global


async def test_create_org_duplicate_slug_409(session, users, mt_on):
    await create_org(OrgCreate(name="Acme", slug="acme"), users["alice"], session)
    with pytest.raises(HTTPException) as exc:
        await create_org(OrgCreate(name="Other", slug="acme"), users["bob"], session)
    assert exc.value.status_code == 409


async def test_me_orgs_lists_only_my_memberships(session, users, mt_on):
    acme = await create_org(OrgCreate(name="Acme", slug="acme"), users["alice"], session)
    await create_org(OrgCreate(name="Bobs", slug="bobs"), users["bob"], session)
    mine = await list_my_orgs(users["alice"], session)
    assert [o.slug for o in mine] == ["acme"]
    assert mine[0].role == "owner"
    assert mine[0].id == acme.id


async def test_member_management_roundtrip(session, users, mt_on):
    org = await create_org(OrgCreate(name="Acme", slug="acme"), users["alice"], session)
    token = current_org_id.set(org.id)
    try:
        added = await add_member(
            OrgMemberAdd(email="bob@t.test", role="editor"), users["alice"], session
        )
        assert added.role == "editor"
        members = await list_members(session)
        assert {m.email for m in members} == {"alice@t.test", "bob@t.test"}

        updated = await update_member(
            users["bob"].id, OrgMemberUpdate(role="admin"), users["alice"], session
        )
        assert updated.role == "admin"

        await remove_member(users["bob"].id, users["alice"], session)
        members = await list_members(session)
        assert {m.email for m in members} == {"alice@t.test"}
    finally:
        current_org_id.reset(token)


async def test_cannot_demote_or_remove_last_owner(session, users, mt_on):
    org = await create_org(OrgCreate(name="Acme", slug="acme"), users["alice"], session)
    token = current_org_id.set(org.id)
    try:
        with pytest.raises(HTTPException) as exc:
            await update_member(
                users["alice"].id, OrgMemberUpdate(role="viewer"), users["alice"], session
            )
        assert exc.value.status_code == 400
        with pytest.raises(HTTPException) as exc:
            await remove_member(users["alice"].id, users["alice"], session)
        assert exc.value.status_code == 400
    finally:
        current_org_id.reset(token)


async def test_delete_default_org_refused(session, users, mt_on):
    session.add(
        models.Membership(
            org_id=DEFAULT_ORG_ID, user_id=users["alice"].id, role="owner"
        )
    )
    await session.commit()
    with pytest.raises(HTTPException) as exc:
        await delete_org(DEFAULT_ORG_ID, users["alice"], session)
    assert exc.value.status_code == 400


async def test_delete_org_requires_org_owner(session, users, mt_on):
    org = await create_org(OrgCreate(name="Acme", slug="acme"), users["alice"], session)
    token = current_org_id.set(org.id)
    try:
        await add_member(
            OrgMemberAdd(email="bob@t.test", role="admin"), users["alice"], session
        )
        with pytest.raises(HTTPException) as exc:
            await delete_org(org.id, users["bob"], session)
        assert exc.value.status_code == 403
        await delete_org(org.id, users["alice"], session)
        assert await session.get(models.Organization, org.id) is None
    finally:
        current_org_id.reset(token)


async def test_skip_org_filter_execution_option(session, users, mt_on):
    await create_org(OrgCreate(name="Acme", slug="acme"), users["alice"], session)
    token = current_org_id.set(DEFAULT_ORG_ID)
    try:
        scoped = (await session.scalars(select(models.Membership))).all()
        unscoped = (
            await session.scalars(
                select(models.Membership).execution_options(skip_org_filter=True)
            )
        ).all()
        assert len(unscoped) > len(scoped)
    finally:
        current_org_id.reset(token)
