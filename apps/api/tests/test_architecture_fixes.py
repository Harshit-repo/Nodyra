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
    """When subprocess mode is on, ``_call_sub_workflow`` dispatches the
    sub through its assigned env's subprocess — not the host engine — so
    the sub runs against its own env's installed packages."""
    from app.config import settings as live_settings
    from app.services import runner as runner_module
    from noodle.context import call_chain

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
    # Publish so production-mode loads (default of `_prefer_draft_graphs`)
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
        sub_workflow_caller=None,
        workflow_modules=None,
    ) -> str:
        # Record what the router asked for.
        captured["env_id"] = env_id
        captured["targets"] = targets
        captured["sub_run_id"] = run_id
        # Mimic a successful sub run: emit one node_finished event for the
        # leaf so ``_extract_sub_leaf`` returns something useful.
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
    previous_dispatch = runner_module.runtime_pool.dispatch_subworkflow
    runner_module.runtime_pool.dispatch_subworkflow = fake_dispatch_subworkflow  # type: ignore[method-assign]
    try:
        token = call_chain.set(frozenset({"parent-run"}))
        try:
            result = await runner_module._call_sub_workflow(
                sub["id"], {"echo": True}
            )
        finally:
            call_chain.reset(token)
    finally:
        runner_module.runtime_pool.dispatch_subworkflow = previous_dispatch  # type: ignore[method-assign]
        live_settings.use_subprocess_runner = previous_mode

    assert captured["env_id"] == env["id"]
    # Trigger-gating must restrict execution to the sub's trigger+descendants.
    assert set(captured["targets"]) == {"t", "c"}
    # Leaf output flows back to the caller.
    assert result == {"echo": True}


async def test_subworkflow_in_process_path_still_works(
    client: AsyncClient,
) -> None:
    """In-process fallback (subprocess runner off — the test default) must
    keep returning the leaf's ``main`` output via the host engine."""
    from app.services import runner as runner_module
    from noodle.context import call_chain

    sub = (await client.post("/workflows", json={"name": "Sub"})).json()
    await client.put(
        f"/workflows/{sub['id']}",
        json={
            "graph": {
                "nodes": [
                    {"id": "t", "type": "manual_trigger", "params": {},
                     "position": {"x": 0, "y": 0}},
                    {"id": "c", "type": "code",
                     "params": {"code": "output = input['n'] * 2"},
                     "position": {"x": 200, "y": 0}},
                ],
                "edges": [{"id": "e", "source": "t", "source_output": "main",
                           "target": "c", "target_input": "input"}],
            },
        },
    )
    await client.post(f"/workflows/{sub['id']}/publish", json={})

    token = call_chain.set(frozenset({"parent-run"}))
    try:
        result = await runner_module._call_sub_workflow(sub["id"], {"n": 7})
    finally:
        call_chain.reset(token)

    assert result == 14


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


async def test_subworkflow_inline_when_same_env_and_no_nested_calls(
    client: AsyncClient,
) -> None:
    """Same env + no nested execute_workflow → InlineSubWorkflow returned
    instead of spawning a fresh subprocess. The pool's callback handler
    will forward the sub graph to the parent's existing subprocess."""
    from app.config import settings as live_settings
    from app.services import runner as runner_module
    from noodle.context import call_chain

    env = (await client.post(
        "/environments",
        json={"name": "Inline Env", "python_version": "3.12", "packages": []},
    )).json()
    sub_id = await _build_sub_with_env(client, env["id"], "output = input")

    previous_mode = live_settings.use_subprocess_runner
    live_settings.use_subprocess_runner = True
    try:
        token = call_chain.set(frozenset({"parent-run"}))
        try:
            outcome = await runner_module._call_sub_workflow(
                sub_id, {"hello": "world"}, parent_env_id=env["id"]
            )
        finally:
            call_chain.reset(token)
    finally:
        live_settings.use_subprocess_runner = previous_mode

    assert isinstance(outcome, runner_module.InlineSubWorkflow)
    # Trigger-gating still applies.
    assert set(outcome.targets) == {"t", "c"}
    # Sources list lets the runtime compute leaves consistently.
    assert "t" in outcome.sources and "c" not in outcome.sources


async def test_subworkflow_spawns_when_sub_has_nested_workflow_call(
    client: AsyncClient,
) -> None:
    """A sub containing execute_workflow is ineligible for inline (cycle
    chain bookkeeping would need to follow the inline path back), so we
    must fall back to the spawn-fresh subprocess path."""
    from app.config import settings as live_settings
    from app.services import runner as runner_module
    from noodle.context import call_chain

    env = (await client.post(
        "/environments",
        json={"name": "Mixed Env", "python_version": "3.12", "packages": []},
    )).json()

    # A "leaf" sub the nested call would target — content doesn't matter
    # because we won't actually dispatch (the spawn is monkeypatched).
    inner = await _build_sub_with_env(client, env["id"], "output = input")

    # Sub that calls inner — has execute_workflow → ineligible for inline.
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

    captured: dict[str, object] = {}

    async def fake_dispatch(
        run_id, env_id, graph, cache, targets, on_event,
        sub_workflow_caller=None, workflow_modules=None,
    ) -> str:
        captured["env_id"] = env_id
        # Mimic a finished sub: emit minimal events so leaf extraction works.
        await on_event({"type": "node_finished", "node_id": "t",
                        "status": "success", "outputs": {"main": {}}})
        await on_event({"type": "node_finished", "node_id": "call",
                        "status": "success", "outputs": {"main": "called"}})
        return "success"

    previous_mode = live_settings.use_subprocess_runner
    live_settings.use_subprocess_runner = True
    previous_dispatch = runner_module.runtime_pool.dispatch_subworkflow
    runner_module.runtime_pool.dispatch_subworkflow = fake_dispatch  # type: ignore[method-assign]
    try:
        token = call_chain.set(frozenset({"parent-run"}))
        try:
            outcome = await runner_module._call_sub_workflow(
                nested["id"], {"x": 1}, parent_env_id=env["id"]
            )
        finally:
            call_chain.reset(token)
    finally:
        runner_module.runtime_pool.dispatch_subworkflow = previous_dispatch  # type: ignore[method-assign]
        live_settings.use_subprocess_runner = previous_mode

    # Did NOT return InlineSubWorkflow — went through spawn path.
    assert not isinstance(outcome, runner_module.InlineSubWorkflow)
    assert outcome == "called"
    assert captured["env_id"] == env["id"]


async def test_subworkflow_pool_writes_inline_response_field(
    client: AsyncClient,
) -> None:
    """Pool callback handler must serialize an InlineSubWorkflow into the
    new ``inline_*`` fields on the call_workflow_response message."""
    from app.services import runner as runner_module
    from app.services import runtime_pool as pool_module

    inline = runner_module.InlineSubWorkflow(
        graph={"nodes": [], "edges": []},
        cache={"t": {"main": {"hi": 1}}},
        targets=["t"],
        sources=["t"],
    )

    async def caller(workflow_id, input_value, *, parent_env_id=None) -> object:
        assert parent_env_id == "env-xyz"
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
        {"callback_id": "cb1", "workflow_id": "wf1", "input": None},
        caller,
    )

    msg = fake.written  # type: ignore[attr-defined]
    assert msg["type"] == "call_workflow_response"
    assert msg["callback_id"] == "cb1"
    assert "inline_graph" in msg
    assert msg["inline_targets"] == ["t"]
    assert msg["inline_sources"] == ["t"]
    assert "result" not in msg
