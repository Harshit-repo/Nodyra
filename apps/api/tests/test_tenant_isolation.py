"""End-to-end cross-tenant isolation across the public API surface."""

from __future__ import annotations

import json

from httpx import AsyncClient

from app.config import settings


def _rpc(method: str, params: dict | None = None, req_id: int = 1) -> dict:
    body = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


async def _mcp_tool(
    client: AsyncClient,
    headers: dict[str, str],
    name: str,
    arguments: dict,
) -> dict:
    response = await client.post(
        "/mcp",
        headers=headers,
        json=_rpc("tools/call", {"name": name, "arguments": arguments}),
    )
    assert response.status_code == 200, response.text
    return response.json()["result"]


async def _create_org(client: AsyncClient, token: str, name: str, slug: str) -> str:
    response = await client.post(
        "/orgs",
        headers={"Authorization": f"Bearer {token}", "X-Org-Id": "default"},
        json={"name": name, "slug": slug},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _artifact_graph(label: str) -> dict:
    return {
        "nodes": [
            {
                "id": "t",
                "type": "manual_trigger",
                "params": {},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "write",
                "type": "code",
                "params": {
                    "code": (
                        "from nodyra.artifacts import write_text\n"
                        f"output = write_text({label!r}, name={label + '.txt'!r})"
                    )
                },
                "position": {"x": 200, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e0",
                "source": "t",
                "source_output": "main",
                "target": "write",
                "target_input": "input",
            },
        ],
    }


async def _workflow_run_and_artifact(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    name: str,
    label: str,
) -> tuple[str, str, str]:
    created = await client.post("/workflows", headers=headers, json={"name": name})
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]

    updated = await client.put(
        f"/workflows/{workflow_id}",
        headers=headers,
        json={"graph": _artifact_graph(label)},
    )
    assert updated.status_code == 200, updated.text

    run_response = await client.post(
        f"/workflows/{workflow_id}/run",
        headers=headers,
        json={},
    )
    assert run_response.status_code == 202, run_response.text
    run_id = run_response.json()["run_id"]

    run = await client.get(f"/runs/{run_id}", headers=headers)
    assert run.status_code == 200, run.text
    run_body = run.json()
    assert run_body["status"] == "success", run_body
    node_runs = {row["node_id"]: row for row in run_body["node_runs"]}
    artifact_ref = node_runs["write"]["output"]["main"]
    assert artifact_ref["__nodyra_artifact__"] is True
    artifact_id = artifact_ref["artifact_id"]

    return workflow_id, run_id, artifact_id


async def _create_credential(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    name: str,
    token_value: str,
) -> str:
    response = await client.post(
        "/credentials",
        headers=headers,
        json={
            "name": name,
            "type": "generic",
            "scope": "global",
            "data": {"token": token_value},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _ids(items: list[dict]) -> set[str]:
    return {str(item["id"]) for item in items}


async def test_tenant_isolation_across_rest_and_mcp_surfaces(
    client: AsyncClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "auth_allow_registration", True)

    registered = await client.post(
        "/auth/register",
        json={
            "email": "tenant-isolation-owner@example.test",
            "password": "correct horse battery staple",
            "name": "Tenant Isolation Owner",
        },
    )
    assert registered.status_code == 201, registered.text
    session_token = registered.json()["token"]

    org_a = await _create_org(client, session_token, "Tenant A", "tenant-a")
    org_b = await _create_org(client, session_token, "Tenant B", "tenant-b")
    auth = {"Authorization": f"Bearer {session_token}"}
    headers_a = {**auth, "X-Org-Id": org_a}
    headers_b = {**auth, "X-Org-Id": org_b}

    wf_a, run_a, artifact_a = await _workflow_run_and_artifact(
        client,
        headers_a,
        name="Tenant A dataset",
        label="tenant-a",
    )
    wf_b, run_b, artifact_b = await _workflow_run_and_artifact(
        client,
        headers_b,
        name="Tenant B dataset",
        label="tenant-b",
    )
    cred_a = await _create_credential(
        client,
        headers_a,
        name="Tenant A Secret",
        token_value="secret-a",
    )
    cred_b = await _create_credential(
        client,
        headers_b,
        name="Tenant B Secret",
        token_value="secret-b",
    )

    own_workflows = await client.get("/workflows", headers=headers_b)
    assert own_workflows.status_code == 200, own_workflows.text
    own_workflow_ids = _ids(own_workflows.json()["items"])
    assert wf_b in own_workflow_ids
    assert wf_a not in own_workflow_ids

    own_runs = await client.get("/runs", headers=headers_b)
    assert own_runs.status_code == 200, own_runs.text
    own_run_ids = _ids(own_runs.json()["items"])
    assert run_b in own_run_ids
    assert run_a not in own_run_ids

    own_artifacts = await client.get("/artifacts", headers=headers_b)
    assert own_artifacts.status_code == 200, own_artifacts.text
    own_artifact_ids = _ids(own_artifacts.json()["items"])
    assert artifact_b in own_artifact_ids
    assert artifact_a not in own_artifact_ids

    own_credentials = await client.get("/credentials", headers=headers_b)
    assert own_credentials.status_code == 200, own_credentials.text
    own_credential_ids = _ids(own_credentials.json()["items"])
    assert cred_b in own_credential_ids
    assert cred_a not in own_credential_ids

    denied_reads = [
        await client.get(f"/workflows/{wf_a}", headers=headers_b),
        await client.get(f"/runs/{run_a}", headers=headers_b),
        await client.get(f"/runs/{run_a}/artifacts", headers=headers_b),
        await client.get(f"/artifacts/{artifact_a}", headers=headers_b),
        await client.get(f"/artifacts/{artifact_a}/download", headers=headers_b),
        await client.post(
            f"/artifacts/{artifact_a}/query",
            headers=headers_b,
            json={"sql": "SELECT * FROM dataset LIMIT 1"},
        ),
        await client.get(
            "/credentials/resolve",
            headers=headers_b,
            params={"name": "Tenant A Secret"},
        ),
    ]
    assert {response.status_code for response in denied_reads} <= {403, 404}

    own_artifact = await client.get(f"/artifacts/{artifact_b}", headers=headers_b)
    assert own_artifact.status_code == 200, own_artifact.text
    own_credential = await client.get(
        "/credentials/resolve",
        headers=headers_b,
        params={"name": "Tenant B Secret"},
    )
    assert own_credential.status_code == 200, own_credential.text

    enabled_a = await _mcp_tool(
        client,
        headers_a,
        "enable_mcp_tool",
        {"workflow_id": wf_a, "tool_name": "tenant_a_tool"},
    )
    enabled_b = await _mcp_tool(
        client,
        headers_b,
        "enable_mcp_tool",
        {"workflow_id": wf_b, "tool_name": "tenant_b_tool"},
    )
    assert enabled_a["isError"] is False
    assert enabled_b["isError"] is False

    foreign_workflow = await _mcp_tool(
        client,
        headers_b,
        "get_workflow",
        {"workflow_id": wf_a},
    )
    assert foreign_workflow["isError"] is True
    assert "not found" in foreign_workflow["content"][0]["text"].lower()

    b_tools = await client.post("/mcp", headers=headers_b, json=_rpc("tools/list"))
    assert b_tools.status_code == 200, b_tools.text
    tool_names = {tool["name"] for tool in b_tools.json()["result"]["tools"]}
    assert "tenant_b_tool" in tool_names
    assert "tenant_a_tool" not in tool_names

    resources = await client.post(
        "/mcp",
        headers=headers_b,
        json=_rpc("resources/list"),
    )
    assert resources.status_code == 200, resources.text
    uris = {item["uri"] for item in resources.json()["result"]["resources"]}
    assert f"nodyra://workflow/{wf_b}" in uris
    assert f"nodyra://workflow/{wf_a}" not in uris
    foreign_resource = await client.post(
        "/mcp",
        headers=headers_b,
        json=_rpc("resources/read", {"uri": f"nodyra://workflow/{wf_a}"}),
    )
    assert foreign_resource.status_code == 200, foreign_resource.text
    assert foreign_resource.json()["error"]["code"] == -32601

    b_mcp_list = await _mcp_tool(client, headers_b, "list_workflows", {})
    b_mcp_ids = {
        item["id"]
        for item in json.loads(b_mcp_list["content"][0]["text"])["workflows"]
    }
    assert wf_b in b_mcp_ids
    assert wf_a not in b_mcp_ids
