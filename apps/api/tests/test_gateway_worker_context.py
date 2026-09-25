"""Trusted actor propagation and the managed-worker gateway boundary."""

import asyncio
import copy
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import settings
from app.models import (
    Organization,
    ProviderTriggerSubscription,
    Run,
    RunQueueEntry,
    ScheduleState,
    User,
    Workflow,
    WorkflowVersion,
)
from app.services import (
    provider_triggers,
    runner,
    runtime_pool,
    sandbox_pool,
    subworkflows,
    triggers,
)
from app.services.execution_actor import (
    ExecutionActor,
    ExecutionActorMiddleware,
    ExecutionAttempt,
    bind_authenticated_actor,
    current_execution_actor,
    current_execution_attempt,
    execution_graph_digest,
    validate_execution_attempt,
)
from nodyra.engine.subworkflows import SubworkflowCall


def _graph() -> dict:
    return {
        "nodes": [{"id": "trigger", "type": "manual_trigger", "params": {}}],
        "edges": [],
    }


async def test_actor_context_isolated_between_requests_and_reset_after_error():
    both_entered = asyncio.Event()
    entered = []
    observed = {}

    async def endpoint(scope, receive, send):
        actor_id = scope["path"].lstrip("/")
        assert current_execution_actor.get() == ExecutionActor(kind="anonymous")
        bind_authenticated_actor(actor_id)
        entered.append(actor_id)
        if len(entered) == 2:
            both_entered.set()
        await both_entered.wait()
        observed[actor_id] = current_execution_actor.get()
        if actor_id == "failed-user":
            raise ValueError("request failed")
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    outer = current_execution_actor.set(ExecutionActor(id="outer", kind="user"))
    try:
        async with AsyncClient(
            transport=ASGITransport(app=ExecutionActorMiddleware(endpoint)),
            base_url="http://test",
        ) as client:
            results = await asyncio.gather(
                client.get("/first-user"), client.get("/failed-user"),
                return_exceptions=True,
            )
        assert results[0].status_code == 204
        assert isinstance(results[1], ValueError)
        assert observed == {
            "first-user": ExecutionActor(id="first-user", kind="user"),
            "failed-user": ExecutionActor(id="failed-user", kind="user"),
        }
        assert current_execution_actor.get() == ExecutionActor(id="outer", kind="user")
    finally:
        current_execution_actor.reset(outer)


async def test_authenticated_run_persists_actor_without_leaking_to_next_run(client, monkeypatch):
    from app.main import app

    monkeypatch.setattr(settings, "auth_required", False)
    monkeypatch.setattr(settings, "run_synchronously", False)
    monkeypatch.setattr(settings, "dispatch_role", "disabled")
    registered = await client.post(
        "/auth/register",
        json={
            "name": "Gateway Owner", "company": "Nodyra",
            "email": "gateway-actor@nodyra.test", "password": "supersecret",
        },
    )
    assert registered.status_code == 201
    bearer = {"Authorization": f"Bearer {registered.json()['token']}"}
    workflow = (await client.post("/workflows", headers=bearer, json={"name": "Actor run"})).json()
    updated = await client.put(
        f"/workflows/{workflow['id']}", headers=bearer, json={"graph": _graph()},
    )
    assert updated.status_code == 200
    # Explicitly wrap the real app so this also exercises the middleware when
    # the router is embedded independently from Nodyra's normal app factory.
    async with AsyncClient(
        transport=ASGITransport(app=ExecutionActorMiddleware(app)),
        base_url="http://test",
    ) as actor_client:
        authenticated = await actor_client.post(
            f"/workflows/{workflow['id']}/run", headers=bearer,
            json={"initiator_id": "forged", "initiator_kind": "system"},
        )
        assert authenticated.status_code == 202
        anonymous = await actor_client.post(f"/workflows/{workflow['id']}/run", json={})
        assert anonymous.status_code == 202
    async with runner.SessionLocal() as session:
        user = await session.scalar(select(User).where(User.email == "gateway-actor@nodyra.test"))
        run = await session.get(Run, authenticated.json()["run_id"])
        other = await session.get(Run, anonymous.json()["run_id"])
        stored_workflow = await session.get(Workflow, workflow["id"])
        assert (run.initiator_id, run.initiator_kind) == (user.id, "user")
        assert run.execution_graph_digest == execution_graph_digest(stored_workflow.draft_graph)
        assert (other.initiator_id, other.initiator_kind) == (None, "anonymous")
    outer = current_execution_actor.set(ExecutionActor())
    try:
        system_id = await runner.start_run(workflow["id"], _graph(), 1, trigger_type="schedule")
    finally:
        current_execution_actor.reset(outer)
    async with runner.SessionLocal() as session:
        system = await session.get(Run, system_id)
        assert (system.initiator_id, system.initiator_kind) == (None, "system")


