"""MCP Connections CRUD + sync + tools endpoints."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import MCPConnection
from app.security import get_current_user, require_permission
from app.services.mcp_client import (
    _load_conn_with_secret,
    discover_tools,
    encrypt_auth_secret,
    mcp_tool_to_node_manifest,
)
from app.services.org_keys import get_org_kek
from app.tenancy import active_org_id

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp-connections", tags=["mcp-connections"])


def _org() -> str:
    oid = active_org_id()
    if oid is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "No organization context"
        )
    return oid


@router.get("")
async def list_mcp_connections(
    session: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_user),
    _: None = Depends(require_permission("mcp_connection:manage")),
) -> list[dict]:
    org_id = _org()
    rows = await session.scalars(
        select(MCPConnection)
        .where(MCPConnection.org_id == org_id)
        .order_by(MCPConnection.created_at)
    )
    return [_row_to_dict(r) for r in rows.all()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_mcp_connection(
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_user),
    _: None = Depends(require_permission("mcp_connection:manage")),
) -> dict:
    org_id = _org()
    transport = str(
        body.get("transport", "streamable-http") or "streamable-http"
    )
    if transport == "sse":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "SSE transport is not yet supported; use streamable-http",
        )
    auth_type = str(body.get("auth_type", "none") or "none")
    raw_secret = body.get("auth_secret")
    encrypted_secret: str | None = None
    if raw_secret and auth_type != "none":
        org_kek = await get_org_kek(org_id, session)
        if org_kek is None:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Org KEK not available",
            )
        encrypted_secret = encrypt_auth_secret(str(raw_secret), org_kek)
    conn = MCPConnection(
        org_id=org_id,
        name=str(body.get("name", "")),
        url=str(body.get("url", "")).rstrip("/"),
        transport=transport,
        auth_type=auth_type,
        auth_secret=encrypted_secret,
        headers=body.get("headers") or {},
    )
    session.add(conn)
    await session.commit()
    await session.refresh(conn)
    return _row_to_dict(conn)


@router.get("/{connection_id}")
async def get_mcp_connection(
    connection_id: str,
    session: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_user),
    _: None = Depends(require_permission("mcp_connection:manage")),
) -> dict:
    org_id = _org()
    try:
        conn, _ = await _load_conn_with_secret(connection_id, org_id, session)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return _row_to_dict(conn)


@router.patch("/{connection_id}")
async def update_mcp_connection(
    connection_id: str,
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_user),
    _: None = Depends(require_permission("mcp_connection:manage")),
) -> dict:
    org_id = _org()
    try:
        conn, _ = await _load_conn_with_secret(connection_id, org_id, session)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    if "name" in body:
        conn.name = str(body["name"])
    if "url" in body:
        conn.url = str(body["url"]).rstrip("/")
    if "transport" in body:
        t = str(body["transport"])
        if t == "sse":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "SSE transport is not yet supported; use streamable-http",
            )
        conn.transport = t
    if "auth_type" in body:
        conn.auth_type = str(body["auth_type"])
    if "auth_secret" in body and body["auth_secret"] is not None:
        org_kek = await get_org_kek(org_id, session)
        if org_kek is None:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Org KEK not available",
            )
        conn.auth_secret = encrypt_auth_secret(
            str(body["auth_secret"]), org_kek
        )
    if "headers" in body:
        conn.headers = body["headers"] or {}

    await session.commit()
    await session.refresh(conn)
    return _row_to_dict(conn)


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mcp_connection(
    connection_id: str,
    session: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_user),
    _: None = Depends(require_permission("mcp_connection:manage")),
) -> None:
    org_id = _org()
    conn = await session.scalar(
        select(MCPConnection).where(
            MCPConnection.id == connection_id,
            MCPConnection.org_id == org_id,
        )
    )
    if conn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    await session.delete(conn)
    await session.commit()


@router.post("/{connection_id}/sync")
async def sync_mcp_connection(
    connection_id: str,
    session: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_user),
    _: None = Depends(require_permission("mcp_connection:manage")),
) -> dict:
    org_id = _org()
    try:
        conn, secret = await _load_conn_with_secret(
            connection_id, org_id, session
        )
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    tools = await discover_tools(conn, decrypted_secret=secret)
    if len(tools) > 500:
        tools = tools[:500]
    conn.tool_cache = tools
    conn.last_synced_at = datetime.now(UTC)
    await session.commit()
    return {"tools_discovered": len(tools), "tools": tools}


@router.get("/{connection_id}/tools")
async def list_mcp_tools(
    connection_id: str,
    session: AsyncSession = Depends(get_session),
    current_user=Depends(get_current_user),
    _: None = Depends(require_permission("mcp_connection:manage")),
) -> list[dict]:
    org_id = _org()
    conn = await session.scalar(
        select(MCPConnection).where(
            MCPConnection.id == connection_id,
            MCPConnection.org_id == org_id,
        )
    )
    if conn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    cache = conn.tool_cache or []
    return [mcp_tool_to_node_manifest(t, conn_id=conn.id) for t in cache]


def _row_to_dict(conn: MCPConnection) -> dict:
    return {
        "id": conn.id,
        "org_id": conn.org_id,
        "name": conn.name,
        "url": conn.url,
        "transport": conn.transport,
        "auth_type": conn.auth_type,
        "auth_secret": "***redacted***" if conn.auth_secret else None,
        "headers": conn.headers or {},
        "tool_cache": conn.tool_cache,
        "last_synced_at": (
            conn.last_synced_at.isoformat() if conn.last_synced_at else None
        ),
        "created_at": conn.created_at.isoformat() if conn.created_at else None,
        "updated_at": conn.updated_at.isoformat() if conn.updated_at else None,
    }
