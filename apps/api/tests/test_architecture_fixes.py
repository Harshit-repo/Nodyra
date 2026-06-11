"""Tests for the architecture-fix slice: broker eviction, webhook capture
TTL, secret-cache TTL, sub-workflow cycle detection across the runtime
subprocess boundary, and the new composite indices.
"""

import asyncio
import time

from httpx import AsyncClient

from app.db import Base
from app.services import events, redaction

# --- Broker eviction --------------------------------------------------------


def test_broker_eager_eviction_after_subscriber_leaves() -> None:
    """When a subscriber drains a finished run's events, the buffer goes
    away — no need to wait for the reaper."""
    broker = events.RunBroker()
    broker.publish("r1", {"type": "run_started", "run_id": "r1"})
    broker.publish("r1", {"type": "run_finished", "run_id": "r1", "status": "success"})

    async def drain() -> None:
        received: list[dict] = []
        async for event in broker.subscribe("r1"):
            received.append(event)
        assert received[-1]["type"] == "run_finished"

    asyncio.run(drain())

    # Buffer dropped immediately because run is finished + no subscribers left.
    assert "r1" not in broker._events
    assert "r1" not in broker._finished


def test_broker_reap_drops_old_finished_runs() -> None:
    """The TTL reaper catches runs no subscriber ever drained."""
    broker = events.RunBroker()
    broker.publish("r1", {"type": "run_finished", "run_id": "r1", "status": "success"})
    broker.publish("r2", {"type": "run_finished", "run_id": "r2", "status": "success"})
    # Force one entry's finished timestamp into the past.
    broker._finished["r1"] = time.monotonic() - 7200  # 2 hours ago

    dropped = broker.reap(ttl_seconds=3600)  # 1 hour TTL
    assert dropped == 1
    assert "r1" not in broker._events
    assert "r2" in broker._events  # too fresh; preserved


def test_broker_reap_keeps_runs_with_subscribers() -> None:
    """An old run with an active subscriber must NOT be evicted under it."""
    broker = events.RunBroker()
    broker.publish("r1", {"type": "run_started"})
    broker.publish("r1", {"type": "run_finished", "run_id": "r1", "status": "success"})
    broker._finished["r1"] = time.monotonic() - 7200

    async def hold_subscription() -> int:
        agen = broker.subscribe("r1")
        first = await agen.__anext__()
        assert first["type"] == "run_started"
        dropped = broker.reap(ttl_seconds=3600)
        assert "r1" in broker._events
        await agen.aclose()
        return dropped

    dropped = asyncio.run(hold_subscription())
    assert dropped == 0


# --- Webhook capture TTL ---------------------------------------------------


async def test_webhook_capture_evicts_stale_entries(client: AsyncClient) -> None:
    from app.routers import webhooks

    webhooks._captured.clear()
    webhooks._captured["old-path"] = (
        time.monotonic() - webhooks.WEBHOOK_CAPTURE_TTL_SECONDS - 60,
        {"body": "stale"},
    )

    response = await client.post("/webhook-test/fresh-path", json={"body": "new"})
    assert response.status_code == 200

    assert "old-path" not in webhooks._captured
    assert "fresh-path" in webhooks._captured


async def test_webhook_capture_cap_evicts_oldest(client: AsyncClient) -> None:
    from app.routers import webhooks

    webhooks._captured.clear()
    cap = webhooks.WEBHOOK_CAPTURE_MAX_ENTRIES
    base = time.monotonic()
    for i in range(cap):
        webhooks._captured[f"path-{i}"] = (base + i, {"i": i})

    response = await client.post("/webhook-test/path-new", json={"i": "new"})
    assert response.status_code == 200

    assert "path-0" not in webhooks._captured
    assert "path-new" in webhooks._captured
    assert len(webhooks._captured) == cap


# --- Secret cache TTL -------------------------------------------------------


