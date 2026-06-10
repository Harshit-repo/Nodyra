"""A2: request org context + automatic ORM org scoping.

The filter (Layer 2) is the ergonomic, all-backends enforcement layer; the
Postgres RLS policies (Layer 1, migration + test in the postgres lane) are the
DB-enforced backstop. These tests prove Layer 2 on SQLite.
"""

import pytest
import pytest_asyncio
from sqlalchemy import String, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.pool import NullPool

from app import models  # noqa: F401 - registers ORM models on Base.metadata
from app.config import settings
from app.db import Base
from app.tenancy import (
    DEFAULT_ORG_ID,
    active_org_id,
    current_org_id,
    install_org_filter,
    org_scoped_models,
    run_as_system,
)


class OrgWidget(Base):
    """Test-only org-scoped model; proves the filter discovers org_id columns."""

    __tablename__ = "test_org_widgets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)


@pytest_asyncio.fixture
async def session(tmp_path):
    install_org_filter()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'scoping.db'}", poolclass=NullPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add_all(
            [
                OrgWidget(id="w1", org_id="org-a", name="a-widget"),
                OrgWidget(id="w2", org_id="org-b", name="b-widget"),
                OrgWidget(id="w3", org_id=DEFAULT_ORG_ID, name="default-widget"),
            ]
        )
        await session.commit()
        yield session
    await engine.dispose()


@pytest.fixture
def mt_enabled(monkeypatch):
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    token = current_org_id.set(None)
    yield
    current_org_id.reset(token)


def test_active_org_is_none_when_flag_off(monkeypatch):
    monkeypatch.setattr(settings, "multi_tenancy_enabled", False)
    token = current_org_id.set("org-a")
    try:
        assert active_org_id() is None
    finally:
        current_org_id.reset(token)


def test_active_org_defaults_to_default_org(mt_enabled):
    assert active_org_id() == DEFAULT_ORG_ID


def test_org_scoped_models_discovers_org_id_columns():
    assert OrgWidget in org_scoped_models()
    assert models.User not in org_scoped_models()


@pytest.mark.asyncio
async def test_flag_off_returns_all_rows(session, monkeypatch):
    monkeypatch.setattr(settings, "multi_tenancy_enabled", False)
    rows = (await session.scalars(select(OrgWidget))).all()
    assert {r.id for r in rows} == {"w1", "w2", "w3"}


@pytest.mark.asyncio
async def test_flag_on_scopes_to_current_org(session, mt_enabled):
    current_org_id.set("org-a")
    rows = (await session.scalars(select(OrgWidget))).all()
    assert {r.id for r in rows} == {"w1"}


@pytest.mark.asyncio
async def test_flag_on_unset_context_scopes_to_default_org(session, mt_enabled):
    rows = (await session.scalars(select(OrgWidget))).all()
    assert {r.id for r in rows} == {"w3"}


def test_run_as_system_disables_scoping(mt_enabled):
    assert active_org_id() == DEFAULT_ORG_ID
    with run_as_system():
        assert active_org_id() is None
    assert active_org_id() == DEFAULT_ORG_ID


@pytest.mark.asyncio
async def test_background_services_see_all_orgs(session, mt_enabled):
    with run_as_system():
        rows = (await session.scalars(select(OrgWidget))).all()
        assert {r.id for r in rows} == {"w1", "w2", "w3"}


@pytest.mark.asyncio
async def test_scoping_applies_to_session_get(session, mt_enabled):
    current_org_id.set("org-a")
    # populate_existing forces a real SELECT (no identity-map shortcut).
    assert await session.get(OrgWidget, "w2", populate_existing=True) is None
    found = await session.get(OrgWidget, "w1", populate_existing=True)
    assert found is not None and found.name == "a-widget"
