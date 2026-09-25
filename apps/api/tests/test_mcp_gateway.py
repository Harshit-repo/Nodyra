"""The gateway must deny before effects and never invent external success."""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import settings
from app.main import app
from app.models import MCPConnection, Run, User, Workflow, WorkflowVersion
from app.security import optional_current_user
from app.services import mcp_gateway as gateway
from app.services import rate_limit, runner
from tests.mcp_approval_helpers import identities
from tests.test_mcp_transport_hardening import Server


@pytest.fixture
async def setup(client, monkeypatch, httpx_mock):
    monkeypatch.setattr(settings, "mcp_gateway_enabled", True)
    monkeypatch.setattr(settings, "queue_backend", "none")
    owner = User(
        id="gateway-owner", email="owner@gateway.test", password_hash="unused", role="owner"
    )
    app.dependency_overrides[optional_current_user] = lambda: owner
    peer = Server(httpx_mock)
    created = await client.post(
        "/mcp-connections",
        json={
            "name": "Gateway test",
            "url": "https://mcp.example.com/mcp",
            "allowed_tools": ["write_record"],
        },
    )
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    async with runner.SessionLocal() as session:
        session.add(owner)
        await session.commit()
    yield client, cid, peer
    app.dependency_overrides.pop(optional_current_user, None)