async def test_secret_cache_expires_after_ttl(client: AsyncClient) -> None:
    """A rotated credential value stops being cached after the TTL.

    Bypasses the credential-update endpoint's ``invalidate_secret_cache``
    call by mutating the underlying row directly, so this test isolates the
    TTL-based refresh path (which is the multi-replica safety net).
    """
    from sqlalchemy import select

    from app.models import Credential
    from app.services.crypto import encrypt_credential
    from app.services.runner import SessionLocal  # patched in conftest

    redaction.invalidate_secret_cache()

    cred_resp = await client.post(
        "/credentials",
        json={
            "name": "Rotating",
            "type": "generic",
            "scope": "global",
            "data": {"token": "first-secret-value-1234"},
        },
    )
    assert cred_resp.status_code in (200, 201)
    cred_id = cred_resp.json()["id"]

    async with SessionLocal() as session:
        values = await redaction.load_secret_values(session)
    assert "first-secret-value-1234" in values

    # Direct DB mutation simulates "another replica rotated this credential"
    # — no local invalidate call.
    async with SessionLocal() as session:
        cred = (
            await session.scalars(select(Credential).where(Credential.id == cred_id))
        ).one()
        cred.encrypted_data, cred.encrypted_dek = encrypt_credential(
            {"token": "rotated-secret-value-5678"}
        )
        await session.commit()

    # Without TTL the cache would still return the old value. Force it stale.
    redaction._secret_cache_loaded_at = time.monotonic() - (
        redaction.SECRET_CACHE_TTL_SECONDS + 1
    )

    async with SessionLocal() as session:
        values = await redaction.load_secret_values(session)
    assert "rotated-secret-value-5678" in values
    assert "first-secret-value-1234" not in values


# --- Composite indices ------------------------------------------------------


def test_required_composite_indices_declared_on_models() -> None:
    """Model-side ``__table_args__`` must declare the indices the migration
    adds, so test fixtures using ``Base.metadata.create_all`` get them too."""
    runs_indices = {ix.name for ix in Base.metadata.tables["runs"].indexes}
    node_runs_indices = {
        ix.name for ix in Base.metadata.tables["node_runs"].indexes
    }
    assert "ix_runs_workflow_id_started_at" in runs_indices
    assert "ix_runs_status_started_at" in runs_indices
    assert "ix_node_runs_run_id_node_id" in node_runs_indices


# --- Sub-workflow env isolation ---------------------------------------------


