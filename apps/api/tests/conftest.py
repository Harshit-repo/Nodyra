import os
import shutil
import tempfile
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.main as main_module
import app.mcp.tools as mcp_tools_module
import app.routers.chat_public as chat_public_module
import app.routers.runner_pools as runner_pools_module
import app.services.agentic_builder as agentic_builder_module
import app.services.artifact_reconcile as artifact_reconcile_module
import app.services.artifacts as artifacts_module
import app.services.backends as backends_module
import app.services.chat_service as chat_service_module
import app.services.environment_builds as environment_builds_module
import app.services.licensing as licensing_module
import app.services.live_settings as live_settings_module
import app.services.provider_triggers as provider_triggers_module
import app.services.queue as queue_module
import app.services.redaction as redaction_module
import app.services.remote_dispatch as remote_dispatch_module
import app.services.retention as retention_module
import app.services.run_checkpoints as run_checkpoints_module
import app.services.runner as runner_module
import app.services.runtime_pool as runtime_pool_module
import app.services.subworkflows as subworkflows_module
import app.services.triggers as triggers_module
from app import models  # noqa: F401 - registers ORM models on Base.metadata
from app.config import settings
from app.db import Base, get_session
from app.main import app
from app.tenancy import DEFAULT_ORG_ID

# Tests never shell out to `uv`, and runs execute synchronously for determinism.
# Force the in-process engine: the subprocess runner keeps a warm process per
# env, but pytest-asyncio gives each test a fresh event loop, so a pooled
# subprocess bound to an earlier (now-closed) loop would break later tests.
settings.enable_venv_builds = False
settings.run_synchronously = True
settings.use_subprocess_runner = False
# The fail-closed security guard (config.security_startup_errors / AUTH-1) aborts
# lifespan startup when auth/multi-tenancy is on while SECRET_KEY is the public
# default. Lifespan-running tests (TestClient in test_cookie_auth/test_health)
# flip auth_required on, so pin a non-default secret for the whole suite — tests
# should never exercise the placeholder key anyway.
settings.secret_key = "nodyra-test-secret-deterministic-not-the-default"


@pytest.fixture(autouse=True)
def _reset_event_broker():
    """Isolate the module-level run-event broker between tests.

    The brokers are singletons. pytest-asyncio hands each test a fresh event
    loop, so any asyncio object it retains from a prior test — a subscriber
    ``Queue``, or the shared Redis client's internal connection lock once
    ``connect()`` has pinned Redis mode — is bound to a now-closed loop and
    raises ``bound to a different event loop`` in the next test. Reset to a
    clean in-process brokers around every test so event streaming is
    deterministic and loop-safe. Production pins the Redis transport for real
    via the app lifespan's ``broker.connect()`` (which tests don't run).
    """
    from app.services import events

    def _clear_broker(broker) -> None:
        broker._events.clear()
        broker._subscribers.clear()
        broker._finished.clear()
        broker._last_activity.clear()
        broker._mode = "inprocess"
        broker._redis = None

    def _clear() -> None:
        _clear_broker(events.broker)
        _clear_broker(events.workflow_broker)

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

    from app.services import github_sync_jobs as github_sync_jobs_mod
    from app.services import queue as queue_mod
    from app.services import runner as runner_module
    from app.services import runtime_pool as runtime_pool_mod

    def _reset() -> None:
        # Cancel and drop any lingering background run tasks.
        for task in list(runner_module._active_runs.values()):
            if not task.done():
                task.cancel()
        runner_module._active_runs.clear()
        # The durable-queue wakeup Event is a lazy module global bound to the
        # loop that first used it. A lifespan-running test (TestClient in
        # test_health) binds it to that test's loop; the next test's loop then
        # raises "bound to a different event loop". Null it so it rebinds (TEST-1).
        queue_mod._wakeup = None
        environment_builds_module._wakeup = None
        github_sync_jobs_mod._wakeup = None
        # Rebuild the runtime pool's loop-bound primitives so no permit slot
        # leaked by a prior test's interrupted run survives into this one.
        pool = runtime_pool_mod.pool
        pool._envs.clear()
        pool._lock = asyncio.Lock()
        pool._global_sem = runtime_pool_mod._ResizableAdmission(
            settings.max_concurrent_runs
        )
        pool._max_concurrent_runs = max(1, settings.max_concurrent_runs)
        pool._scale_lock = asyncio.Lock()
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