async def test_verified_webhook_binds_system_actor_only_after_signature_passes(client, monkeypatch):
    from tests.test_triggers import _webhook_graph_with_auth

    monkeypatch.setattr(settings, "run_synchronously", False)
    monkeypatch.setattr(settings, "dispatch_role", "disabled")
    workflow = (await client.post("/workflows", json={"name": "Verified automation"})).json()
    graph = _webhook_graph_with_auth("gateway-signed", {
        "auth_type": "none", "hmac_verification": "on", "hmac_header": "X-Signature",
        "hmac_secret": "test-signing-secret", "hmac_algorithm": "sha256", "hmac_prefix": "sha256=",
    })
    await client.put(f"/workflows/{workflow['id']}", json={"graph": graph, "active": True})
    published = await client.post(f"/workflows/{workflow['id']}/publish", json={})
    assert published.status_code == 200
    body = b'{"payload":"verified"}'
    invalid = await client.post("/webhook/gateway-signed", content=body, headers={
        "X-Signature": "sha256=invalid", "Content-Type": "application/json",
    })
    assert invalid.status_code == 401
    async with runner.SessionLocal() as session:
        assert await session.scalar(select(Run.id).where(Run.workflow_id == workflow["id"])) is None
    signature = "sha256=" + hmac.new(b"test-signing-secret", body, hashlib.sha256).hexdigest()
    outer = current_execution_actor.set(ExecutionActor(id="ambient-admin", kind="user"))
    try:
        accepted = await client.post("/webhook/gateway-signed", content=body, headers={
            "X-Signature": signature, "Content-Type": "application/json",
        })
        assert accepted.status_code == 200
        assert current_execution_actor.get() == ExecutionActor(id="ambient-admin", kind="user")
    finally:
        current_execution_actor.reset(outer)
    async with runner.SessionLocal() as session:
        run = await session.get(Run, accepted.json()["runs"][0])
        version = await session.get(WorkflowVersion, published.json()["workflow_version_id"])
        assert (run.initiator_id, run.initiator_kind) == (None, "system")
        assert run.workflow_version_id == version.id
        assert run.execution_graph_digest == execution_graph_digest(version.graph)


async def test_verified_provider_webhook_binds_system_actor_after_verification(client, monkeypatch):
    from tests.test_triggers import (
        _FakeGithubTransport,
        _github_headers,
        _github_trigger_graph,
        github_triggers,
    )

    monkeypatch.setattr(settings, "run_synchronously", False)
    monkeypatch.setattr(settings, "dispatch_role", "disabled")
    monkeypatch.setattr(github_triggers, "_transport", lambda _credentials: _FakeGithubTransport())
    workflow = (await client.post("/workflows", json={"name": "Verified provider"})).json()
    await client.put(f"/workflows/{workflow['id']}", json={"graph": _github_trigger_graph(), "active": True})
    published = await client.post(f"/workflows/{workflow['id']}/publish", json={})
    assert published.status_code == 200
    async with provider_triggers.SessionLocal() as session:
        subscription = await session.scalar(select(ProviderTriggerSubscription).where(
            ProviderTriggerSubscription.workflow_id == workflow["id"],
        ))
        subscription_id = subscription.id
    body = json.dumps({"repository": {"full_name": "octocat/hello-world"}}).encode()
    headers = _github_headers(body)
    invalid = await client.post(f"/provider-webhook/{subscription_id}", content=body, headers={
        **headers, "X-Hub-Signature-256": "sha256=invalid",
    })
    assert invalid.status_code == 401
    async with runner.SessionLocal() as session:
        assert await session.scalar(select(Run.id).where(Run.workflow_id == workflow["id"])) is None
    accepted = await client.post(f"/provider-webhook/{subscription_id}", content=body, headers=headers)
    assert accepted.status_code == 202
    async with runner.SessionLocal() as session:
        run = await session.get(Run, accepted.json()["runs"][0])
        version = await session.get(WorkflowVersion, published.json()["workflow_version_id"])
        assert (run.initiator_id, run.initiator_kind) == (None, "system")
        assert run.workflow_version_id == version.id
        assert run.execution_graph_digest == execution_graph_digest(version.graph)