async def test_subworkflow_routes_through_subprocess_for_sub_env(
    client: AsyncClient,
) -> None:
    """When subprocess mode is on, the resolver dispatches the sub through
    its assigned env's subprocess — not the host engine — and forwards the
    resolver + child meta so nested calls keep working (A3)."""
    from app.config import settings as live_settings
    from app.services import runtime_pool as pool_module
    from app.services.subworkflows import resolve_subworkflow
    from noodle.engine.subworkflows import SubworkflowCall

    # Build a sub workflow assigned to a specific environment.
    env = (
        await client.post(
            "/environments",
            json={
                "name": "Sub Env",
                "python_version": "3.12",
                "packages": ["pandas"],
            },
        )
    ).json()
    sub = (await client.post("/workflows", json={"name": "Sub"})).json()
    await client.put(
        f"/workflows/{sub['id']}",
        json={
            "environment_id": env["id"],
            "graph": {
                "nodes": [
                    {"id": "t", "type": "manual_trigger", "params": {},
                     "position": {"x": 0, "y": 0}},
                    {"id": "c", "type": "code",
                     "params": {"code": "output = input"},
                     "position": {"x": 200, "y": 0}},
                ],
                "edges": [{"id": "e", "source": "t", "source_output": "main",
                           "target": "c", "target_input": "input"}],
            },
        },
    )
    # Publish so production-mode loads (use_published=True, the default)
    # see the real graph rather than the empty initial WorkflowVersion.
    await client.post(f"/workflows/{sub['id']}/publish", json={})

    captured: dict[str, object] = {}

    async def fake_dispatch_subworkflow(
        run_id: str,
        env_id: str | None,
        graph: dict,
        cache: dict | None,
        targets: list[str] | None,
        on_event,
        subworkflow_resolver=None,
        subworkflow_meta=None,
        workflow_modules=None,
    ) -> str:
        # Record what the resolver asked for.
        captured["env_id"] = env_id
        captured["targets"] = targets
        captured["sub_run_id"] = run_id
        captured["resolver"] = subworkflow_resolver
        captured["meta"] = subworkflow_meta
        # Mimic a successful sub run: emit node_finished events so leaf
        # extraction returns something useful.
        await on_event({
            "type": "node_finished",
            "node_id": "t",
            "status": "success",
            "outputs": {"main": {"echo": True}},
        })
        await on_event({
            "type": "node_finished",
            "node_id": "c",
            "status": "success",
            "outputs": {"main": {"echo": True}},
        })
        return "success"

    previous_mode = live_settings.use_subprocess_runner
    live_settings.use_subprocess_runner = True
    previous_dispatch = pool_module.pool.dispatch_subworkflow
    pool_module.pool.dispatch_subworkflow = fake_dispatch_subworkflow  # type: ignore[method-assign]
    try:
        call = SubworkflowCall(
            workflow_id=sub["id"], parameters={"echo": True},
            use_published=True, parent_run_id=None, depth=1,
            call_chain=frozenset({"parent-wf", sub["id"]}),
        )
        result = await resolve_subworkflow(call)
    finally:
        pool_module.pool.dispatch_subworkflow = previous_dispatch  # type: ignore[method-assign]
        live_settings.use_subprocess_runner = previous_mode

    assert captured["env_id"] == env["id"]
    # Trigger-gating must restrict execution to the sub's trigger+descendants.
    assert set(captured["targets"]) == {"t", "c"}
    # Nested calls from the spawned child must round-trip through the SAME
    # resolver, carrying the child's meta (depth + extended chain).
    assert captured["resolver"] is resolve_subworkflow
    meta = captured["meta"]
    assert meta is not None and meta.depth == 1
    assert sub["id"] in meta.call_chain
    # Leaf output flows back to the caller.
    assert result == {"echo": True}


# --- Sub-workflow inline-same-subprocess optimization -----------------------


async def _build_sub_with_env(
    client: AsyncClient, env_id: str | None, code: str
) -> str:
    sub = (await client.post("/workflows", json={"name": "InlineSub"})).json()
    body: dict = {
        "graph": {
            "nodes": [
                {"id": "t", "type": "manual_trigger", "params": {},
                 "position": {"x": 0, "y": 0}},
                {"id": "c", "type": "code",
                 "params": {"code": code},
                 "position": {"x": 200, "y": 0}},
            ],
            "edges": [{"id": "e", "source": "t", "source_output": "main",
                       "target": "c", "target_input": "input"}],
        },
    }
    if env_id is not None:
        body["environment_id"] = env_id
    await client.put(f"/workflows/{sub['id']}", json=body)
    await client.post(f"/workflows/{sub['id']}/publish", json={})
    return sub["id"]


def _sub_call(workflow_id: str, value, **kw):
    from noodle.engine.subworkflows import SubworkflowCall

    defaults = dict(
        parameters=value, use_published=True, parent_run_id=None,
        depth=1, call_chain=frozenset({workflow_id}),
    )
    defaults.update(kw)
    return SubworkflowCall(workflow_id=workflow_id, **defaults)


