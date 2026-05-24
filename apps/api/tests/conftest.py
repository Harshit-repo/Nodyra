import os
import tempfile
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.services.retention as retention_module
import app.services.runner as runner_module
import app.services.triggers as triggers_module
import app.services.venv as venv_module
from app import models  # noqa: F401 - registers ORM models on Base.metadata
from app.config import settings
from app.db import Base, get_session
from app.main import app

# Tests never shell out to `uv`, and runs execute synchronously for determinism.
# Force the in-process engine: the subprocess runner keeps a warm process per
# env, but pytest-asyncio gives each test a fresh event loop, so a pooled
# subprocess bound to an earlier (now-closed) loop would break later tests.
settings.enable_venv_builds = False
settings.run_synchronously = True
settings.use_subprocess_runner = False


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    db_path = handle.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_session = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_session() -> AsyncIterator:
        async with test_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    originals = {
        venv_module: venv_module.SessionLocal,
        runner_module: runner_module.SessionLocal,
        triggers_module: triggers_module.SessionLocal,
        retention_module: retention_module.SessionLocal,
    }
    venv_module.SessionLocal = test_session
    runner_module.SessionLocal = test_session
    triggers_module.SessionLocal = test_session
    retention_module.SessionLocal = test_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client

    app.dependency_overrides.clear()
    for module, original in originals.items():
        module.SessionLocal = original
    await engine.dispose()
    try:
        os.unlink(db_path)
    except OSError:
        pass