async def test_scheduled_automation_does_not_inherit_ambient_user(client, monkeypatch):
    monkeypatch.setattr(settings, "run_synchronously", False)
    monkeypatch.setattr(settings, "dispatch_role", "disabled")
    workflow = (await client.post("/workflows", json={"name": "Scheduled automation"})).json()
    graph = {"nodes": [{
        "id": "timer", "type": "schedule_trigger", "params": {"interval": "minutes", "every": 1},
    }], "edges": []}
    await client.put(f"/workflows/{workflow['id']}", json={"graph": graph, "active": True})
    await client.post(f"/workflows/{workflow['id']}/publish", json={})
    await triggers._tick()
    async with triggers.SessionLocal() as session:
        state = await session.scalar(select(ScheduleState).where(ScheduleState.workflow_id == workflow["id"]))
        state.last_fired = datetime.now(UTC) - timedelta(minutes=5)
        await session.commit()
    outer = current_execution_actor.set(ExecutionActor(id="ambient-admin", kind="user"))
    try:
        await triggers._tick()
        assert current_execution_actor.get() == ExecutionActor(id="ambient-admin", kind="user")
    finally:
        current_execution_actor.reset(outer)
    async with runner.SessionLocal() as session:
        run = (await session.scalars(select(Run).where(Run.workflow_id == workflow["id"]))).one()
        assert (run.initiator_id, run.initiator_kind) == (None, "system")


@pytest.mark.parametrize("same_org", [True, False])
async def test_child_run_inherits_only_matching_parent_identity(client, same_org):
    workflow = (await client.post("/workflows", json={"name": "Child identity"})).json()
    async with runner.SessionLocal() as session:
        if not same_org:
            session.add(Organization(id="other", name="Other", slug="other"))
            await session.flush()
        parent = Run(
            workflow_id=workflow["id"], org_id="default" if same_org else "other",
            initiator_id="parent-user", initiator_kind="user", status="success",
        )
        session.add(parent)
        await session.commit()
        parent_id = parent.id
    child_id = await subworkflows._create_child_run(
        SubworkflowCall(
            workflow_id=workflow["id"], parameters=None, use_published=True,
            parent_run_id=parent_id, depth=1, org_id="default",
        ),
        workflow["id"],
    )
    async with runner.SessionLocal() as session:
        child = await session.get(Run, child_id)
        assert (child.initiator_id, child.initiator_kind) == (
            ("parent-user", "user") if same_org else (None, "system")
        )


@pytest.mark.parametrize("worker_kind", ["subprocess", "sandbox"])
@pytest.mark.parametrize("denied", [False, True])
async def test_worker_uses_host_run_and_gateway_even_when_worker_forges_identity(
    client, monkeypatch, worker_kind, denied,
):
    from app import db
    from app.services import mcp_gateway

    gateway = AsyncMock(side_effect=ValueError("policy denied") if denied else None)
    gateway.return_value = {"accepted": True}
    monkeypatch.setattr(mcp_gateway, "execute_for_run", gateway)
    monkeypatch.setattr(db, "SessionLocal", runner.SessionLocal)
    event = {
        "callback_id": "callback-1", "connection_id": "connection-1",
        "tool_name": "write", "arguments": {"value": 1},
        "run_id": "forged-run", "actor_id": "forged-actor", "org_id": "forged-org",
    }
    send = AsyncMock()
    if worker_kind == "subprocess":
        worker = SimpleNamespace(_write_message=send)
        await runtime_pool._RuntimeProcess._handle_call_mcp_tool(worker, event, "host-run")
    else:
        worker = SimpleNamespace(_send=send)
        await sandbox_pool.SandboxWorker._handle_call_mcp_tool(
            worker, event, asyncio.get_running_loop(), run_id="host-run", org_id="host-org",
        )
    gateway.assert_awaited_once()
    assert gateway.await_args.args[1:] == ("connection-1", "write", {"value": 1})
    assert gateway.await_args.kwargs == {"run_id": "host-run"}
    response = send.await_args.args[0]
    assert response["callback_id"] == "callback-1"
    assert response["type"] == ("call_mcp_tool_error" if denied else "call_mcp_tool_response")
    if denied:
        assert "policy denied" in response["error"]
    else:
        assert response["result"] == {"accepted": True}


