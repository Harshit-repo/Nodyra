import os
import shutil
import tempfile
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.routers.runner_pools as runner_pools_module
import app.services.artifacts as artifacts_module
import app.services.chat_service as chat_service_module
import app.services.live_settings as live_settings_module
import app.services.provider_triggers as provider_triggers_module
import app.services.redaction as redaction_module
import app.services.remote_dispatch as remote_dispatch_module
import app.services.retention as retention_module
import app.services.runner as runner_module
import app.services.runtime_pool as runtime_pool_module
import app.services.triggers as triggers_module
import app.services.backends as backends_module
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


@pytest.fixture(autouse=True)
def _reset_event_broker():
    """Isolate the module-level run-event broker between tests.

    The broker is a singleton. pytest-asyncio hands each test a fresh event
    loop, so any asyncio object it retains from a prior test — a subscriber
    ``Queue``, or the shared Redis client's internal connection lock once
    ``connect()`` has pinned Redis mode — is bound to a now-closed loop and
    raises ``bound to a different event loop`` in the next test. Reset to a
    clean in-process broker around every test so event streaming is
    deterministic and loop-safe. Production pins the Redis transport for real
    via the app lifespan's ``broker.connect()`` (which tests don't run).
    """
    from app.services import events

    def _clear() -> None:
        events.broker._events.clear()
        events.broker._subscribers.clear()
        events.broker._finished.clear()
        events.broker._mode = "inprocess"
        events.broker._redis = None

    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True)
def _reset_run_dispatch_state():
    """Stop any run/dispatch state from leaking into the next test.

    Two singletons survive across tests because the FastAPI lifespan (which
    would normally drain/shutdown them) never runs under ``ASGITransport``:

    * ``runner._active_runs`` — a process-global ``{run_id: asyncio.Task}``.
      A test that dispatches asynchronously (``run_synchronously = False``)
      creates an ``asyncio.create_task`` run that may still be in flight, or
      a cancelled-but-not-collected task, when the test ends. pytest-asyncio
      then closes that test's loop, leaving an orphaned task pinned to a dead
      loop in the dict.

    * ``runtime_pool.pool`` — its ``_global_sem`` / ``_subworkflow_sem`` /
      ``_lock`` are ``asyncio`` primitives created once at import and reused
      by every per-test loop. A run interrupted mid ``async with`` (cancel,
      loop teardown) can release on a closing loop, permanently decrementing
      the global semaphore's permit count. A later in-process run (e.g.
      ``_execute_queued_entry`` → ``_execute_run`` → ``global_slot()``) then
      blocks or half-completes, so its run is observed as ``running`` instead
      of ``success``. Under SQLite the runs finish fast enough to mask this;
      Postgres timing exposes it.

    Rebuild both to a clean state around every test so neither a leaked task
    nor a drained semaphore slot can bleed across the suite. The production
    lifespan owns real drain/shutdown; tests don't run it.
    """
    import asyncio

    from app.services import runner as runner_module
    from app.services import runtime_pool as runtime_pool_mod

    def _reset() -> None:
        # Cancel and drop any lingering background run tasks.
        for task in list(runner_module._active_runs.values()):
            if not task.done():
                task.cancel()
        runner_module._active_runs.clear()
        # Rebuild the runtime pool's loop-bound primitives so no permit slot
        # leaked by a prior test's interrupted run survives into this one.
        pool = runtime_pool_mod.pool
        pool._envs.clear()
        pool._lock = asyncio.Lock()
        pool._global_sem = asyncio.Semaphore(
            max(1, settings.max_concurrent_runs)
        )
        sub_cap = (
            settings.max_concurrent_subworkflows or settings.max_concurrent_runs
        )
        pool._subworkflow_sem = asyncio.Semaphore(max(1, sub_cap))
        # Per-org sub-workflow semaphores (C4) are loop-bound too — drop them
        # so a later test's loop never touches a prior loop's primitives.
        pool._org_subworkflow_sems.clear()
        pool._rss_budget = runtime_pool_mod._RssBudget()

    _reset()
    yield
    _reset()


# Set ``NOODLE_TEST_DATABASE_URL`` (e.g. a Postgres async URL) to run the suite
# against a real backend instead of per-test SQLite. The CI "postgres" lane uses
# this so the durable queue's ``SELECT ... FOR UPDATE SKIP LOCKED`` lease path
# (Postgres-only — SQLite has no row locking) is actually exercised. Unset →
# fast, isolated, file-per-test SQLite for local runs.
TEST_DATABASE_URL = os.environ.get("NOODLE_TEST_DATABASE_URL")


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    artifacts_dir = tempfile.mkdtemp(prefix="noodle-artifacts-test-")
    old_artifacts_dir = settings.artifacts_dir
    settings.artifacts_dir = artifacts_dir

    if TEST_DATABASE_URL:
        # Shared external DB (Postgres in CI). Reset the schema per test so
        # tests stay isolated; ``drop_all``/``create_all`` use checkfirst so an
        # empty database is fine on the first run.
        db_path = None
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    else:
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        db_path = handle.name
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{db_path}", poolclass=NullPool
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    test_session = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_session() -> AsyncIterator:
        async with test_session() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    # Each test gets a fresh DB; the secret cache + live-settings cache are
    # process-local, so reset them at fixture boundaries to keep tests
    # isolated. Tests mutate ``settings.X`` directly to override retention /
    # output cap / artifact limits and rely on the fallback path in
    # ``live_settings._load_from_db`` reading those at call time.
    redaction_module.invalidate_secret_cache()
    live_settings_module.invalidate_live_settings_cache()
    originals = {
        backends_module: backends_module.SessionLocal,
        artifacts_module: artifacts_module.SessionLocal,
        chat_service_module: chat_service_module.SessionLocal,
        runner_module: runner_module.SessionLocal,
        triggers_module: triggers_module.SessionLocal,
        retention_module: retention_module.SessionLocal,
        live_settings_module: live_settings_module.SessionLocal,
        provider_triggers_module: provider_triggers_module.SessionLocal,
        runtime_pool_module: runtime_pool_module.SessionLocal,
        remote_dispatch_module: remote_dispatch_module.SessionLocal,
        runner_pools_module: runner_pools_module.SessionLocal,
    }
    backends_module.SessionLocal = test_session
    artifacts_module.SessionLocal = test_session
    chat_service_module.SessionLocal = test_session
    runner_module.SessionLocal = test_session
    triggers_module.SessionLocal = test_session
    retention_module.SessionLocal = test_session
    live_settings_module.SessionLocal = test_session
    provider_triggers_module.SessionLocal = test_session
    runtime_pool_module.SessionLocal = test_session
    remote_dispatch_module.SessionLocal = test_session
    runner_pools_module.SessionLocal = test_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client

    app.dependency_overrides.clear()
    for module, original in originals.items():
        module.SessionLocal = original
    settings.artifacts_dir = old_artifacts_dir
    redaction_module.invalidate_secret_cache()
    live_settings_module.invalidate_live_settings_cache()
    if TEST_DATABASE_URL:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
    shutil.rmtree(artifacts_dir, ignore_errors=True)
    if db_path is not None:
        try:
            os.unlink(db_path)
        except OSError:
            pass
