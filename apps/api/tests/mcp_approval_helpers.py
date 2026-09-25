"""Real PAT request + browser-session review flow shared by MCP integration tests."""

import copy
import hashlib
import uuid
from json import loads

from httpx import AsyncClient

from app.config import settings
from app.mcp import tools as mcp_tools
from app.models import ApiToken, User
from app.services.crypto import create_token


async def identities(client: AsyncClient, *, role: str = "owner") -> tuple[str, str, str]:
    existing = getattr(client, "mcp_test_identities", None)
    if existing:
        return existing
    user_id = uuid.uuid4().hex
    pat = "ndpat_" + uuid.uuid4().hex
    async with mcp_tools.SessionLocal() as session:
        session.add(
            User(
                id=user_id,
                email=f"{user_id}@mcp-test.invalid",
                role=role,
                password_hash="not-a-login-hash",
                email_verified=True,
            )
        )
        await session.flush()
        session.add(
            ApiToken(
                user_id=user_id,
                org_id="default",
                name="MCP test",
                token_hash=hashlib.sha256(pat.encode()).hexdigest(),
                token_prefix=pat[:14],
                scopes=["*"],
            )
        )
        await session.commit()
    result = (pat, create_token(user_id), user_id)
    client.mcp_test_identities = result
    return result


async def review(
    client: AsyncClient,
    approval_id: str,
    browser_token: str,
    *,
    decision: str = "approve",
    org_id: str | None = None,
):
    # Do not leak cookies into unrelated REST/MCP calls in this fixture.
    previous = client.cookies
    try:
        client.cookies = {}
        client.cookies.set(settings.session_cookie_name, browser_token)
        client.cookies.set(settings.csrf_cookie_name, "mcp-test-csrf")
        headers = {settings.csrf_header_name: "mcp-test-csrf"}
        if org_id:
            headers["X-Org-Id"] = org_id
        return await client.post(
            f"/mcp-approvals/{approval_id}/decision", json={"decision": decision}, headers=headers
        )
    finally:
        client.cookies = previous


async def mcp_post(client: AsyncClient, *, json: dict, approve: bool = True, **kwargs):
    """Run existing handler tests through the production review protocol."""
    body = copy.deepcopy(json)
    params = body.get("params", {}) if isinstance(body, dict) else {}
    tool = mcp_tools.get_tool(params.get("name", "")) if isinstance(params, dict) else None
    if not tool or not tool.requires_approval or body.get("method") != "tools/call":
        return await client.post("/mcp", json=body, **kwargs)
    pat, browser_token, _ = await identities(client)
    kwargs.setdefault("headers", {})["Authorization"] = f"Bearer {pat}"
    result = await client.post("/mcp", json=body, **kwargs)
    if not approve or result.status_code != 200:
        return result
    payload = result.json().get("result", {}).get("structuredContent", {})
    if not payload:
        try:
            payload = loads(result.json()["result"]["content"][0]["text"])
        except (KeyError, ValueError, IndexError):
            return result
    if not isinstance(payload, dict) or payload.get("error") != "human_approval_required":
        return result
    decision = await review(client, payload["approval_id"], browser_token)
    assert decision.status_code == 200, decision.text
    body["params"].setdefault("arguments", {})["approval_id"] = payload["approval_id"]
    return await client.post("/mcp", json=body, **kwargs)
