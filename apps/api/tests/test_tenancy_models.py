"""A1: Organization + Membership models."""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import models  # noqa: F401 - registers ORM models on Base.metadata
from app.db import Base
from app.models import Membership, Organization, User
from app.services.crypto import hash_password


@pytest_asyncio.fixture
async def session(tmp_path):
    # File-backed: with NullPool every connection to an in-memory SQLite gets
    # a fresh empty database, so create_all would be invisible to the session.
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'tenancy.db'}", poolclass=NullPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_org_and_membership_round_trip(session):
    org = Organization(name="Acme", slug="acme")
    user = User(email="a@acme.test", password_hash=hash_password("pw"), role="admin")
    session.add_all([org, user])
    await session.flush()
    session.add(Membership(org_id=org.id, user_id=user.id, role="owner"))
    await session.commit()

    loaded = await session.scalar(select(Organization).where(Organization.slug == "acme"))
    assert loaded is not None
    assert loaded.status == "active"
    assert loaded.wrapped_org_kek is None  # Phase E fills this in

    member = await session.scalar(select(Membership).where(Membership.org_id == org.id))
    assert member.user_id == user.id
    assert member.role == "owner"


@pytest.mark.asyncio
async def test_membership_unique_per_org_user(session):
    org = Organization(name="Acme", slug="acme")
    user = User(email="a@acme.test", password_hash=hash_password("pw"), role="admin")
    session.add_all([org, user])
    await session.flush()
    session.add(Membership(org_id=org.id, user_id=user.id, role="viewer"))
    await session.commit()
    session.add(Membership(org_id=org.id, user_id=user.id, role="editor"))
    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.asyncio
async def test_org_slug_unique(session):
    session.add_all([
        Organization(name="A", slug="same"),
        Organization(name="B", slug="same"),
    ])
    with pytest.raises(IntegrityError):
        await session.commit()