async def approve(client, cid, *, constraints=None, versions=None):
    catalog = await client.get(f"/mcp-gateway/{cid}/catalog")
    assert catalog.status_code == 200, catalog.text
    response = await client.put(
        f"/mcp-gateway/{cid}/policy",
        json={
            "catalog_digest": catalog.json()["catalog_digest"],
            "tools": {"write_record": constraints or {}},
            "workflow_version_ids": versions or [],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def call(client, cid, args=None, name="write_record"):
    response = await client.post(
        f"/mcp-gateway/{cid}",
        json={
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": {"value": "secret-business-value"} if args is None else args,
            },
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


async def events(client, cid):
    response = await client.get(f"/mcp-gateway/{cid}/invocations")
    assert response.status_code == 200
    return response.json()


async def test_gateway_disabled_and_auth_required(client, monkeypatch):
    monkeypatch.setattr(settings, "mcp_gateway_enabled", False)
    assert (await client.post("/mcp-gateway/missing", json={})).status_code == 404
    monkeypatch.setattr(settings, "mcp_gateway_enabled", True)
    assert (await client.post("/mcp-gateway/missing", json={})).status_code == 401


async def test_discovery_never_approves_and_denial_is_durable(setup):
    client, cid, peer = setup
    assert (await client.get(f"/mcp-gateway/{cid}/catalog")).status_code == 200
    result = await call(client, cid)
    assert result["isError"]
    assert peer.calls == 0
    row = (await events(client, cid))[0]
    assert row["decision"] == "denied" and row["outcome"] == "not_dispatched"
    assert row["actor_id"] == "gateway-owner"
    assert result["_meta"]["io.nodyra/correlationId"] == row["id"]


async def test_approved_call_preserves_receipt_without_logging_arguments_or_results(setup):
    client, cid, peer = setup
    policy = await approve(client, cid)
    peer.result = {
        "content": [{"type": "text", "text": "private-provider-receipt"}],
        "structuredContent": {"record_id": "r-1"},
    }
    result = await call(client, cid)
    assert result["structuredContent"] == {"record_id": "r-1"}
    assert peer.calls == 1
    rows = await events(client, cid)
    assert rows[0]["outcome"] == "provider_reported_success"
    assert rows[0]["policy_revision"] == policy["revision"]
    assert "secret-business-value" not in str(rows)
    assert "private-provider-receipt" not in str(rows)
    assert rows[0]["result_digest"]


@pytest.mark.parametrize(
    "failure", ["required_field", "capabilities", "removed_tool", "permissions"]
)
async def test_live_contract_drift_prevents_dispatch(setup, failure):
    client, cid, peer = setup
    await approve(client, cid)
    if failure == "required_field":
        peer.tools[0]["inputSchema"]["required"].append("account")
    elif failure == "capabilities":
        peer.capabilities["resources"] = {"subscribe": True}
    elif failure == "permissions":
        peer.tools[0]["_meta"] = {"permissions": ["write:anywhere"]}
    else:
        peer.tools = []
    assert (await call(client, cid))["isError"]
    assert peer.calls == 0
    row = (await events(client, cid))[0]
    assert row["decision"] == "denied" and row["outcome"] == "not_dispatched"


async def test_description_changes_do_not_break_approved_contract(setup):
    client, cid, peer = setup
    await approve(client, cid)
    peer.tools[0]["description"] = "Better documentation"
    peer.tools[0]["inputSchema"]["properties"]["value"]["description"] = "Updated help"
    result = await call(client, cid)
    assert not result.get("isError")
    assert peer.calls == 1


@pytest.mark.parametrize("args", [{}, {"value": 2}, {"value": "not-approved"}])
async def test_schema_and_operator_constraints_block_before_network(setup, args):
    client, cid, peer = setup
    await approve(client, cid, constraints={"properties": {"value": {"const": "approved"}}})
    requests = len(peer.messages)
    assert (await call(client, cid, args))["isError"]
    assert peer.calls == 0 and len(peer.messages) == requests


@pytest.mark.parametrize(
    "change",
    [
        {"enabled": False},
        {"allowed_tools": []},
        {"url": "https://other.example.com/mcp"},
        {"headers": {"X-Account": "new"}},
    ],
)
async def test_connection_changes_revoke_effective_authorization(setup, change):
    client, cid, peer = setup
    await approve(client, cid)
    assert (await client.patch(f"/mcp-connections/{cid}", json=change)).status_code == 200
    assert (await call(client, cid))["isError"]
    assert peer.calls == 0


async def test_stale_catalog_cannot_be_silently_approved(setup):
    client, cid, peer = setup
    catalog = (await client.get(f"/mcp-gateway/{cid}/catalog")).json()
    peer.tools[0]["inputSchema"]["required"].append("new_permission")
    response = await client.put(
        f"/mcp-gateway/{cid}/policy",
        json={"catalog_digest": catalog["catalog_digest"], "tools": {"write_record": {}}},
    )
    assert response.status_code == 409
    assert (await call(client, cid))["isError"]
    assert peer.calls == 0


async def test_tool_error_is_distinct_from_uncertain_transport_failure(setup):
    client, cid, peer = setup
    await approve(client, cid)
    peer.result = {
        "isError": True,
        "content": [{"type": "text", "text": "private provider failure"}],
    }
    assert (await call(client, cid))["isError"]
    assert (await events(client, cid))[0]["outcome"] == "tool_error"

    def timeout(message):
        if message["method"] == "tools/call":
            peer.calls += 1
            raise httpx.ReadTimeout("private timeout text")

    peer.override = timeout
    result = await call(client, cid)
    assert result["isError"]
    # SQLite's server timestamp has second precision: two calls may tie.
    # Correlate the evidence by ID rather than assuming UUID sort order.
    observed = {row["id"]: row for row in await events(client, cid)}
    assert observed[result["_meta"]["io.nodyra/correlationId"]]["outcome"] == "outcome_unknown"
    assert peer.calls == 2  # Exactly one attempt per request, no write retry.
    assert "private" not in str(await events(client, cid))


async def test_audit_storage_failure_prevents_any_tool_effect(setup, monkeypatch):
    client, cid, peer = setup
    await approve(client, cid)
    async with runner.SessionLocal() as session:
        monkeypatch.setattr(
            session, "commit", AsyncMock(side_effect=RuntimeError("DB unavailable"))
        )
        with pytest.raises(RuntimeError, match="DB unavailable"):
            await gateway.execute(
                session,
                cid,
                "write_record",
                {"value": "x"},
                principal=gateway.Principal("default", "gateway-owner"),
            )
    assert peer.calls == 0


async def test_shared_rate_counter_outage_fails_closed(setup, monkeypatch):
    client, cid, peer = setup
    await approve(client, cid)
    monkeypatch.setattr(settings, "queue_backend", "redis")
    monkeypatch.setattr(rate_limit, "_allow_redis", AsyncMock(return_value=None))
    async with runner.SessionLocal() as session:
        with pytest.raises(gateway.GatewayDenied, match="rate_limit_or_counter_unavailable"):
            await gateway.execute(
                session,
                cid,
                "write_record",
                {"value": "x"},
                principal=gateway.Principal("default", "gateway-owner"),
            )
    assert peer.calls == 0


async def test_published_worker_context_is_bound_to_approved_actual_graph(setup):
    client, cid, peer = setup
    graph = {"nodes": [], "edges": []}
    async with runner.SessionLocal() as session:
        workflow = Workflow(
            id="gateway-workflow", org_id="default", name="Gateway workflow", draft_graph=graph
        )
        session.add(workflow)
        await session.flush()
        version = WorkflowVersion(
            id="gateway-version", org_id="default", workflow_id=workflow.id, version=1, graph=graph
        )
        session.add(version)
        await session.flush()
        session.add(
            Run(
                id="gateway-run",
                status="running",
                org_id="default",
                workflow_id=workflow.id,
                workflow_version_id=version.id,
                workflow_version=1,
                initiator_id="gateway-owner",
                initiator_kind="user",
                execution_graph_digest=gateway.digest(graph),
            )
        )
        await session.commit()
    await approve(client, cid, versions=["gateway-version"])
    async with runner.SessionLocal() as session:
        assert (
            await gateway.execute_for_run(
                session, cid, "write_record", {"value": "x"}, run_id="gateway-run"
            )
            == "saved"
        )
        run = await session.get(Run, "gateway-run")
        run.execution_graph_digest = gateway.digest({"nodes": ["tampered"]})
        await session.commit()
        with pytest.raises(gateway.GatewayDenied, match="workflow_version_not_approved"):
            await gateway.execute_for_run(
                session, cid, "write_record", {"value": "x"}, run_id="gateway-run"
            )
    assert peer.calls == 1
    rows = await events(client, cid)
    assert all(
        row["actor_id"] == "gateway-owner" and row["workflow_version_id"] == "gateway-version"
        for row in rows
    )


async def test_cross_org_connection_cannot_execute_even_with_known_id(setup):
    client, cid, peer = setup
    await approve(client, cid)
    async with runner.SessionLocal() as session:
        connection = await session.get(MCPConnection, cid)
        # Another real organization, not a malformed/nonexistent actor context.
        from app.models import Organization

        session.add(Organization(id="other-org", name="Other", slug="other-org"))
        await session.commit()
        with pytest.raises(gateway.GatewayDenied, match="connection_unavailable"):
            await gateway.execute(
                session,
                connection.id,
                "write_record",
                {"value": "x"},
                principal=gateway.Principal("other-org", "gateway-owner"),
            )
    assert peer.calls == 0


async def test_remote_schema_refs_are_not_fetched(setup):
    client, cid, peer = setup
    peer.tools[0]["inputSchema"] = {
        "type": "object",
        "properties": {"value": {"$ref": "https://metadata.internal/secret"}},
    }
    catalog = (await client.get(f"/mcp-gateway/{cid}/catalog")).json()
    response = await client.put(
        f"/mcp-gateway/{cid}/policy",
        json={"catalog_digest": catalog["catalog_digest"], "tools": {"write_record": {}}},
    )
    assert response.status_code == 409
    assert peer.calls == 0


async def test_discovery_rpc_error_is_recorded_as_no_dispatch(setup):
    client, cid, peer = setup
    await approve(client, cid)
    peer.override = lambda message: httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": message["id"],
            "error": {"code": -32603, "message": "private failure"},
        },
    )
    assert (await call(client, cid))["isError"]
    assert peer.calls == 0
    row = (await events(client, cid))[0]
    assert row["decision"] == "denied" and row["outcome"] == "not_dispatched"


async def test_unavailable_secret_records_denial(setup, monkeypatch):
    client, cid, peer = setup
    await approve(client, cid)
    monkeypatch.setattr(
        gateway.mcp_client,
        "_load_conn_with_secret",
        AsyncMock(side_effect=ValueError("private decryption detail")),
    )
    result = await call(client, cid)
    assert result["_meta"]["io.nodyra/reason"] == "connection_secret_unavailable"
    assert "private" not in str(result)
    assert peer.calls == 0
    assert (await events(client, cid))[0]["outcome"] == "not_dispatched"


async def test_result_storage_failure_retains_uncertain_intent(setup, monkeypatch):
    client, cid, peer = setup
    await approve(client, cid)
    async with runner.SessionLocal() as session:
        original = session.commit
        commits = 0

        async def commit():
            nonlocal commits
            commits += 1
            if commits == 2:
                raise RuntimeError("result storage unavailable")
            await original()

        monkeypatch.setattr(session, "commit", commit)
        with pytest.raises(RuntimeError, match="result storage unavailable"):
            await gateway.execute(
                session,
                cid,
                "write_record",
                {"value": "x"},
                principal=gateway.Principal("default", "gateway-owner"),
            )
    assert peer.calls == 1
    assert (await events(client, cid))[0]["outcome"] == "dispatch_pending"


async def test_revocation_after_admission_prevents_dispatch(setup, monkeypatch):
    client, cid, peer = setup
    await approve(client, cid)
    original = gateway.mcp_client.call_tool

    async def revoke_then_call(*args, **kwargs):
        async with runner.SessionLocal() as session:
            connection = await session.get(MCPConnection, cid)
            connection.gateway_policy = None
            await session.commit()
        return await original(*args, **kwargs)

    monkeypatch.setattr(gateway.mcp_client, "call_tool", revoke_then_call)
    result = await call(client, cid)
    assert result["_meta"]["io.nodyra/reason"] == "policy_revoked_before_dispatch"
    assert peer.calls == 0
    assert (await events(client, cid))[0]["outcome"] == "not_dispatched"


@pytest.mark.parametrize(
    "role,scopes,status",
    [
        ("editor", ["mcp_gateway:call"], 200),
        ("viewer", ["mcp_gateway:call"], 403),
        ("owner", ["workflow:read"], 403),
    ],
)
async def test_real_pat_scope_and_role_are_both_required(setup, role, scopes, status):
    from sqlalchemy import select

    from app.models import ApiToken

    client, cid, peer = setup
    await approve(client, cid)
    app.dependency_overrides.pop(optional_current_user)
    pat, _, user_id = await identities(client, role=role)
    async with runner.SessionLocal() as session:
        token = await session.scalar(select(ApiToken).where(ApiToken.user_id == user_id))
        token.scopes = scopes
        await session.commit()
    headers = {"Authorization": f"Bearer {pat}"}
    result = await client.post(
        f"/mcp-gateway/{cid}",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert result.status_code == status, result.text
    assert (await client.get(f"/mcp-gateway/{cid}/policy", headers=headers)).status_code == 403
    assert peer.calls == 0


@pytest.mark.parametrize("status", ["cancelled", "success", "error"])
async def test_finished_run_cannot_dispatch(setup, status):
    client, cid, peer = setup
    await approve(client, cid)
    async with runner.SessionLocal() as session:
        workflow = Workflow(name="Stopped workflow", draft_graph={"nodes": [], "edges": []})
        session.add(workflow)
        await session.flush()
        run = Run(
            workflow_id=workflow.id,
            status=status,
            initiator_id="gateway-owner",
            initiator_kind="user",
        )
        session.add(run)
        await session.commit()
        with pytest.raises(gateway.GatewayDenied, match="run_not_active"):
            await gateway.execute_for_run(
                session, cid, "write_record", {"value": "x"}, run_id=run.id
            )
    assert peer.calls == 0
    assert (await events(client, cid))[0]["outcome"] == "not_dispatched"


async def test_reviewed_draft7_dependencies_are_enforced_before_dispatch(setup):
    client, cid, peer = setup
    await approve(client, cid, constraints={
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "dependencies": {"value": ["approved_destination"]},
        "properties": {"approved_destination": {"const": "reviewed-table"}},
    })
    denied = await call(client, cid, {"value": "x"})
    assert denied["isError"]
    assert peer.calls == 0
    assert (await events(client, cid))[0]["reason"] == "arguments_do_not_match_approved_contract"
    allowed = await call(client, cid, {"value": "x", "approved_destination": "reviewed-table"})
    assert not allowed.get("isError")
    assert peer.calls == 1


@pytest.mark.parametrize("schema", [
    {"$schema": "https://unknown-dialect.invalid/schema", "type": "object"},
    {"type": "object", "required": "not-an-array"},
    {"type": "object", "properties": {"value": {"$schema": "https://unknown-dialect.invalid/schema"}}},
])
async def test_unknown_or_invalid_schema_cannot_be_approved(setup, schema):
    client, cid, peer = setup
    catalog = await client.get(f"/mcp-gateway/{cid}/catalog")
    response = await client.put(f"/mcp-gateway/{cid}/policy", json={
        "catalog_digest": catalog.json()["catalog_digest"],
        "tools": {"write_record": schema},
    })
    assert response.status_code == 409, response.text
    assert (await client.get(f"/mcp-gateway/{cid}/policy")).json()["policy"] is None
    assert peer.calls == 0


def test_schema_registry_keeps_id_and_fragment_references_local(monkeypatch):
    import urllib.request

    from jsonschema import ValidationError

    retrieved = []

    def forbidden(url, *args, **kwargs):
        retrieved.append(url)
        raise AssertionError("Schema validation must never fetch a URL")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    schema = {
        "$id": "https://schema.invalid/root",
        "type": "object",
        "$defs": {"value": {"type": "string", "const": "approved"}},
        "properties": {"value": {"$ref": "#/$defs/value"}},
    }
    validator = gateway._schema_validator(schema)
    validator.validate({"value": "approved"})
    with pytest.raises(ValidationError):
        validator.validate({"value": "denied"})
    assert retrieved == []


def test_nested_id_unresolved_fragment_cannot_trigger_network_retrieval(monkeypatch):
    import urllib.request

    from referencing.exceptions import Unresolvable

    retrieved = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, *args, **kwargs: retrieved.append(url))
    validator = gateway._schema_validator({"type": "object", "properties": {
        "value": {"$id": "https://schema.invalid/child", "$ref": "#missing"},
    }})
    with pytest.raises(Unresolvable):
        validator.validate({"value": "x"})
    assert retrieved == []


def test_reference_shaped_constant_values_are_data_not_schema_references():
    value = {"$ref": "https://customer-document.invalid/reference", "$schema": "customer-data"}
    gateway._schema_validator({"const": value}).validate(value)


async def test_malformed_provider_metadata_returns_an_explicit_unknown_outcome(setup):
    client, cid, peer = setup
    await approve(client, cid)
    peer.result = {"content": [{"type": "text", "text": "saved"}], "_meta": "invalid"}
    result = await call(client, cid)
    assert result["isError"]
    assert peer.calls == 1
    row = (await events(client, cid))[0]
    assert row["outcome"] == "outcome_unknown"
    assert result["_meta"]["io.nodyra/correlationId"] == row["id"]


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity"])
async def test_nonfinite_policy_json_is_rejected_without_server_error(setup, number):
    client, cid, peer = setup
    catalog = await client.get(f"/mcp-gateway/{cid}/catalog")
    reviewed_digest = catalog.json()["catalog_digest"]
    response = await client.put(
        f"/mcp-gateway/{cid}/policy",
        headers={"Content-Type": "application/json"},
        content=(
            '{"catalog_digest":"' + reviewed_digest
            + '","tools":{"write_record":{"properties":{"value":{"const":'
            + number + '}}}}}'
        ),
    )
    assert response.status_code == 422, response.text
    assert "finite JSON" in response.text
    assert (await client.get(f"/mcp-gateway/{cid}/policy")).json()["policy"] is None
    assert peer.calls == 0