class _InlineTestProcessIsolator:
    """Run Code-node workers inline for API tests.

    The engine has dedicated process-isolator tests. API workflow tests use
    trusted snippets and should not depend on Windows multiprocessing spawn
    semantics for every Code node they exercise.
    """

    async def run(self, fn, kwargs, *, timeout=None):
        import asyncio

        if timeout is not None:
            return await asyncio.wait_for(asyncio.to_thread(fn, **kwargs), timeout)
        return await asyncio.to_thread(fn, **kwargs)

    def shutdown(self) -> None:
        """Interface parity with PooledProcessIsolator.

        Tests that reload ``app.main`` (e.g. the webhook_role reload test)
        rebind ``main.process_isolator`` to THIS fake for the rest of the
        session; any later test that runs the app lifespan then calls
        ``process_isolator.shutdown()`` on it during teardown."""


@pytest.fixture(autouse=True)
def _inline_code_process_isolator_for_api_tests():
    previous = runner_module.process_isolator
    runner_module.process_isolator = _InlineTestProcessIsolator()
    yield
    runner_module.process_isolator = previous


@pytest.fixture(autouse=True)
def _relax_sandbox_policy():
    """Default the MT→sandbox startup policy off for the suite.

    Many MT tests flip ``multi_tenancy_enabled`` on without configuring a
    container sandbox; in production that combination refuses to boot
    (services/sandbox_policy.py). test_sandbox_policy.py re-enables
    strictness explicitly to test the gate itself.
    """
    from app.config import settings as _settings

    prev = _settings.sandbox_policy_strict
    _settings.sandbox_policy_strict = False
    yield
    _settings.sandbox_policy_strict = prev


@pytest.fixture(autouse=True)
def _reset_queue_drain_flag():
    """Reset the process-global ``settings.queue_drain`` between tests.

    ``/ops/drain`` and the dispatch-drain tests flip this module-level flag; a
    test that sets it True (or leaves it set after a crash) makes
    test_ops::test_drain_status_default_false observe ``draining: True``. Reset
    around every test so drain state never leaks across the suite (TEST-2).
    """
    from app.config import settings as _settings

    prev = _settings.queue_drain
    _settings.queue_drain = False
    yield
    _settings.queue_drain = prev


@pytest.fixture(autouse=True)
def _reset_auth_rate_limit_state():
    """Clear the in-process auth rate-limit buckets between tests.

    The buckets are keyed by client IP — every test shares the ASGI transport
    "testclient" address, so a fast full-suite run accumulates enough register
    calls within one minute to trip the brute-force limiter and 429 unrelated
    tests.
    """
    from app.services import rate_limit

    rate_limit.reset()
    yield
    rate_limit.reset()


@pytest.fixture(autouse=True)
def _license_enterprise_by_default():
    """Default the whole suite to an (unlimited) Enterprise license.

    The Community edition imposes resource caps (3 environments, 1 runner, 3
    active deployments, 2 seats). Unrelated tests freely create more than that
    within a single fresh-DB test, so without a default license they would trip
    the 402 caps. We mint a valid Enterprise key against the test public key so
    every test runs ``unlimited`` unless it explicitly opts into a lower edition
    (test_licensing.py / test_license_caps.py monkeypatch ``license_key`` /
    ``license_public_key`` back down).
    """
    from app.config import settings as _settings
    from tests._license_keys import TEST_PUBLIC_KEY_PEM, enterprise_key

    prev_pub = _settings.license_public_key
    prev_key = _settings.license_key
    _settings.license_public_key = TEST_PUBLIC_KEY_PEM
    _settings.license_key = enterprise_key()
    licensing_module.invalidate_license_cache()
    # A trigger-role test reloads ``app.main`` with a temporary Settings
    # instance.  Functions attached to the already-imported FastAPI app retain
    # the reloaded module globals, so restore the canonical singleton at every
    # fixture boundary to keep middleware configuration isolated by test.
    main_module.settings = settings
    yield
    _settings.license_public_key = prev_pub
    _settings.license_key = prev_key
    licensing_module.invalidate_license_cache()


@pytest.fixture(autouse=True)
def _reset_webhook_auth_flag():
    """Disable webhook_require_auth by default so tests can publish workflows
    with open webhooks (auth_type=none). Individual tests that exercise the
    enforcement path re-enable it explicitly."""
    prev = settings.webhook_require_auth
    settings.webhook_require_auth = False
    yield
    settings.webhook_require_auth = prev


@pytest.fixture(autouse=True)
def _reset_webhook_listen_state():
    """Clear in-memory webhook listen sessions and capture buffer between tests.

    Both dicts are module-level state in the webhooks router. Without a reset,
    a test that calls start_listen_session for path X leaves that session open
    for subsequent tests, and a captured payload can pollute the next test's
    lastWebhook poll.
    """
    import app.routers.webhooks as webhooks_module

    webhooks_module._listening.clear()
    webhooks_module._captured.clear()
    yield
    webhooks_module._listening.clear()
    webhooks_module._captured.clear()