@pytest.mark.parametrize("worker_kind", ["subprocess", "sandbox"])
async def test_subworkflow_callback_replaces_worker_supplied_parent_and_org(
    client, worker_kind,
):
    workflow = (await client.post("/workflows", json={"name": "Host parent"})).json()
    async with runner.SessionLocal() as session:
        parent = Run(workflow_id=workflow["id"], org_id="default", status="running")
        session.add(parent)
        await session.commit()
        host_run_id = parent.id
    resolver = AsyncMock(return_value="done")
    event = {
        "callback_id": "child-call", "workflow_id": workflow["id"],
        "parent_run_id": "forged-parent", "org_id": "forged-org", "input": None,
    }
    send = AsyncMock()
    if worker_kind == "subprocess":
        await runtime_pool._RuntimeProcess._handle_call_workflow(
            SimpleNamespace(_write_message=send, env_id=None), event, resolver,
            run_id=host_run_id,
        )
    else:
        await sandbox_pool.SandboxWorker._handle_call_workflow(
            SimpleNamespace(_send=send, key=("default", None, "")),
            event, resolver, asyncio.get_running_loop(), run_id=host_run_id,
        )
    resolver.assert_awaited_once()
    call = resolver.await_args.args[0]
    assert call.parent_run_id == host_run_id
    assert call.org_id == "default"
    assert send.await_args.args[0]["type"] == "call_workflow_response"


async def test_inprocess_child_gateway_uses_child_snapshot_and_restores_parent_callback(
    client, monkeypatch,
):
    from app.services import mcp_gateway
    from nodyra.engine.types import _call_mcp_tool_impl

    monkeypatch.setattr(settings, "mcp_gateway_enabled", True)
    workflow = (await client.post("/workflows", json={"name": "Child MCP identity"})).json()
    graph = _graph()
    graph["nodes"].append({
        "id": "mcp", "type": "mcp_tool",
        "params": {"connection_id": "conn", "tool_name": "write", "arguments": {"x": 1}},
    })
    graph["edges"].append({
        "id": "edge", "source": "trigger", "source_output": "main",
        "target": "mcp", "target_input": "input",
    })
    async with runner.SessionLocal() as session:
        version = await session.scalar(
            select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow["id"])
        )
        version.graph = graph
        parent = Run(
            workflow_id=workflow["id"], initiator_id="parent-user", initiator_kind="user",
            org_id="default", status="running",
        )
        session.add(parent)
        await session.commit()
        parent_id, version_id = parent.id, version.id
    gateway = AsyncMock(return_value={"receipt": "provider-report"})
    monkeypatch.setattr(mcp_gateway, "execute_for_run", gateway)
    parent_callback = AsyncMock(side_effect=AssertionError("child reused parent callback"))
    callback_token = _call_mcp_tool_impl.set(parent_callback)
    try:
        result = await subworkflows.resolve_subworkflow(SubworkflowCall(
            workflow_id=workflow["id"], parameters=None, use_published=True,
            parent_run_id=parent_id, depth=1, org_id="default",
        ))
        assert result["receipt"] == "provider-report"
        assert _call_mcp_tool_impl.get() is parent_callback
    finally:
        _call_mcp_tool_impl.reset(callback_token)
    gateway.assert_awaited_once()
    child_id = gateway.await_args.kwargs["run_id"]
    assert child_id != parent_id
    async with runner.SessionLocal() as session:
        child = await session.get(Run, child_id)
        assert child.parent_run_id == parent_id
        assert (child.initiator_id, child.initiator_kind) == ("parent-user", "user")
        assert child.workflow_version_id == version_id
        assert child.execution_graph_digest == execution_graph_digest(graph)
        assert child.status == "success"


