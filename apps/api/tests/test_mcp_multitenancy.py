"""End-to-end MCP tenant isolation and scoped automation-token coverage."""

import json

from httpx import AsyncClient

from app.config import settings


def rpc(method: str, params: dict | None = None, req_id: int = 1) -> dict:
    body = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


async def tool(
    client: AsyncClient, headers: dict[str, str], name: str, arguments: dict
) -> dict:
    response = await client.post(
        "/mcp",
        headers=headers,
        json=rpc("tools/call", {"name": name, "arguments": arguments}),
    )
    assert response.status_code == 200
    return response.json()["result"]


async def create_org(client: AsyncClient, token: str, name: str, slug: str) -> str:
    response = await client.post(
        "/orgs",
        headers={"Authorization": f"Bearer {token}", "X-Org-Id": "default"},
        json={"name": name, "slug": slug},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_mcp_isolates_tools_resources_and_tokens_by_org(
    client: AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "auth_allow_registration", True)

    registered = await client.post(
        "/auth/register",
        json={
            "email": "mcp-owner@example.test",
            "password": "correct horse battery staple",
            "name": "MCP Owner",
        },
    )
    assert registered.status_code == 201, registered.text
    session_token = registered.json()["token"]
    org_a = await create_org(client, session_token, "Org A", "org-a")
    org_b = await create_org(client, session_token, "Org B", "org-b")

    auth = {"Authorization": f"Bearer {session_token}"}
    headers_a = {**auth, "X-Org-Id": org_a}
    headers_b = {**auth, "X-Org-Id": org_b}
    created_a = await tool(client, headers_a, "create_workflow", {"name": "A only"})
    created_b = await tool(client, headers_b, "create_workflow", {"name": "B only"})
    wf_a = json.loads(created_a["content"][0]["text"])["workflow_id"]
    wf_b = json.loads(created_b["content"][0]["text"])["workflow_id"]

    listed_a = await tool(client, headers_a, "list_workflows", {})
    ids_a = {
        item["id"]
        for item in json.loads(listed_a["content"][0]["text"])["workflows"]
    }
    assert wf_a in ids_a
    assert wf_b not in ids_a

    foreign = await tool(client, headers_a, "get_workflow", {"workflow_id": wf_b})
    assert foreign["isError"] is True
    assert "not found" in foreign["content"][0]["text"].lower()

    resources = await client.post(
        "/mcp", headers=headers_a, json=rpc("resources/list")
    )
    uris = {item["uri"] for item in resources.json()["result"]["resources"]}
    assert f"noodle://workflow/{wf_a}" in uris
    assert f"noodle://workflow/{wf_b}" not in uris
    foreign_resource = await client.post(
        "/mcp",
        headers=headers_a,
        json=rpc("resources/read", {"uri": f"noodle://workflow/{wf_b}"}),
    )
    assert foreign_resource.json()["error"]["code"] == -32601

    # Dynamic names are tenant-local: the same name is valid in each org.
    enabled_a = await tool(
        client, headers_a, "enable_mcp_tool", {"workflow_id": wf_a, "tool_name": "shared"}
    )
    enabled_b = await tool(
        client, headers_b, "enable_mcp_tool", {"workflow_id": wf_b, "tool_name": "shared"}
    )
    assert enabled_a["isError"] is False
    assert enabled_b["isError"] is False

    token_response = await client.post(
        "/auth/api-tokens",
        headers=headers_a,
        json={"name": "mcp-runner", "scopes": ["workflow:run"], "expires_in_days": 30},
    )
    assert token_response.status_code == 201, token_response.text
    token_body = token_response.json()
    pat = token_body["token"]
    token_id = token_body["id"]
    pat_headers = {"Authorization": f"Bearer {pat}"}

    # No X-Org-Id is needed: the token is permanently bound to org A.
    pat_list = await tool(client, pat_headers, "list_workflows", {})
    pat_ids = {
        item["id"]
        for item in json.loads(pat_list["content"][0]["text"])["workflows"]
    }
    assert wf_a in pat_ids and wf_b not in pat_ids
    denied_scope = await tool(client, pat_headers, "list_credentials", {})
    assert denied_scope["isError"] is True
    assert "credential:read" in denied_scope["content"][0]["text"]

    wrong_org = await client.post(
        "/mcp",
        headers={**pat_headers, "X-Org-Id": org_b},
        json=rpc("tools/list"),
    )
    assert wrong_org.status_code == 403

    class IntrospectionResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "active": True,
                "sub": "mcp-owner@example.test",
                "org_id": org_a,
                "scope": "workflow:run",
            }

    class IntrospectionClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs) -> IntrospectionResponse:
            return IntrospectionResponse()

    import httpx

    monkeypatch.setattr(settings, "mcp_authorization_server_url", "https://issuer.test")
    monkeypatch.setattr(settings, "mcp_oauth_introspection_url", "https://issuer.test/introspect")
    monkeypatch.setattr(httpx, "AsyncClient", IntrospectionClient)
    oauth_list = await tool(
        client, {"Authorization": "Bearer external-oauth-token"}, "list_workflows", {}
    )
    oauth_ids = {
        item["id"]
        for item in json.loads(oauth_list["content"][0]["text"])["workflows"]
    }
    assert wf_a in oauth_ids and wf_b not in oauth_ids

    token_list = await client.get("/auth/api-tokens", headers=headers_a)
    assert token_list.status_code == 200
    assert "token" not in token_list.json()[0]
    revoked = await client.delete(f"/auth/api-tokens/{token_id}", headers=headers_a)
    assert revoked.status_code == 204
    rejected = await client.post("/mcp", headers=pat_headers, json=rpc("ping"))
    assert rejected.status_code == 401
