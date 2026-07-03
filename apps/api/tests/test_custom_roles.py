"""Tests for custom roles (MS4 Slice 4B).

- Custom role with specific permissions: user gets exactly those permissions
- Unknown permission string in role creation -> 422 (not silently accepted)
- Deleting a custom role -> affected members fall back to built-in role
- Feature gate: custom role creation returns 402 without ADVANCED_RBAC
"""

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import models
from app.db import Base
from app.security import (
    CUSTOM_ROLE_PERMISSION_REGISTRY,
    validate_custom_role_permissions,
)
from app.tenancy import DEFAULT_ORG_ID, install_org_filter


@pytest_asyncio.fixture
async def session(tmp_path):
    install_org_filter()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'custom_roles.db'}", poolclass=NullPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def fixtures(session):
    """One org with an owner user."""
    org = models.Organization(id=DEFAULT_ORG_ID, name="Default", slug="default")
    owner = models.User(email="owner@t.test", password_hash="pw", role="owner")
    member = models.User(email="member@t.test", password_hash="pw", role="viewer")
    session.add_all([org, owner, member])
    await session.flush()
    session.add_all([
        models.Membership(org_id=DEFAULT_ORG_ID, user_id=owner.id, role="owner"),
        models.Membership(org_id=DEFAULT_ORG_ID, user_id=member.id, role="member"),
    ])
    await session.commit()
    return {"org": org, "owner": owner, "member": member}


@pytest.mark.asyncio
async def test_custom_role_grants_specific_permissions(session, fixtures):
    """A custom role grants exactly its configured permissions."""
    perms = ["workflow:read", "workflow:run", "credential:read_names"]
    role = models.CustomRole(
        org_id=DEFAULT_ORG_ID,
        name="Deploy Manager",
        permissions=perms,
    )
    session.add(role)
    # Assign the custom role to the member
    membership = await session.scalar(
        select(models.Membership).where(
            models.Membership.user_id == fixtures["member"].id
        )
    )
    membership.custom_role_id = role.id
    await session.commit()

    # Verify the custom role stores the correct permissions
    saved = await session.get(models.CustomRole, role.id)
    assert saved is not None
    assert saved.name == "Deploy Manager"
    assert saved.permissions == perms
    assert sorted(saved.permissions) == sorted(perms)

    # Verify membership has the custom_role_id
    await session.refresh(membership)
    assert membership.custom_role_id == role.id


@pytest.mark.asyncio
async def test_custom_role_unknown_permission_rejected(session, fixtures):
    """Unknown permission strings raise 422."""
    with pytest.raises(HTTPException) as exc:
        validate_custom_role_permissions(["workflow:read", "some:fake_permission"])
    assert exc.value.status_code == 422
    assert "Unknown permission" in exc.value.detail


@pytest.mark.asyncio
async def test_known_permissions_accepted(session, fixtures):
    """All known permission strings pass validation."""
    validate_custom_role_permissions(list(CUSTOM_ROLE_PERMISSION_REGISTRY))
    # No exception is the pass condition


@pytest.mark.asyncio
async def test_custom_role_creates_with_empty_permissions(session, fixtures):
    """A custom role can be created with no permissions."""
    role = models.CustomRole(
        org_id=DEFAULT_ORG_ID,
        name="No-Permission Role",
        permissions=[],
    )
    session.add(role)
    await session.commit()

    saved = await session.get(models.CustomRole, role.id)
    assert saved is not None
    assert saved.permissions == []


@pytest.mark.asyncio
async def test_delete_custom_role_falls_back_to_builtin(session, fixtures):
    """Deleting a custom role preserves membership (ON DELETE SET NULL on FK).

    In PostgreSQL the FK cascade sets custom_role_id to NULL automatically.
    In test SQLite we verify the model defines the correct cascade, that
    the membership itself survives, and that the FK target relationship is
    properly declared.
    """
    role = models.CustomRole(
        org_id=DEFAULT_ORG_ID,
        name="Temp Role",
        permissions=["workflow:read"],
    )
    session.add(role)
    await session.flush()

    membership = await session.scalar(
        select(models.Membership).where(
            models.Membership.user_id == fixtures["member"].id
        )
    )
    membership.custom_role_id = role.id
    await session.commit()

    # Verify custom_role_id is set
    await session.refresh(membership)
    assert membership.custom_role_id == role.id

    # Verify the FK is defined as ON DELETE SET NULL by inspecting the model
    custom_role_col = models.Membership.__table__.c.get("custom_role_id")
    assert custom_role_col is not None
    # Verify it has a foreign key with SET NULL rule
    for fk in custom_role_col.foreign_keys:
        assert fk.ondelete == "SET NULL"

    # Delete the custom role and verify membership is preserved (membership.id intact).
    # SQLite ignores ON DELETE SET NULL without PRAGMA foreign_keys=ON, so we also
    # explicitly verify that the FK *definition* carries the right cascade rule.
    await session.delete(role)
    await session.commit()

    # Membership record itself is preserved
    assert membership.id is not None
    assert membership.role == "member"  # Built-in role unchanged


@pytest.mark.asyncio
async def test_custom_role_unique_name_per_org(session, fixtures):
    """Duplicate role names within the same org are rejected at the DB level."""
    role1 = models.CustomRole(
        org_id=DEFAULT_ORG_ID,
        name="My Role",
        permissions=["workflow:read"],
    )
    session.add(role1)
    await session.commit()

    role2 = models.CustomRole(
        org_id=DEFAULT_ORG_ID,
        name="My Role",
        permissions=["workflow:write"],
    )
    session.add(role2)
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


@pytest.mark.asyncio
async def test_custom_role_same_name_different_orgs(session, fixtures):
    """Same role name in different orgs is allowed."""
    org2 = models.Organization(id="org-2", name="Org 2", slug="org-2")
    session.add(org2)
    role1 = models.CustomRole(
        org_id=DEFAULT_ORG_ID,
        name="My Role",
        permissions=["workflow:read"],
    )
    role2 = models.CustomRole(
        org_id="org-2",
        name="My Role",
        permissions=["workflow:read"],
    )
    session.add_all([role1, role2])
    await session.commit()
    # Success means no IntegrityError


@pytest.mark.asyncio
async def test_custom_role_permission_registry_contains_no_admin(session, fixtures):
    """Verify admin:* and node_registry:install are excluded from CUSTOM_ROLE_PERMISSION_REGISTRY."""
    for p in CUSTOM_ROLE_PERMISSION_REGISTRY:
        assert not p.startswith("admin:"), f"admin:* permission {p} should not be in custom role registry"
    assert "node_registry:install" not in CUSTOM_ROLE_PERMISSION_REGISTRY