async def test_gateway_disables_inline_child_shortcut(client, monkeypatch):
    from nodyra.engine.subworkflows import InlineSubworkflow

    monkeypatch.setattr(settings, "mcp_gateway_enabled", True)
    monkeypatch.setattr(settings, "use_subprocess_runner", True)
    workflow = (await client.post("/workflows", json={"name": "Isolated child"})).json()
    await client.put(f"/workflows/{workflow['id']}", json={"graph": _graph()})
    dispatch = AsyncMock(return_value="success")
    monkeypatch.setattr(sandbox_pool.pool, "dispatch", dispatch)
    result = await subworkflows.resolve_subworkflow(
        SubworkflowCall(
            workflow_id=workflow["id"], parameters=None, use_published=False,
            parent_run_id=None, depth=1, org_id="default",
        ),
        parent_env_id=None, parent_sandboxed=True,
    )
    assert not isinstance(result, InlineSubworkflow)
    dispatch.assert_awaited_once()


async def test_gateway_replay_rejects_draft_changed_after_admission(client, monkeypatch):
    monkeypatch.setattr(settings, "mcp_gateway_enabled", True)
    monkeypatch.setattr(settings, "run_synchronously", False)
    monkeypatch.setattr(settings, "dispatch_role", "disabled")
    monkeypatch.setattr(settings, "queue_backend", "none")
    workflow = (await client.post("/workflows", json={"name": "Replay guard"})).json()
    await client.put(f"/workflows/{workflow['id']}", json={"graph": _graph()})
    published = await client.post(f"/workflows/{workflow['id']}/publish", json={})
    assert published.status_code == 200
    version_id = published.json()["workflow_version_id"]
    async with runner.SessionLocal() as session:
        version = await session.get(WorkflowVersion, version_id)
        admitted_graph = copy.deepcopy(version.graph)
        version_number = version.version
    run_id = await runner.start_run(
        workflow["id"], admitted_graph, version_number,
        workflow_version_id=version_id, mode="test",
    )
    async with runner.SessionLocal() as session:
        stored_workflow = await session.get(Workflow, workflow["id"])
        changed_graph = copy.deepcopy(admitted_graph)
        changed_graph["nodes"][0]["params"] = {"data": {"changed": True}}
        stored_workflow.draft_graph = changed_graph
        await session.commit()
    preparation = AsyncMock(side_effect=AssertionError("Changed workflow reached credential resolution"))
    monkeypatch.setattr(runner, "_prepare_run_context", preparation)
    await runner._execute_queued_entry(run_id)
    preparation.assert_not_awaited()
    async with runner.SessionLocal() as session:
        run = await session.get(Run, run_id)
        assert run.status == "error"
        assert "changed after run admission" in run.error
        assert run.execution_graph_digest == execution_graph_digest(admitted_graph)


