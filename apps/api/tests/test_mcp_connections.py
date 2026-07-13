"""Integration tests for MCP Connections CRUD, sync, and tool execution."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.models import MCPConnection
from app.services.mcp_client import (
    MCPError,
    _build_auth_headers,
    _load_conn_with_secret,
    _unwrap_mcp_result,
    call_tool,
    discover_tools,
    encrypt_auth_secret,
    mcp_tool_to_node_manifest,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MOCK_TOOLS_RESPONSE = {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {
        "tools": [
            {
                "name": "echo",
                "description": "Echo input back",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "The message to echo",
                        }
                    },
                    "required": ["message"],
                },
            },
            {
                "name": "add",
                "description": "Add two numbers",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "a": {"type": "number"},
                        "b": {"type": "number"},
                    },
                    "required": ["a", "b"],
                },
            },
        ]
    },
}

_MOCK_CALL_RESPONSE = {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {
        "content": [{"type": "text", "text": "hello world"}],
    },
}

_MOCK_CALL_ERROR = {
    "jsonrpc": "2.0",
    "id": 1,
    "error": {"code": -32000, "message": "Tool execution failed"},
}


# ---------------------------------------------------------------------------
# Unit tests for service functions
# ---------------------------------------------------------------------------


class TestBuildAuthHeaders:
    def test_bearer_auth(self):
        conn = AsyncMock(spec=MCPConnection)
        conn.headers = {}
        conn.auth_type = "bearer"
        headers = _build_auth_headers(conn, "secret-token")
        assert headers["Authorization"] == "Bearer secret-token"

    def test_header_auth(self):
        conn = AsyncMock(spec=MCPConnection)
        conn.headers = {}
        conn.auth_type = "header"
        headers = _build_auth_headers(conn, "X-API-Key:my-key")
        assert headers["X-API-Key"] == "my-key"

    def test_none_auth(self):
        conn = AsyncMock(spec=MCPConnection)
        conn.headers = {}
        conn.auth_type = "none"
        headers = _build_auth_headers(conn, None)
        assert headers == {}

    def test_custom_headers_preserved(self):
        conn = AsyncMock(spec=MCPConnection)
        conn.headers = {"X-Custom": "value"}
        conn.auth_type = "none"
        headers = _build_auth_headers(conn, None)
        assert headers["X-Custom"] == "value"


class TestUnwrapMCPResult:
    def test_single_text(self):
        result = {"content": [{"type": "text", "text": "hello"}]}
        assert _unwrap_mcp_result(result) == "hello"

    def test_multiple_items(self):
        result = {
            "content": [
                {"type": "text", "text": "a"},
                {"type": "text", "text": "b"},
            ]
        }
        assert _unwrap_mcp_result(result) == [
            {"type": "text", "text": "a"},
            {"type": "text", "text": "b"},
        ]

    def test_no_content(self):
        result = {"foo": "bar"}
        assert _unwrap_mcp_result(result) == result

    def test_non_text_content(self):
        result = {"content": [{"type": "image", "data": "abc"}]}
        assert _unwrap_mcp_result(result) == [{"type": "image", "data": "abc"}]


class TestMCPToolToNodeManifest:
    def test_basic_conversion(self):
        tool = {
            "name": "echo",
            "description": "Echo input",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "The message"},
                    "count": {"type": "integer"},
                },
                "required": ["message"],
            },
        }
        manifest = mcp_tool_to_node_manifest(tool, "conn-1")
        assert manifest["id"] == "mcp:conn-1:echo"
        assert manifest["name"] == "echo"
        assert manifest["category"] == "MCP"
        assert manifest["mcp_connection_id"] == "conn-1"
        assert manifest["mcp_tool_name"] == "echo"
        assert manifest["input_kinds"] == {"message": "string", "count": "number"}
        assert len(manifest["params"]) == 2
        message_param = next(p for p in manifest["params"] if p["name"] == "message")
        assert message_param["required"] is True
        count_param = next(p for p in manifest["params"] if p["name"] == "count")
        assert count_param["required"] is False

    def test_title_used_when_present(self):
        tool = {"name": "echo", "inputSchema": {"type": "object", "properties": {}}}
        tool["title"] = "Echo Tool"
        manifest = mcp_tool_to_node_manifest(tool, "conn-1")
        assert manifest["name"] == "Echo Tool"


class TestDiscoverTools:
    async def test_success(self, httpx_mock):
        httpx_mock.add_response(
            url="https://mcp.example.com/",
            method="POST",
            json=_MOCK_TOOLS_RESPONSE,
        )
        conn = AsyncMock(spec=MCPConnection)
        conn.url = "https://mcp.example.com/"
        conn.headers = {}
        conn.auth_type = "none"

        tools = await discover_tools(conn, decrypted_secret=None)
        assert len(tools) == 2
        assert tools[0]["name"] == "echo"
        assert tools[1]["name"] == "add"

    async def test_jsonrpc_error(self, httpx_mock):
        httpx_mock.add_response(
            url="https://mcp.example.com/",
            method="POST",
            json={"jsonrpc": "2.0", "id": 1, "error": {"message": "bad request"}},
        )
        conn = AsyncMock(spec=MCPConnection)
        conn.url = "https://mcp.example.com/"
        conn.headers = {}
        conn.auth_type = "none"

        with pytest.raises(MCPError, match="bad request"):
            await discover_tools(conn, decrypted_secret=None)

    async def test_ssrf_blocked(self, httpx_mock):
        conn = AsyncMock(spec=MCPConnection)
        conn.url = "http://localhost:3000/"
        conn.headers = {}
        conn.auth_type = "none"

        with patch(
            "nodyra_nodes.http_security.private_egress_allowed",
            return_value=False,
        ):
            with pytest.raises(Exception, match="private|blocked"):
                await discover_tools(conn, decrypted_secret=None)

    async def test_private_ip_blocked(self, httpx_mock):
        conn = AsyncMock(spec=MCPConnection)
        conn.url = "http://192.168.1.1:3000/"
        conn.headers = {}
        conn.auth_type = "none"

        with patch(
            "nodyra_nodes.http_security.private_egress_allowed",
            return_value=False,
        ):
            with pytest.raises(Exception, match="private|blocked"):
                await discover_tools(conn, decrypted_secret=None)


class TestCallTool:
    async def test_success(self, httpx_mock):
        httpx_mock.add_response(
            url="https://mcp.example.com/",
            method="POST",
            json=_MOCK_CALL_RESPONSE,
        )
        conn = AsyncMock(spec=MCPConnection)
        conn.url = "https://mcp.example.com/"
        conn.headers = {}
        conn.auth_type = "none"

        result = await call_tool(
            conn, "echo", {"message": "hello"}, decrypted_secret=None
        )
        assert result == "hello world"

    async def test_jsonrpc_error(self, httpx_mock):
        httpx_mock.add_response(
            url="https://mcp.example.com/",
            method="POST",
            json=_MOCK_CALL_ERROR,
        )
        conn = AsyncMock(spec=MCPConnection)
        conn.url = "https://mcp.example.com/"
        conn.headers = {}
        conn.auth_type = "none"

        with pytest.raises(MCPError, match="Tool execution failed"):
            await call_tool(
                conn, "bad_tool", {}, decrypted_secret=None
            )

    async def test_http_error(self, httpx_mock):
        httpx_mock.add_response(
            url="https://mcp.example.com/",
            method="POST",
            status_code=500,
        )
        conn = AsyncMock(spec=MCPConnection)
        conn.url = "https://mcp.example.com/"
        conn.headers = {}
        conn.auth_type = "none"

        with pytest.raises(httpx.HTTPStatusError):
            await call_tool(
                conn, "echo", {}, decrypted_secret=None
            )


class TestEncryptDecrypt:
    async def test_roundtrip(self):
        """auth_secret encrypt/decrypt roundtrip with a real KEK."""
        # Use the master KEK path (org KEK derived from SECRET_KEY)
        # This requires a SECRET_KEY in the test environment.
        from app.services.crypto import generate_org_kek

        kek = generate_org_kek()
        raw = "my-secret-value"
        encrypted = encrypt_auth_secret(raw, kek)
        assert encrypted != raw
        assert isinstance(encrypted, str)

        from cryptography.fernet import Fernet

        decrypted = Fernet(kek).decrypt(encrypted.encode()).decode()
        assert decrypted == raw


class TestLoadConnWithSecret:
    async def test_connection_not_found(self, client):  # noqa: ARG002
        from app.main import SessionLocal

        async with SessionLocal() as session:
            with pytest.raises(ValueError, match="not found"):
                await _load_conn_with_secret("nonexistent", "org-1", session)


# ---------------------------------------------------------------------------
# Integration tests via API (require DB + auth context)
# ---------------------------------------------------------------------------


async def test_list_connections_allows_auth_disabled_local_workspace(client):
    response = await client.get("/mcp-connections")

    assert response.status_code == 200
    assert response.json() == []


async def test_list_connections_requires_identity_when_auth_enabled(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth_required", True)

    response = await client.get("/mcp-connections")

    assert response.status_code == 401


async def test_list_connections_rejects_viewer_when_auth_enabled(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auth_required", True)
    owner = (
        await client.post(
            "/auth/register",
            json={
                "name": "Owner",
                "company": "Nodyra",
                "email": "mcp-rbac-owner@nodyra.test",
                "password": "supersecret",
            },
        )
    ).json()
    owner_headers = {"Authorization": f"Bearer {owner['token']}"}
    created = await client.post(
        "/auth/users",
        headers=owner_headers,
        json={
            "name": "Viewer",
            "email": "mcp-rbac-viewer@nodyra.test",
            "password": "supersecret",
            "role": "viewer",
        },
    )
    assert created.status_code == 201
    viewer = (
        await client.post(
            "/auth/login",
            json={
                "email": "mcp-rbac-viewer@nodyra.test",
                "password": "supersecret",
            },
        )
    ).json()

    response = await client.get(
        "/mcp-connections",
        headers={"Authorization": f"Bearer {viewer['token']}"},
    )

    assert response.status_code == 403


async def test_list_connections_uses_default_org_in_single_tenant(client):
    registered = (
        await client.post(
            "/auth/register",
            json={
                "name": "Owner",
                "company": "Nodyra",
                "email": "mcp-owner@nodyra.test",
                "password": "supersecret",
            },
        )
    ).json()

    response = await client.get(
        "/mcp-connections",
        headers={"Authorization": f"Bearer {registered['token']}"},
    )

    assert response.status_code == 200
    assert response.json() == []


async def test_mcp_tool_audit_records_success_and_error_calls(client, httpx_mock):
    registered = (
        await client.post(
            "/auth/register",
            json={
                "name": "Owner",
                "company": "Nodyra",
                "email": "mcp-audit-owner@nodyra.test",
                "password": "supersecret",
            },
        )
    ).json()
    headers = {"Authorization": f"Bearer {registered['token']}"}
    created = await client.post(
        "/mcp-connections",
        headers=headers,
        json={
            "name": "Audited MCP",
            "url": "https://mcp.example.com/mcp",
            "transport": "streamable-http",
        },
    )
    assert created.status_code == 201
    conn_id = created.json()["id"]

    httpx_mock.add_response(
        url="https://mcp.example.com/mcp",
        method="POST",
        json=_MOCK_CALL_RESPONSE,
    )
    httpx_mock.add_response(
        url="https://mcp.example.com/mcp",
        method="POST",
        json=_MOCK_CALL_ERROR,
    )

    from app.main import SessionLocal

    async with SessionLocal() as session:
        conn = await session.get(MCPConnection, conn_id)
        assert conn is not None
        assert (
            await call_tool(
                conn,
                "echo",
                {"message": "hello"},
                decrypted_secret=None,
                audit_session=session,
                run_id="run_success",
            )
        ) == "hello world"
        with pytest.raises(MCPError, match="Tool execution failed"):
            await call_tool(
                conn,
                "explode",
                {},
                decrypted_secret=None,
                audit_session=session,
                run_id="run_error",
            )

    response = await client.get(
        f"/mcp-connections/{conn_id}/calls",
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    by_tool = {item["tool"]: item for item in body["items"]}
    assert by_tool["echo"]["ok"] is True
    assert by_tool["echo"]["connection_id"] == conn_id
    assert by_tool["echo"]["run_id"] == "run_success"
    assert isinstance(by_tool["echo"]["duration_ms"], int)
    assert by_tool["explode"]["ok"] is False
    assert "Tool execution failed" in by_tool["explode"]["error"]


@pytest.mark.skip(reason="Requires full DB + auth setup; run manually")
class TestMCPConnectionsAPI:
    """Full integration tests that require auth setup."""

    BASE_URL = "http://test/mcp-connections"

    async def test_create_and_list(self, client):
        resp = await client.post(
            self.BASE_URL,
            json={
                "name": "My MCP",
                "url": "https://mcp.example.com/",
                "transport": "streamable-http",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "My MCP"
        assert data["auth_secret"] is None

        # List
        resp = await client.get(self.BASE_URL)
        assert resp.status_code == 200
        ids = [c["id"] for c in resp.json()]
        assert data["id"] in ids

    async def test_create_with_sse_rejected(self, client):
        resp = await client.post(
            self.BASE_URL,
            json={
                "name": "Bad Transport",
                "url": "https://mcp.example.com/",
                "transport": "sse",
            },
        )
        assert resp.status_code == 400
        assert "SSE transport is not yet supported" in resp.text

    async def test_create_with_bearer_auth(self, client):
        resp = await client.post(
            self.BASE_URL,
            json={
                "name": "Authed MCP",
                "url": "https://mcp.example.com/",
                "auth_type": "bearer",
                "auth_secret": "my-token",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["auth_secret"] == "***redacted***"

    async def test_sync_discovers_tools(self, client, httpx_mock):
        # Create connection
        resp = await client.post(
            self.BASE_URL,
            json={
                "name": "Sync Test",
                "url": "https://mcp.example.com/",
            },
        )
        conn_id = resp.json()["id"]

        # Mock MCP server
        httpx_mock.add_response(
            url="https://mcp.example.com/",
            method="POST",
            json=_MOCK_TOOLS_RESPONSE,
        )

        # Sync
        resp = await client.post(f"{self.BASE_URL}/{conn_id}/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tools_discovered"] == 2

        # Tools endpoint
        resp = await client.get(f"{self.BASE_URL}/{conn_id}/tools")
        assert resp.status_code == 200
        tools = resp.json()
        assert len(tools) == 2

    async def test_list_tools_returns_manifests(self, client):
        # Create connection with cached tools
        resp = await client.post(
            self.BASE_URL,
            json={
                "name": "Tool Test",
                "url": "https://mcp.example.com/",
            },
        )
        conn_id = resp.json()["id"]

        # Directly set tool_cache on the connection
        from app.db import SessionLocal

        async with SessionLocal() as session:
            conn = await session.get(MCPConnection, conn_id)
            conn.tool_cache = _MOCK_TOOLS_RESPONSE["result"]["tools"]
            conn.last_synced_at = __import__(
                "datetime"
            ).datetime.now(__import__("datetime").UTC)
            await session.commit()

        resp = await client.get(f"{self.BASE_URL}/{conn_id}/tools")
        assert resp.status_code == 200
        tools = resp.json()
        assert len(tools) == 2
        assert tools[0]["id"] == f"mcp:{conn_id}:echo"
        assert tools[0]["category"] == "MCP"
