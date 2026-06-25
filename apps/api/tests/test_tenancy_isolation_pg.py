"""A5: prove the RLS backstop on Postgres — the only backend that can.

These tests run automatically in the CI postgres lane (NOODLE_TEST_DATABASE_URL
set) and against a local compose Postgres; they skip when no server is
reachable. They use their own scratch database (never the configured one) and
assert through a dedicated NON-superuser role, because Postgres superusers
bypass row-level security entirely — which is itself codified as a test below
and a deployment requirement: **production must connect as a non-superuser DB
role or the RLS backstop is void.**
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

asyncpg = pytest.importorskip("asyncpg")

API_DIR = Path(__file__).resolve().parents[1]
TEST_DB = "noodle_tenancy_test"
APP_ROLE, APP_PASSWORD = "noodle_tenancy_app", "tenancy-app-pw"

_BASE_URL = os.environ.get(
    "NOODLE_TEST_DATABASE_URL",
    "postgresql+asyncpg://noodle:noodle@localhost:5432/noodle",
)


def _dsn(url: str, database: str | None = None, user: str | None = None) -> str:
    """SQLAlchemy-or-plain URL -> asyncpg DSN, optionally swapping db/user."""
    dsn = url.replace("postgresql+asyncpg://", "postgresql://")
    head, _, db = dsn.rpartition("/")
    if database is not None:
        dsn = f"{head}/{database}"
    if user is not None:
        scheme, _, rest = dsn.partition("://")
        _, _, hostpart = rest.rpartition("@")
        dsn = f"{scheme}://{user}:{APP_PASSWORD}@{hostpart}"
    return dsn


async def _setup() -> None:
    admin = await asyncpg.connect(_dsn(_BASE_URL))
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE {TEST_DB}')
        await admin.execute(
            f"DROP ROLE IF EXISTS {APP_ROLE}; "
            f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}' "
            "NOSUPERUSER NOBYPASSRLS"
        )
    finally:
        await admin.close()

    # Real migration chain on Postgres — also proves 0040-0042 apply there
    # (revision-id length, RLS DDL) which SQLite cannot.
    env = {**os.environ, "DATABASE_URL": _dsn(_BASE_URL, TEST_DB).replace(
        "postgresql://", "postgresql+asyncpg://"
    )}
    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=API_DIR, env=env, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, f"alembic upgrade failed:\n{result.stderr}"

    db = await asyncpg.connect(_dsn(_BASE_URL, TEST_DB))
    try:
        await db.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
        await db.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
            f"IN SCHEMA public TO {APP_ROLE}"
        )
        # Seed as admin with the GUC unset: the fail-open path (documented in
        # migration 0042) is what allows maintenance/seeding connections.
        await db.execute(
            "INSERT INTO organizations (id, name, slug, status) VALUES "
            "('org-a', 'A', 'a', 'active'), ('org-b', 'B', 'b', 'active')"
        )
        await db.execute(
            "INSERT INTO workflows "
            "(id, name, active, published_version, error_alerts, "
            " allow_concurrent, org_id) VALUES "
            "('wf-a', 'a-flow', false, 1, '{}', true, 'org-a'), "
            "('wf-b', 'b-flow', false, 1, '{}', true, 'org-b')"
        )
        await db.execute(
            "INSERT INTO folders (id, org_id, name) VALUES "
            "('folder-a', 'org-a', 'A folder'), "
            "('folder-b', 'org-b', 'B folder')"
        )
        await db.execute(
            "INSERT INTO runner_pools (id, org_id, name) VALUES "
            "('pool-a', 'org-a', 'A pool'), ('pool-b', 'org-b', 'B pool')"
        )
        await db.execute(
            "INSERT INTO runners (id, pool_id, org_id, name) VALUES "
            "('runner-a', 'pool-a', 'org-a', 'A runner'), "
            "('runner-b', 'pool-b', 'org-b', 'B runner')"
        )
        await db.execute(
            "INSERT INTO runs "
            "(id, workflow_id, workflow_version, mode, status, trigger_type, org_id) "
            "VALUES "
            "('run-a', 'wf-a', 1, 'manual', 'success', 'manual', 'org-a'), "
            "('run-b', 'wf-b', 1, 'manual', 'success', 'manual', 'org-b')"
        )
        await db.execute(
            "INSERT INTO artifacts "
            "(id, run_id, node_id, name, kind, content_type, size_bytes, "
            " storage_backend, storage_key, metadata, org_id) VALUES "
            "('artifact-a', 'run-a', 'n', 'a', 'binary', "
            " 'application/octet-stream', 1, 'local', 'a', '{}', 'org-a'), "
            "('artifact-b', 'run-b', 'n', 'b', 'binary', "
            " 'application/octet-stream', 1, 'local', 'b', '{}', 'org-b')"
        )
    finally:
        await db.close()


async def _teardown() -> None:
    admin = await asyncpg.connect(_dsn(_BASE_URL))
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)')
        await admin.execute(f'DROP ROLE IF EXISTS {APP_ROLE}')
    finally:
        await admin.close()


@pytest.fixture(scope="module")
def pg():
    """Scratch DB + non-superuser role, or skip when Postgres is unreachable."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(_setup())
    except (OSError, asyncpg.PostgresError, ConnectionError) as exc:
        pytest.skip(f"Postgres not reachable for RLS tests: {exc}")
    yield {
        "app": _dsn(_BASE_URL, TEST_DB, user=APP_ROLE),
        "admin": _dsn(_BASE_URL, TEST_DB),
    }
    asyncio.run(_teardown())


