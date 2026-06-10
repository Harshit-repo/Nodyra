"""Phase E: per-org KEK envelope.

DEKs are wrapped by the org's KEK (itself wrapped by the master KEK / future
KMS) instead of directly by the master. Legacy rows — master-wrapped DEK, or
the even older KEK-direct ciphertext — must keep decrypting.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import models
from app.db import Base
from app.services import crypto, org_keys
from app.tenancy import DEFAULT_ORG_ID


@pytest_asyncio.fixture
async def session(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'kek.db'}", poolclass=NullPool
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
def _clear_kek_cache():
    org_keys.invalidate_kek_cache()
    yield
    org_keys.invalidate_kek_cache()


# ---------------------------------------------------------------------------
# crypto layer
# ---------------------------------------------------------------------------

def test_encrypt_decrypt_with_org_kek_roundtrip():
    org_kek = crypto.generate_org_kek()
    ciphertext, wrapped_dek = crypto.encrypt_credential({"k": "v"}, org_kek=org_kek)
    assert crypto.decrypt_credential(ciphertext, wrapped_dek, org_kek=org_kek) == {"k": "v"}


def test_master_wrapped_dek_still_decrypts_with_org_kek_present():
    """Legacy row (pre-migration): DEK wrapped by master. Passing an org KEK
    must fall back to the master unwrap, not fail."""
    ciphertext, wrapped_dek = crypto.encrypt_credential({"k": "v"})  # master wrap
    org_kek = crypto.generate_org_kek()
    assert crypto.decrypt_credential(ciphertext, wrapped_dek, org_kek=org_kek) == {"k": "v"}


def test_org_wrapped_dek_does_not_decrypt_without_its_kek():
    org_kek = crypto.generate_org_kek()
    ciphertext, wrapped_dek = crypto.encrypt_credential({"k": "v"}, org_kek=org_kek)
    assert crypto.decrypt_credential(ciphertext, wrapped_dek) == {}
    other = crypto.generate_org_kek()
    assert crypto.decrypt_credential(ciphertext, wrapped_dek, org_kek=other) == {}


def test_legacy_kek_direct_path_still_works():
    ciphertext = crypto.encrypt_data({"k": "v"})
    assert crypto.decrypt_credential(ciphertext, None, org_kek=crypto.generate_org_kek()) == {"k": "v"}


def test_rewrap_dek_moves_master_wrap_to_org_wrap():
    ciphertext, master_wrapped = crypto.encrypt_credential({"k": "v"})
    org_kek = crypto.generate_org_kek()
    rewrapped = crypto.rewrap_dek(master_wrapped, org_kek)
    assert rewrapped != master_wrapped
    assert crypto.decrypt_credential(ciphertext, rewrapped, org_kek=org_kek) == {"k": "v"}


# ---------------------------------------------------------------------------
# org_keys service
# ---------------------------------------------------------------------------

async def test_get_org_kek_lazily_mints_and_persists(session):
    kek = await org_keys.get_org_kek(DEFAULT_ORG_ID, session)
    assert kek is not None
    org = await session.get(models.Organization, DEFAULT_ORG_ID)
    assert org.wrapped_org_kek  # persisted
    assert crypto.unwrap_org_kek(org.wrapped_org_kek) == kek


async def test_get_org_kek_is_stable_across_calls(session):
    first = await org_keys.get_org_kek(DEFAULT_ORG_ID, session)
    org_keys.invalidate_kek_cache()  # force a re-read from the DB
    second = await org_keys.get_org_kek(DEFAULT_ORG_ID, session)
    assert first == second


async def test_get_org_kek_unknown_org_is_none(session):
    assert await org_keys.get_org_kek("nope", session) is None
    assert await org_keys.get_org_kek(None, session) is None


async def test_credential_helpers_roundtrip(session):
    enc, dek = await org_keys.encrypt_credential_for(
        DEFAULT_ORG_ID, {"token": "s3cret"}, session
    )
    cred = models.Credential(
        name="c", type="generic", encrypted_data=enc, encrypted_dek=dek
    )
    session.add(cred)
    await session.commit()
    assert cred.org_id == DEFAULT_ORG_ID  # A3 stamping
    assert await org_keys.decrypt_credential_for(cred, session) == {"token": "s3cret"}


async def test_decrypt_for_legacy_master_wrapped_credential(session):
    """Rows created before Phase E (DEK wrapped by master) keep working."""
    enc, dek = crypto.encrypt_credential({"token": "old"})
    cred = models.Credential(
        name="old", type="generic", encrypted_data=enc, encrypted_dek=dek
    )
    session.add(cred)
    await session.commit()
    assert await org_keys.decrypt_credential_for(cred, session) == {"token": "old"}


async def test_rewrap_org_credentials(session):
    """The migration helper: master-wrapped DEKs get rewrapped under the org
    KEK; KEK-direct legacy rows (NULL dek) are left alone."""
    enc, dek = crypto.encrypt_credential({"token": "a"})
    direct = models.Credential(
        name="direct", type="generic",
        encrypted_data=crypto.encrypt_data({"token": "b"}), encrypted_dek=None,
    )
    wrapped = models.Credential(
        name="wrapped", type="generic", encrypted_data=enc, encrypted_dek=dek
    )
    session.add_all([direct, wrapped])
    await session.commit()

    count = await org_keys.rewrap_org_credentials(session, DEFAULT_ORG_ID)
    await session.commit()
    assert count == 1

    refreshed = await session.scalar(
        select(models.Credential).where(models.Credential.name == "wrapped")
    )
    assert refreshed.encrypted_dek != dek
    assert await org_keys.decrypt_credential_for(refreshed, session) == {"token": "a"}
    untouched = await session.scalar(
        select(models.Credential).where(models.Credential.name == "direct")
    )
    assert untouched.encrypted_dek is None
    assert await org_keys.decrypt_credential_for(untouched, session) == {"token": "b"}