async def test_published_api_run_matches_approved_gateway_snapshot(client, monkeypatch, httpx_mock):
    from app.services import mcp_gateway
    from tests.test_mcp_transport_hardening import Server

    monkeypatch.setattr(settings, "mcp_gateway_enabled", True)
    monkeypatch.setattr(settings, "queue_backend", "none")
    peer = Server(httpx_mock)
    registered = await client.post("/auth/register", json={
        "name": "Gateway Owner", "company": "Nodyra",
        "email": "published-gateway@nodyra.test", "password": "supersecret",
    })
    assert registered.status_code == 201
    headers = {"Authorization": f"Bearer {registered.json()['token']}"}
    connection = await client.post("/mcp-connections", headers=headers, json={
        "name": "Published gateway", "url": "https://mcp.example.com/mcp",
        "allowed_tools": ["write_record"],
    })
    assert connection.status_code == 201
    connection_id = connection.json()["id"]
    workflow = (await client.post("/workflows", headers=headers, json={"name": "Published run"})).json()
    graph = _graph()
    graph["nodes"].append({
        "id": "mcp", "type": "mcp_tool",
        "params": {
            "connection_id": connection_id, "tool_name": "write_record",
            "arguments": {"value": "approved-channel-payload"},
        },
    })
    graph["edges"].append({
        "id": "edge", "source": "trigger", "source_output": "main",
        "target": "mcp", "target_input": "input",
    })
    updated = await client.put(f"/workflows/{workflow['id']}", headers=headers, json={"graph": graph})
    assert updated.status_code == 200
    published = await client.post(f"/workflows/{workflow['id']}/publish", headers=headers, json={})
    assert published.status_code == 200
    version_id = published.json()["workflow_version_id"]
    catalog = await client.get(f"/mcp-gateway/{connection_id}/catalog", headers=headers)
    assert catalog.status_code == 200
    approved = await client.put(f"/mcp-gateway/{connection_id}/policy", headers=headers, json={
        "catalog_digest": catalog.json()["catalog_digest"],
        "tools": {"write_record": {}}, "workflow_version_ids": [version_id],
    })
    assert approved.status_code == 200
    started = await client.post(f"/workflows/{workflow['id']}/run?use_draft=false", headers=headers, json={})
    assert started.status_code == 202
    run_id = started.json()["run_id"]
    async with runner.SessionLocal() as session:
        run = await session.get(Run, run_id)
        version = await session.get(WorkflowVersion, version_id)
        assert run.status == "success", run.error
        assert run.workflow_version_id == version_id
        assert run.execution_graph_digest == execution_graph_digest(version.graph)
        assert run.initiator_kind == "user"
        actor_id = run.initiator_id
    assert peer.calls == 1
    events = await client.get(f"/mcp-gateway/{connection_id}/invocations", headers=headers)
    assert events.status_code == 200
    invocation = events.json()[0]
    assert invocation["run_id"] == run_id
    assert invocation["workflow_version_id"] == version_id
    assert invocation["actor_id"] == actor_id
    assert invocation["outcome"] == "provider_reported_success"

    # A worker from the old attempt must not regain effects after a recovery
    # dispatcher reclaims this same durable run. Keep the approved actor/graph
    # valid so lease fencing is the condition that blocks the second call.
    async with runner.SessionLocal() as session:
        run = await session.get(Run, run_id)
        run.status = "running"
        entry = await session.scalar(select(RunQueueEntry).where(RunQueueEntry.run_id == run_id))
        assert entry is not None
        entry.status = "running"
        entry.lease_token = "replacement-worker-lease"
        entry.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
        await session.commit()
    token = current_execution_attempt.set(ExecutionAttempt(run_id, "stale-worker-lease"))
    try:
        async with runner.SessionLocal() as session:
            with pytest.raises(mcp_gateway.GatewayDenied, match="execution_attempt_lease_changed"):
                await mcp_gateway.execute_for_run(
                    session, connection_id, "write_record", {"value": "late-effect"}, run_id=run_id,
                )
    finally:
        current_execution_attempt.reset(token)
    assert peer.calls == 1
    events = (await client.get(f"/mcp-gateway/{connection_id}/invocations", headers=headers)).json()
    denied = next(event for event in events if event["decision"] == "denied")
    assert denied["reason"] == "execution_attempt_lease_changed"
    assert denied["outcome"] == "not_dispatched"