async def test_rls_blocks_cross_org_raw_sql(pg):
    """The core backstop proof: raw SQL as the app role cannot read, update,
    delete, or insert across the org boundary once the GUC is set."""
    conn = await asyncpg.connect(pg["app"])
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_org', 'org-a', true)"
            )
            rows = await conn.fetch("SELECT id FROM workflows")
            assert [r["id"] for r in rows] == ["wf-a"]

            status = await conn.execute(
                "UPDATE workflows SET name = 'stolen' WHERE id = 'wf-b'"
            )
            assert status == "UPDATE 0"

            status = await conn.execute(
                "DELETE FROM workflows WHERE id = 'wf-b'"
            )
            assert status == "DELETE 0"

        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.current_org', 'org-a', true)"
                )
                await conn.execute(
                    "INSERT INTO workflows "
                    "(id, name, active, published_version, error_alerts, "
                    " allow_concurrent, org_id) VALUES "
                    "('wf-evil', 'evil', false, 1, '{}', true, 'org-b')"
                )
    finally:
        await conn.close()


async def test_guc_dies_with_the_transaction(pg):
    """set_config(..., true) == SET LOCAL: a pooled connection cannot leak
    the previous request's org into the next transaction."""
    conn = await asyncpg.connect(pg["app"])
    try:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_org', 'org-a', true)"
            )
            value = await conn.fetchval(
                "SELECT current_setting('app.current_org', true)"
            )
            assert value == "org-a"
        value = await conn.fetchval(
            "SELECT current_setting('app.current_org', true)"
        )
        assert value in (None, "")
        rows = await conn.fetch("SELECT id FROM workflows ORDER BY id")
        assert [r["id"] for r in rows] == ["wf-a", "wf-b"]  # fail-open, unset
    finally:
        await conn.close()


async def test_new_direct_org_policies_cover_folders_artifacts_and_runners(pg):
    """New tenant tables and migrated direct-org children stay RLS scoped."""
    conn = await asyncpg.connect(pg["app"])
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_org', 'org-a', true)")
            expected = {
                "folders": "folder-a",
                "artifacts": "artifact-a",
                "runners": "runner-a",
            }
            for table, row_id in expected.items():
                rows = await conn.fetch(f"SELECT id FROM {table} ORDER BY id")
                assert [row["id"] for row in rows] == [row_id]

            assert (
                await conn.execute(
                    "UPDATE artifacts SET name = 'stolen' WHERE id = 'artifact-b'"
                )
                == "UPDATE 0"
            )
    finally:
        await conn.close()


async def test_superuser_bypasses_rls_so_production_must_not_use_one(pg):
    """Codifies the deployment requirement: a superuser connection ignores
    RLS no matter what the GUC says. docs/deployment.md must require a
    non-superuser app role for multi-tenant installs."""
    conn = await asyncpg.connect(pg["admin"])
    try:
        is_super = await conn.fetchval(
            "SELECT usesuper FROM pg_user WHERE usename = current_user"
        )
        if not is_super:
            pytest.skip("admin URL is not a superuser; bypass case n/a")
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_org', 'org-a', true)"
            )
            rows = await conn.fetch("SELECT id FROM workflows ORDER BY id")
        assert [r["id"] for r in rows] == ["wf-a", "wf-b"]
    finally:
        await conn.close()


async def test_rls_catches_orm_filter_bypass(pg, monkeypatch):
    """Layer 1 catches what Layer 2 misses: a text() query through the ORM
    Session skips the with_loader_criteria filter, but the after_begin GUC
    hook + RLS still scope it to the request org."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.config import settings
    from app.tenancy import current_org_id, install_org_filter

    install_org_filter()
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    engine = create_async_engine(
        pg["app"].replace("postgresql://", "postgresql+asyncpg://"),
        poolclass=NullPool,
    )
    token = current_org_id.set("org-a")
    try:
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            rows = (await session.execute(text("SELECT id FROM workflows"))).all()
            assert [r[0] for r in rows] == ["wf-a"]
    finally:
        current_org_id.reset(token)
        await engine.dispose()


async def test_multi_tenant_startup_rejects_rls_bypass_role(pg, monkeypatch):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from app.config import settings
    from app.tenancy import assert_safe_postgres_role

    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    app_engine = create_async_engine(
        pg["app"].replace("postgresql://", "postgresql+asyncpg://"),
        poolclass=NullPool,
    )
    try:
        await assert_safe_postgres_role(app_engine)
    finally:
        await app_engine.dispose()

    admin = await asyncpg.connect(pg["admin"])
    try:
        is_super = await admin.fetchval(
            "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
        )
    finally:
        await admin.close()
    if not is_super:
        pytest.skip("admin URL is not a superuser; rejection case n/a")

    admin_engine = create_async_engine(
        pg["admin"].replace("postgresql://", "postgresql+asyncpg://"),
        poolclass=NullPool,
    )
    try:
        with pytest.raises(RuntimeError, match="NOBYPASSRLS"):
            await assert_safe_postgres_role(admin_engine)
    finally:
        await admin_engine.dispose()