# Set ``NODYRA_TEST_DATABASE_URL`` (e.g. a Postgres async URL) to run the suite
# against a real backend instead of per-test SQLite. The CI "postgres" lane uses
# this so the durable queue's ``SELECT ... FOR UPDATE SKIP LOCKED`` lease path
# (Postgres-only — SQLite has no row locking) is actually exercised. Unset →
# fast, isolated, file-per-test SQLite for local runs.
TEST_DATABASE_URL = os.environ.get("NODYRA_TEST_DATABASE_URL")


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    artifacts_dir = tempfile.mkdtemp(prefix="nodyra-artifacts-test-")
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
        # WAL mode must be set before any SQLAlchemy connection touches the
        # file (journal_mode is a persistent DB setting but must be set
        # outside a transaction).  Open a standalone sync sqlite3 connection,
        # set WAL, close it — then let the async engine take over.
        import sqlite3 as _sqlite3
        _raw = _sqlite3.connect(db_path, timeout=5)
        _raw.execute("PRAGMA journal_mode=WAL")
        _raw.close()

        engine = create_async_engine(
            f"sqlite+aiosqlite:///{db_path}",
            poolclass=NullPool,
            connect_args={"timeout": 5.0},
        )

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    test_session = async_sessionmaker(engine, expire_on_commit=False)

    # ``alembic/versions/0040_orgs.py`` establishes this invariant in every
    # real deployment. ``metadata.create_all()`` does not run migration data
    # backfills, so mirror the migrated schema state for both test backends.
    # PostgreSQL's FK enforcement exposed the missing row; keeping SQLite on
    # the same fixture state prevents the two lanes from drifting again.
    async with test_session() as session:
        session.add(
            models.Organization(
                id=DEFAULT_ORG_ID,
                name="Default",
                slug="default",
            )
        )
        await session.commit()

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
    licensing_module.invalidate_license_cache()
    originals = {
        agentic_builder_module: agentic_builder_module.SessionLocal,
        artifact_reconcile_module: artifact_reconcile_module.SessionLocal,
        backends_module: backends_module.SessionLocal,
        artifacts_module: artifacts_module.SessionLocal,
        chat_service_module: chat_service_module.SessionLocal,
        run_checkpoints_module: run_checkpoints_module.SessionLocal,
        runner_module: runner_module.SessionLocal,
        triggers_module: triggers_module.SessionLocal,
        queue_module: queue_module.SessionLocal,
        environment_builds_module: environment_builds_module.SessionLocal,
        retention_module: retention_module.SessionLocal,
        live_settings_module: live_settings_module.SessionLocal,
        licensing_module: licensing_module.SessionLocal,
        provider_triggers_module: provider_triggers_module.SessionLocal,
        runtime_pool_module: runtime_pool_module.SessionLocal,
        remote_dispatch_module: remote_dispatch_module.SessionLocal,
        runner_pools_module: runner_pools_module.SessionLocal,
        chat_public_module: chat_public_module.SessionLocal,
        subworkflows_module: subworkflows_module.SessionLocal,
        mcp_tools_module: mcp_tools_module.SessionLocal,
        main_module: main_module.SessionLocal,
    }
    agentic_builder_module.SessionLocal = test_session
    artifact_reconcile_module.SessionLocal = test_session
    backends_module.SessionLocal = test_session
    artifacts_module.SessionLocal = test_session
    chat_service_module.SessionLocal = test_session
    run_checkpoints_module.SessionLocal = test_session
    runner_module.SessionLocal = test_session
    triggers_module.SessionLocal = test_session
    queue_module.SessionLocal = test_session
    environment_builds_module.SessionLocal = test_session
    retention_module.SessionLocal = test_session
    live_settings_module.SessionLocal = test_session
    licensing_module.SessionLocal = test_session
    provider_triggers_module.SessionLocal = test_session
    runtime_pool_module.SessionLocal = test_session
    remote_dispatch_module.SessionLocal = test_session
    runner_pools_module.SessionLocal = test_session
    chat_public_module.SessionLocal = test_session
    subworkflows_module.SessionLocal = test_session
    mcp_tools_module.SessionLocal = test_session
    main_module.SessionLocal = test_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client

    app.dependency_overrides.clear()
    for module, original in originals.items():
        module.SessionLocal = original
    main_module.settings = settings
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