async def test_subworkflow_inline_even_with_nested_workflow_call(
    client: AsyncClient,
) -> None:
    """Same env → InlineSubworkflow directive, INCLUDING when the sub
    contains nested execute_workflow nodes. The pre-A3 restriction is
    lifted: the engine executes inline directives with explicit depth/chain
    meta, so nested calls inside inline children stay cycle-checked."""
    from app.config import settings as live_settings
    from app.services.subworkflows import resolve_subworkflow
    from noodle.engine.subworkflows import InlineSubworkflow

    env = (await client.post(
        "/environments",
        json={"name": "Mixed Env", "python_version": "3.12", "packages": []},
    )).json()

    # A "leaf" sub the nested call would target.
    inner = await _build_sub_with_env(client, env["id"], "output = input")

    # Sub that calls inner — nested execute_workflow, same env → still inline.
    nested = (await client.post(
        "/workflows", json={"name": "Nested"})).json()
    await client.put(
        f"/workflows/{nested['id']}",
        json={
            "environment_id": env["id"],
            "graph": {
                "nodes": [
                    {"id": "t", "type": "manual_trigger", "params": {},
                     "position": {"x": 0, "y": 0}},
                    {"id": "call", "type": "execute_workflow",
                     "params": {"workflow_id": inner},
                     "position": {"x": 200, "y": 0}},
                ],
                "edges": [{"id": "e", "source": "t", "source_output": "main",
                           "target": "call", "target_input": "input"}],
            },
        },
    )
    await client.post(f"/workflows/{nested['id']}/publish", json={})

    previous_mode = live_settings.use_subprocess_runner
    live_settings.use_subprocess_runner = True
    try:
        outcome = await resolve_subworkflow(
            _sub_call(nested["id"], {"x": 1}), parent_env_id=env["id"]
        )
    finally:
        live_settings.use_subprocess_runner = previous_mode

    assert isinstance(outcome, InlineSubworkflow)
    # Trigger-gating still applies.
    assert set(outcome.targets or []) == {"t", "call"}
    # Sources let the engine compute leaves consistently.
    assert "t" in outcome.sources and "call" not in outcome.sources


async def test_subworkflow_spawns_when_env_differs(
    client: AsyncClient,
) -> None:
    """A sub bound to a different env than the parent's still goes through
    the spawn-fresh ``dispatch_subworkflow`` path — never inline."""
    from app.config import settings as live_settings
    from app.services import runtime_pool as pool_module
    from app.services.subworkflows import resolve_subworkflow
    from noodle.engine.subworkflows import InlineSubworkflow

    env = (await client.post(
        "/environments",
        json={"name": "Sub-only Env", "python_version": "3.12", "packages": []},
    )).json()
    sub_id = await _build_sub_with_env(client, env["id"], "output = input")

    captured: dict[str, object] = {}

    async def fake_dispatch(
        run_id, env_id, graph, cache, targets, on_event,
        subworkflow_resolver=None, subworkflow_meta=None, workflow_modules=None,
    ) -> str:
        captured["env_id"] = env_id
        await on_event({"type": "node_finished", "node_id": "t",
                        "status": "success", "outputs": {"main": {}}})
        await on_event({"type": "node_finished", "node_id": "c",
                        "status": "success", "outputs": {"main": "called"}})
        return "success"

    previous_mode = live_settings.use_subprocess_runner
    live_settings.use_subprocess_runner = True
    previous_dispatch = pool_module.pool.dispatch_subworkflow
    pool_module.pool.dispatch_subworkflow = fake_dispatch  # type: ignore[method-assign]
    try:
        outcome = await resolve_subworkflow(
            _sub_call(sub_id, {"x": 1}), parent_env_id="some-other-env"
        )
    finally:
        pool_module.pool.dispatch_subworkflow = previous_dispatch  # type: ignore[method-assign]
        live_settings.use_subprocess_runner = previous_mode

    # Did NOT return an inline directive — went through the spawn path.
    assert not isinstance(outcome, InlineSubworkflow)
    assert outcome == "called"
    assert captured["env_id"] == env["id"]