@pytest.mark.parametrize("failure,expected", [
    ("none", None),
    ("missing_context", "execution_attempt_missing"),
    ("wrong_root", "execution_attempt_mismatch"),
    ("new_lease", "execution_attempt_lease_changed"),
    ("expired_lease", "execution_attempt_lease_expired"),
    ("cancelled_run", "execution_attempt_run_not_running"),
    ("completed_queue", "execution_attempt_not_running"),
])
async def test_execution_attempt_fences_root_and_child_against_fresh_queue_state(
    client, failure, expected,
):
    workflow = (await client.post("/workflows", json={"name": "Lease identity"})).json()
    async with runner.SessionLocal() as session:
        parent = Run(workflow_id=workflow["id"], org_id="default", status="running")
        session.add(parent)
        await session.flush()
        child = Run(
            workflow_id=workflow["id"], org_id="default", parent_run_id=parent.id,
            status="running",
        )
        entry = RunQueueEntry(
            run_id=parent.id, workflow_id=workflow["id"], org_id="default",
            status="running", lease_token="current-lease",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        session.add_all([child, entry])
        await session.commit()
        parent_id, child_id, entry_id = parent.id, child.id, entry.id
        attempt = None if failure == "missing_context" else ExecutionAttempt(
            run_id="unrelated-root" if failure == "wrong_root" else parent_id,
            lease_token="current-lease",
        )
        token = current_execution_attempt.set(attempt)
        try:
            # Change the durable lease from another session while the original
            # objects remain cached. Validation must not trust that stale cache.
            async with runner.SessionLocal() as other_session:
                other_entry = await other_session.get(RunQueueEntry, entry_id)
                if failure == "new_lease":
                    other_entry.lease_token = "replacement-lease"
                elif failure == "expired_lease":
                    other_entry.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
                elif failure == "completed_queue":
                    other_entry.status = "completed"
                elif failure == "cancelled_run":
                    other_parent = await other_session.get(Run, parent_id)
                    other_parent.status = "cancelled"
                await other_session.commit()
            assert await validate_execution_attempt(session, run_id=parent_id) == expected
            assert await validate_execution_attempt(session, run_id=child_id) == expected
        finally:
            current_execution_attempt.reset(token)


async def test_direct_execution_without_queue_ledger_remains_valid(client):
    workflow = (await client.post("/workflows", json={"name": "Direct execution"})).json()
    async with runner.SessionLocal() as session:
        run = Run(workflow_id=workflow["id"], org_id="default", status="running")
        session.add(run)
        await session.commit()
        token = current_execution_attempt.set(None)
        try:
            assert await validate_execution_attempt(session, run_id=run.id) is None
        finally:
            current_execution_attempt.reset(token)


async def test_executor_binds_attempt_and_resets_it_when_execution_raises(monkeypatch):
    observed = []

    async def failing_executor(*args, **kwargs):
        observed.append(current_execution_attempt.get())
        # Callback tasks inherit the immutable host attempt without receiving it
        # from an untrusted worker event or taking a new HTTP request identity.
        async def callback():
            return current_execution_attempt.get()
        observed.append(await asyncio.create_task(callback()))
        raise RuntimeError("execution failed")

    monkeypatch.setattr(runner, "_resolve_run_org", AsyncMock(return_value="default"))
    monkeypatch.setattr(runner, "_execute_run_impl", failing_executor)
    monkeypatch.setattr(runner.tracing, "enabled", lambda: False)
    outer = ExecutionAttempt(run_id="outer", lease_token="outer-lease")
    token = current_execution_attempt.set(outer)
    try:
        with pytest.raises(RuntimeError, match="execution failed"):
            await runner._execute_run("host-run", "workflow", _graph(), None)
        assert len(observed) == 2
        assert observed[0] == observed[1]
        assert observed[0].run_id == "host-run"
        assert observed[0].lease_token != outer.lease_token
        assert current_execution_attempt.get() == outer
    finally:
        current_execution_attempt.reset(token)


@pytest.mark.parametrize("dispatch_path", ["direct", "remote_dispatcher"])
@pytest.mark.parametrize("parent_run_id", [None, "forged-cross-tenant-parent"])
async def test_remote_subworkflow_cannot_mint_unfenced_gateway_identity(
    monkeypatch, dispatch_path, parent_run_id,
):
    from app.services.providers import agent
    from app.services.remote_dispatch import RemoteDispatcher

    monkeypatch.setattr(settings, "mcp_gateway_enabled", True)
    resolver = AsyncMock(side_effect=AssertionError("Remote request reached child creation"))
    monkeypatch.setattr(subworkflows, "resolve_subworkflow", resolver)
    replied = asyncio.Event()
    replies = []

    async def send(message):
        replies.append(message)
        replied.set()

    connection = SimpleNamespace(runner_id="untrusted-runner", send=send, active_runs={})
    message = {
        "type": "call_workflow", "callback_id": "callback", "workflow_id": "approved-system-workflow",
        "parent_run_id": parent_run_id, "org_id": "other-tenant", "use_published": True,
    }
    if dispatch_path == "direct":
        await agent.resolve_remote_subworkflow(connection, message)
    else:
        # Agent, managed Docker agents, and Kubernetes jobs all share this
        # WebSocket dispatcher before reaching the same child callback.
        await RemoteDispatcher()._handle_agent_message(connection, message)
        await asyncio.wait_for(replied.wait(), timeout=1)
    resolver.assert_not_awaited()
    assert replies[0]["type"] == "call_workflow_error"
    assert replies[0]["callback_id"] == "callback"
    assert "not supported while the MCP gateway is enabled" in replies[0]["error"]
