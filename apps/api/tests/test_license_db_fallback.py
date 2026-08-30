"""Licence resolution must survive a database that is not reachable yet.

``_db_license_key`` reads the stored licence key so an operator can set it
without redeploying. It is a *lookup*, not a gate: if the database cannot be
reached, the honest answer is "no stored key", which resolves to Community —
never an exception escaping into every request that checks an entitlement.

Its except clause listed ``OSError, OperationalError, ProgrammingError``, which
covers SQLAlchemy-wrapped failures. It does not cover a driver error raised
while the *connection* is being established, before SQLAlchemy classifies it.
``asyncpg.exceptions.InvalidCatalogNameError`` — "database ... does not exist" —
is exactly that, and it is the ordinary state of a fresh deployment whose
database has not been created yet, or one pointed at the wrong DSN.

CI found it: with no .env, settings fall back to the default PostgreSQL DSN,
the database does not exist, and three licensing tests died on a raw asyncpg
error instead of degrading to Community.
"""

from __future__ import annotations

import pytest

from app.services import licensing
from app.services.licensing import Edition


class _InvalidCatalogName(Exception):
    """Stands in for asyncpg.exceptions.InvalidCatalogNameError.

    Deliberately *not* a SQLAlchemyError subclass — that is the whole point.
    Using the real asyncpg class would tie this test to a driver that need not
    be installed to run the suite.
    """


def _session_factory_raising(exc: BaseException):
    class _Failing:
        async def __aenter__(self):
            raise exc

        async def __aexit__(self, *args):
            return False

    return lambda: _Failing()


@pytest.fixture(autouse=True)
def _clear_cache():
    licensing.invalidate_license_cache()
    yield
    licensing.invalidate_license_cache()


async def test_a_missing_database_reads_as_no_stored_key(monkeypatch):
    monkeypatch.setattr(
        licensing,
        "SessionLocal",
        _session_factory_raising(_InvalidCatalogName('database "nodyra" does not exist')),
    )
    assert await licensing._db_license_key() is None


async def test_a_missing_database_resolves_to_community(monkeypatch):
    """The consequence that matters: every entitlement check keeps working."""
    # Settings are loaded once at import, so clearing the environment is not
    # enough — a key in a developer's .env would otherwise decide this test.
    monkeypatch.setattr(licensing.boot_settings, "license_key", "", raising=False)
    monkeypatch.setattr(
        licensing,
        "SessionLocal",
        _session_factory_raising(_InvalidCatalogName('database "nodyra" does not exist')),
    )
    licence = await licensing.current_license()
    assert licence.edition is Edition.COMMUNITY
    assert licence.valid is True


@pytest.mark.parametrize(
    "exc",
    [OSError("socket closed"), RuntimeError("event loop is closed")],
    ids=["oserror", "runtime"],
)
async def test_other_connection_failures_also_degrade(monkeypatch, exc):
    monkeypatch.setattr(licensing, "SessionLocal", _session_factory_raising(exc))
    assert await licensing._db_license_key() is None


async def test_a_reachable_database_still_returns_its_key(monkeypatch):
    """The widened except must not swallow a working lookup."""

    class _Row:
        license_key = "stored-key-value"

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, model, pk):
            return _Row()

    monkeypatch.setattr(licensing, "SessionLocal", lambda: _Session())
    assert await licensing._db_license_key() == "stored-key-value"


async def test_cancellation_is_not_swallowed(monkeypatch):
    """A broad except must still let task cancellation through, or shutdown
    hangs and the reason is invisible."""
    import asyncio

    monkeypatch.setattr(
        licensing, "SessionLocal", _session_factory_raising(asyncio.CancelledError())
    )
    with pytest.raises(asyncio.CancelledError):
        await licensing._db_license_key()