async def test_subworkflow_pool_writes_inline_response_field(
    client: AsyncClient,
) -> None:
    """Pool callback handler must rebuild the SubworkflowCall from the
    event and serialize an InlineSubworkflow directive into the
    ``inline_*`` fields on the call_workflow_response message."""
    from app.services import runtime_pool as pool_module
    from noodle.engine.subworkflows import InlineSubworkflow, SubworkflowCall

    inline = InlineSubworkflow(
        graph={"nodes": [], "edges": []},
        cache={"t": {"main": {"hi": 1}}},
        targets=["t"],
        sources=("t",),
    )
    seen: dict[str, object] = {}

    async def resolver(call, *, parent_env_id=None) -> object:
        assert isinstance(call, SubworkflowCall)
        assert parent_env_id == "env-xyz"
        seen["call"] = call
        return inline

    # Build a fake _RuntimeProcess just sturdy enough for _handle_call_workflow.
    class FakeProcess:
        env_id = "env-xyz"
        dead = False
        process = object()  # unused on this path
        _write_lock = None  # bypassed by replacing _write_message

        async def _write_message(self, message):
            self.written = message

    fake = FakeProcess()
    fake._write_lock = __import__("asyncio").Lock()  # unused but set

    await pool_module._RuntimeProcess._handle_call_workflow(
        fake,  # type: ignore[arg-type]
        {"callback_id": "cb1", "workflow_id": "wf1", "input": None,
         "use_published": False, "depth": 2, "call_chain": ["wf0", "wf1"]},
        resolver,
    )

    msg = fake.written  # type: ignore[attr-defined]
    assert msg["type"] == "call_workflow_response"
    assert msg["callback_id"] == "cb1"
    assert "inline_graph" in msg
    assert msg["inline_targets"] == ["t"]
    assert msg["inline_sources"] == ["t"]
    assert "result" not in msg
    # The protocol fields round-tripped into a typed call.
    call = seen["call"]
    assert call.workflow_id == "wf1"
    assert call.depth == 2
    assert call.call_chain == frozenset({"wf0", "wf1"})
    assert call.use_published is False


# --- Retention: active runs must not be pruned ------------------------------


async def test_retention_skips_running_runs(client: AsyncClient) -> None:
    """An actively-running (or waiting/queued) run must not be deleted by
    the age-based prune even when it's older than the retention window."""
    from datetime import UTC, datetime, timedelta

    from app.models import Run
    from app.services import retention

    workflow_id = (
        await client.post("/workflows", json={"name": "Long-running"})
    ).json()["id"]
    # Give the workflow a trigger so start_run doesn't reject it.
    await client.put(
        f"/workflows/{workflow_id}",
        json={"graph": {
            "nodes": [{"id": "t", "type": "manual_trigger", "params": {},
                       "position": {"x": 0, "y": 0}}],
            "edges": [],
        }},
    )
    run_resp = await client.post(f"/workflows/{workflow_id}/run", json={})
    assert run_resp.status_code in (200, 202), run_resp.text
    run_id = run_resp.json()["run_id"]

    # Force the run into "running" status and backdate it past retention window.
    async with retention.SessionLocal() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        run.status = "running"
        run.started_at = datetime.now(UTC) - timedelta(days=30)
        await session.commit()

    prev_days = retention.settings.run_retention_days
    retention.settings.run_retention_days = 7
    try:
        aged, _ = await retention.prune_old_runs()
    finally:
        retention.settings.run_retention_days = prev_days

    # The running run must still be in the DB.
    async with retention.SessionLocal() as session:
        still_there = await session.get(Run, run_id)
    assert still_there is not None, "Active run was incorrectly pruned"
    assert aged == 0


# --- Request body size limit -------------------------------------------------


async def test_request_body_too_large_returns_413(client: AsyncClient) -> None:
    """The body-size middleware must reject a body that exceeds the cap."""
    big = "x" * (11 * 1024 * 1024)  # 11 MiB
    response = await client.post(
        "/workflows",
        content=big,
        headers={"Content-Type": "application/json", "Content-Length": str(len(big))},
    )
    assert response.status_code == 413


# --- Credential __repr__ does not leak secrets ------------------------------


def test_credential_repr_omits_sensitive_fields() -> None:
    """Credential.__repr__ must not expose encrypted_data or encrypted_dek."""
    from app.models import Credential

    cred = Credential(
        id="abc123",
        name="My Key",
        type="generic",
        encrypted_data="FERNET_CIPHERTEXT",
        encrypted_dek="WRAPPED_DEK",
    )
    r = repr(cred)
    assert "FERNET_CIPHERTEXT" not in r
    assert "WRAPPED_DEK" not in r
    assert "My Key" in r


# --- CORS wildcard warning in production mode --------------------------------


def test_cors_wildcard_warning_in_production_mode() -> None:
    """A '*' CORS origin must surface a runtime_warnings entry in production."""
    from app.config import Settings

    s = Settings(
        runtime_mode="production",
        cors_origins="*",
        secret_key="strong-secret-value-for-test-only",
        database_url="postgresql+asyncpg://noodle:noodle@localhost/noodle",
    )
    warnings = s.runtime_warnings()
    assert any("wildcard" in w.lower() or "'*'" in w for w in warnings)


def test_default_secret_key_warning_in_production_mode() -> None:
    """The default dev secret key must surface a runtime_warnings entry."""
    from app.config import Settings

    s = Settings(
        runtime_mode="production",
        cors_origins="https://app.example.com",
        database_url="postgresql+asyncpg://noodle:noodle@localhost/noodle",
        # default secret_key left unchanged
    )
    warnings = s.runtime_warnings()
    assert any("secret" in w.lower() for w in warnings)


# --- run_id ContextVar injected into log records ----------------------------


def test_run_id_context_var_injected_into_log_records() -> None:
    """Log records emitted with a run_id ContextVar set must carry run_id."""
    import logging

    from app.services.runner import _RunIdFilter, _log_run_id

    logger = logging.getLogger("noodle.test_run_id")
    handler_records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            handler_records.append(record)

    handler = _Capture()
    logger.addFilter(_RunIdFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

    token = _log_run_id.set("run-abc123")
    try:
        logger.info("something happened")
    finally:
        _log_run_id.reset(token)
        logger.removeHandler(handler)

    assert handler_records, "No log records captured"
    assert getattr(handler_records[0], "run_id", None) == "run-abc123"


# --- Runtime-mode warnings: auth_required + internal_api_token ---------------


def test_auth_required_false_warning_in_production_mode() -> None:
    """auth_required=False in production must surface a warning."""
    from app.config import Settings

    s = Settings(
        _env_file=None,
        runtime_mode="production",
        database_url="postgresql+asyncpg://u:p@h/db",
        artifact_storage_backend="s3",
        artifact_s3_bucket="bucket",
        queue_backend="redis",
        secret_key="strong-production-secret-xyz",
        cors_origins="https://app.example.com",
        auth_required=False,
    )
    warnings = s.runtime_warnings()
    assert any("auth_required" in w.lower() or "authentication" in w.lower() for w in warnings)


def test_internal_api_token_empty_warning_in_production_mode() -> None:
    """internal_api_token='' in production must surface a warning."""
    from app.config import Settings

    s = Settings(
        _env_file=None,
        runtime_mode="production",
        database_url="postgresql+asyncpg://u:p@h/db",
        artifact_storage_backend="s3",
        artifact_s3_bucket="bucket",
        queue_backend="redis",
        secret_key="strong-production-secret-xyz",
        cors_origins="https://app.example.com",
        internal_api_token="",
    )
    warnings = s.runtime_warnings()
    assert any("internal_api_token" in w.lower() for w in warnings)


# --- Scheduler loop: exception logging + backoff -----------------------------


def test_scheduler_loop_logs_tick_failures(monkeypatch) -> None:
    """scheduler_loop must log exceptions from _tick instead of silently passing."""
    import asyncio
    import logging

    from app.services import triggers as triggers_module

    logged: list[str] = []

    class _CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            logged.append(record.getMessage())

    handler = _CapturingHandler()
    triggers_module.logger.addHandler(handler)
    triggers_module.logger.setLevel(logging.ERROR)

    call_count = 0

    async def boom():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("db is down")
        # Stop the loop after second call
        raise asyncio.CancelledError()

    monkeypatch.setattr(triggers_module, "_tick", boom)

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    try:
        asyncio.run(triggers_module.scheduler_loop())
    except asyncio.CancelledError:
        pass
    finally:
        triggers_module.logger.removeHandler(handler)

    assert call_count >= 1
    assert any("db is down" in m or "tick" in m.lower() for m in logged), (
        f"Expected exception logged; got: {logged}"
    )


def test_scheduler_loop_sleeps_longer_after_failure(monkeypatch) -> None:
    """After a tick failure the scheduler must sleep longer (backoff > 0)."""
    import asyncio

    from app.services import triggers as triggers_module

    call_count = 0

    async def boom():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient error")
        raise asyncio.CancelledError()

    monkeypatch.setattr(triggers_module, "_tick", boom)

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    try:
        asyncio.run(triggers_module.scheduler_loop())
    except asyncio.CancelledError:
        pass

    assert sleeps, "No sleeps recorded"
    # First sleep (after failure) must be longer than baseline 30 s
    assert sleeps[0] > 30, f"Expected backoff > 30s after failure, got {sleeps[0]}"


# --- Retention loop: exception logging ----------------------------------------


def test_retention_loop_logs_exceptions(monkeypatch) -> None:
    """retention_loop must log exceptions instead of silently passing them."""
    import asyncio
    import logging

    from app.services import retention as retention_module

    logged: list[str] = []

    class _Cap(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            logged.append(record.getMessage())

    import logging as _logging
    rt_logger = _logging.getLogger("noodle.retention")
    # The module may use getLogger(__name__) which resolves to app.services.retention
    mod_logger = _logging.getLogger("app.services.retention")

    for lg in (rt_logger, mod_logger, retention_module.logger if hasattr(retention_module, "logger") else mod_logger):
        lg.addHandler(_Cap())
        lg.setLevel(logging.ERROR)

    call_count = 0

    async def boom(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("retention db error")
        raise asyncio.CancelledError()

    monkeypatch.setattr(retention_module, "prune_old_runs", boom)

    async def fake_sleep(seconds: float) -> None:
        pass

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    try:
        asyncio.run(retention_module.retention_loop())
    except asyncio.CancelledError:
        pass

    assert any("retention" in m.lower() or "tick" in m.lower() for m in logged), (
        f"Expected retention exception logged; got: {logged}"
    )


# --- _graph_for_run: warn when pinned version is missing ----------------------


async def test_graph_for_run_logs_warning_when_version_missing(
    client: AsyncClient,
) -> None:
    """_graph_for_run must log a warning when the pinned WorkflowVersion row
    is gone (deleted or corrupted FK) and falls back to the draft graph."""
    import logging

    from app.models import Run, WorkflowVersion
    from app.routers.runs import _graph_for_run
    from app.services.runner import SessionLocal

    wf_resp = await client.post("/workflows", json={"name": "VersionTest"})
    wf_id = wf_resp.json()["id"]

    # Publish to create a version row
    await client.post(f"/workflows/{wf_id}/publish", json={})

    captured_warnings: list[str] = []

    class _WarnCap(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.levelno >= logging.WARNING:
                captured_warnings.append(record.getMessage())

    import logging as _logging
    runs_logger = _logging.getLogger("app.routers.runs")
    handler = _WarnCap()
    runs_logger.addHandler(handler)
    runs_logger.setLevel(logging.WARNING)

    try:
        async with SessionLocal() as session:
            from app.models import Workflow
            from sqlalchemy.orm import selectinload
            from sqlalchemy import select

            wf = await session.scalar(
                select(Workflow)
                .where(Workflow.id == wf_id)
                .options(selectinload(Workflow.versions))
            )
            assert wf is not None

            # Build a Run with a fake (non-existent) version id
            fake_run = Run(
                id="fake-run-001",
                workflow_id=wf_id,
                workflow_version=1,
                workflow_version_id="nonexistent-version-id",
                status="running",
            )

            graph, version_num, version_id = await _graph_for_run(session, fake_run, wf)
    finally:
        runs_logger.removeHandler(handler)

    assert any("nonexistent-version-id" in w or "version" in w.lower() for w in captured_warnings), (
        f"Expected version-missing warning; got: {captured_warnings}"
    )


# --- #20: Redis-backed auth rate limiter -------------------------------------


def test_enforce_auth_rate_limit_is_async() -> None:
    """_enforce_auth_rate_limit must be a coroutine (async def) so it can
    make Redis INCR/EXPIRE calls when queue_backend=redis."""
    import asyncio
    from app.routers.auth import _enforce_auth_rate_limit

    assert asyncio.iscoroutinefunction(_enforce_auth_rate_limit), (
        "_enforce_auth_rate_limit must be declared async"
    )


async def test_enforce_auth_rate_limit_uses_redis_incr_when_backend_configured(
    monkeypatch,
) -> None:
    """When queue_backend=redis, rate-limit must use Redis INCR+EXPIRE instead
    of the in-process deque so all replicas share one counter."""
    import app.redis_client as redis_module
    from app.config import settings as live_settings
    from app.routers import auth as auth_module

    incr_calls: list[str] = []
    expire_calls: list = []

    class FakeRedis:
        async def incr(self, key: str) -> int:
            incr_calls.append(key)
            return 1

        async def expire(self, key: str, ttl: int) -> None:
            expire_calls.append((key, ttl))

    monkeypatch.setattr(live_settings, "queue_backend", "redis")
    monkeypatch.setattr(live_settings, "auth_rate_limit_enabled", True)
    monkeypatch.setattr(live_settings, "auth_rate_limit_per_minute", 10)
    monkeypatch.setattr(redis_module, "redis_client", FakeRedis())

    class FakeRequest:
        client = type("C", (), {"host": "10.0.0.1"})()

    await auth_module._enforce_auth_rate_limit(FakeRequest(), "login")

    assert incr_calls, "Redis INCR should have been called"
    assert any("noodle:rl" in k for k in incr_calls), (
        f"INCR key should be namespaced 'noodle:rl:…'; got: {incr_calls}"
    )
    assert expire_calls, "Redis EXPIRE should set TTL on first attempt"


# --- #5: dispatch_webhook shared session for credential lookup ----------------


async def test_dispatch_webhook_passes_shared_session_to_resolve_node_auth(
    monkeypatch,
) -> None:
    """dispatch_webhook must pass a shared DB session to _resolve_node_auth so
    credential resolution doesn't open a new connection per webhook node."""
    from app.services import triggers as triggers_module

    captured_sessions: list = []

    async def spy_resolve(params, workflow_id, environment_id, session=None):
        captured_sessions.append(session)
        return {}

    async def fake_active_workflows():
        class FakeVer:
            version = 1
            id = "ver-x"
            graph = {
                "nodes": [{
                    "id": "wh1",
                    "type": "webhook_trigger",
                    "params": {"path": "test-dispatch-session"},
                    "position": {"x": 0, "y": 0},
                }],
                "edges": [],
            }

        class FakeWF:
            id = "wf-x"
            environment_id = None
            versions = [FakeVer()]

        return [FakeWF()]

    async def fake_start_run(*args, **kwargs):
        return "run-fake-id"

    monkeypatch.setattr(triggers_module, "_active_workflows", fake_active_workflows)
    monkeypatch.setattr(triggers_module, "_resolve_node_auth", spy_resolve)
    monkeypatch.setattr(triggers_module, "_webhook_dedup_key", lambda p, r: None)

    import app.services.runner as runner_module
    monkeypatch.setattr(runner_module, "start_run", fake_start_run)

    await triggers_module.dispatch_webhook(
        "test-dispatch-session",
        {"method": "GET", "headers": {}, "query": {}, "body": None, "received_at": ""},
        raw_body=None,
    )

    assert captured_sessions, "_resolve_node_auth was never called during dispatch"
    assert all(s is not None for s in captured_sessions), (
        "dispatch_webhook must pass a non-None shared session to _resolve_node_auth; "
        f"got sessions={captured_sessions}"
    )
