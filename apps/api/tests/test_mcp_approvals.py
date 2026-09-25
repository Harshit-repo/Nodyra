import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update

from app.config import settings
from app.mcp import tools as mcp_tools
from app.models import (
    ApiToken,
    AuditEvent,
    CustomRole,
    MCPCommandApproval,
    MCPGatewayInvocation,
    Membership,
    Run,
    RunApproval,
    User,
    Workflow,
)
from app.services import mcp_approvals, retention
from app.services.crypto import create_token
from tests.mcp_approval_helpers import identities, review


def command(name="create_workflow", arguments=None):
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {"name": "Reviewed workflow"}},
    }


def payload(response):
    return json.loads(response.json()["result"]["content"][0]["text"])


async def request_approval(client, name="create_workflow", arguments=None):
    pat, browser, user_id = await identities(client)
    body = command(name, arguments)
    response = await client.post("/mcp", json=body, headers={"Authorization": f"Bearer {pat}"})
    assert response.status_code == 200, response.text
    approval = payload(response)
    assert approval["error"] == "human_approval_required", approval
    return approval, body, pat, browser, user_id


async def test_approval_decision_executes_exactly_once(client: AsyncClient):
    approval, body, pat, browser, _ = await request_approval(client)
    async with mcp_tools.SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Workflow)) == 0
    decision = await review(client, approval["approval_id"], browser)
    assert decision.status_code == 200, decision.text
    assert decision.headers["Cache-Control"] == "no-store"
    body["params"]["arguments"]["approval_id"] = approval["approval_id"]
    response = await client.post("/mcp", json=body, headers={"Authorization": f"Bearer {pat}"})
    assert response.json()["result"]["isError"] is False, response.text
    replay = await client.post("/mcp", json=body, headers={"Authorization": f"Bearer {pat}"})
    assert replay.json()["result"]["isError"] is True
    assert "already been used" in replay.text
    async with mcp_tools.SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Workflow)) == 1


@pytest.mark.parametrize("decision", [None, "deny"])
async def test_unapproved_or_denied_never_executes(client, decision):
    approval, body, pat, browser, _ = await request_approval(client)
    if decision:
        assert (
            await review(client, approval["approval_id"], browser, decision=decision)
        ).status_code == 200
    body["params"]["arguments"].update(approval_id=approval["approval_id"], approved_by_user=True)
    response = await client.post("/mcp", json=body, headers={"Authorization": f"Bearer {pat}"})
    assert response.json()["result"]["isError"] is True
    async with mcp_tools.SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Workflow)) == 0


async def test_legacy_boolean_is_not_approval(client):
    approval, *_ = await request_approval(
        client, arguments={"name": "No self approval", "approved_by_user": True}
    )
    assert approval["approval_id"]
    async with mcp_tools.SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Workflow)) == 0


async def test_automation_token_cannot_review_even_as_cookie(client):
    approval, _, pat, _, _ = await request_approval(client)
    path = f"/mcp-approvals/{approval['approval_id']}/decision"
    response = await client.post(
        path, json={"decision": "approve"}, headers={"Authorization": f"Bearer {pat}"}
    )
    assert response.status_code == 401
    response = await review(client, approval["approval_id"], pat)
    assert response.status_code == 401


async def test_requesting_session_cannot_self_approve_by_changing_transport(client):
    _, browser, _ = await identities(client)
    requested = await client.post(
        "/mcp", json=command(), headers={"Authorization": f"Bearer {browser}"}
    )
    approval = payload(requested)
    response = await review(client, approval["approval_id"], browser)
    assert response.status_code == 403
    assert "cannot approve its own" in response.text


async def test_cookie_review_requires_csrf(client):
    approval, _, _, browser, _ = await request_approval(client)
    client.cookies.set(settings.session_cookie_name, browser)
    response = await client.post(
        f"/mcp-approvals/{approval['approval_id']}/decision", json={"decision": "approve"}
    )
    assert response.status_code == 403
    assert "CSRF" in response.text


async def test_other_actor_cannot_review(client):
    approval, _, _, _, _ = await request_approval(client)
    async with mcp_tools.SessionLocal() as session:
        actor = User(
            email="other@mcp-test.invalid", password_hash="test", role="owner", email_verified=True
        )
        session.add(actor)
        await session.commit()
    response = await review(client, approval["approval_id"], create_token(actor.id))
    assert response.status_code == 404


async def test_role_loss_blocks_review(client):
    approval, _, _, browser, user_id = await request_approval(client)
    async with mcp_tools.SessionLocal() as session:
        await session.execute(update(User).where(User.id == user_id).values(role="viewer"))
        await session.commit()
    response = await review(client, approval["approval_id"], browser)
    assert response.status_code == 403


async def test_argument_tamper_or_other_token_cannot_consume(client):
    approval, body, pat, browser, user_id = await request_approval(client)
    assert (await review(client, approval["approval_id"], browser)).status_code == 200
    body["params"]["arguments"].update(approval_id=approval["approval_id"], name="Changed action")
    response = await client.post("/mcp", json=body, headers={"Authorization": f"Bearer {pat}"})
    assert "does not match" in response.text
    body["params"]["arguments"]["name"] = "Reviewed workflow"
    other_pat = "ndpat_different-test-token"
    async with mcp_tools.SessionLocal() as session:
        session.add(
            ApiToken(
                user_id=user_id,
                org_id="default",
                name="Other",
                token_hash=hashlib.sha256(other_pat.encode()).hexdigest(),
                token_prefix="ndpat_different",
                scopes=["*"],
            )
        )
        await session.commit()
    response = await client.post(
        "/mcp", json=body, headers={"Authorization": f"Bearer {other_pat}"}
    )
    assert "does not match" in response.text


async def test_workflow_change_invalidates_review(client):
    workflow_id = (await client.post("/workflows", json={"name": "Draft"})).json()["id"]
    approval, body, pat, browser, _ = await request_approval(
        client, "rename_workflow", {"workflow_id": workflow_id, "name": "After"}
    )
    assert (await review(client, approval["approval_id"], browser)).status_code == 200
    await client.put(f"/workflows/{workflow_id}", json={"name": "Changed elsewhere"})
    body["params"]["arguments"]["approval_id"] = approval["approval_id"]
    response = await client.post("/mcp", json=body, headers={"Authorization": f"Bearer {pat}"})
    assert "changed after" in response.text


async def test_expired_approval_cannot_be_reviewed_or_used(client):
    approval, body, pat, browser, _ = await request_approval(client)
    async with mcp_tools.SessionLocal() as session:
        await session.execute(
            update(MCPCommandApproval)
            .where(MCPCommandApproval.id == approval["approval_id"])
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
    assert (await review(client, approval["approval_id"], browser)).status_code == 409
    body["params"]["arguments"]["approval_id"] = approval["approval_id"]
    response = await client.post("/mcp", json=body, headers={"Authorization": f"Bearer {pat}"})
    assert "expired" in response.text


async def test_review_preview_redacts_secrets(client):
    approval, *_ = await request_approval(
        client, arguments={"name": "Secret-safe", "password": "not-for-audit-log"}
    )
    async with mcp_tools.SessionLocal() as session:
        row = await session.get(MCPCommandApproval, approval["approval_id"])
        assert "not-for-audit-log" not in json.dumps(row.arguments_preview)
        assert row.arguments_preview["password"] == "***REDACTED***"


async def test_invalid_schema_or_permission_denied_creates_no_review(client):
    pat, _, _ = await identities(client, role="viewer")
    response = await client.post("/mcp", json=command(), headers={"Authorization": f"Bearer {pat}"})
    assert response.json()["result"]["isError"] is True
    async with mcp_tools.SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(MCPCommandApproval)) == 0


@pytest.mark.parametrize("granted,builtin", [(False, "owner"), (True, "viewer")])
async def test_custom_role_replaces_builtin_mcp_permission(client, monkeypatch, granted, builtin):
    pat, _, user_id = await identities(client, role=builtin)
    async with mcp_tools.SessionLocal() as session:
        role = CustomRole(
            org_id="default",
            name="Custom operator",
            permissions=["workflow:write"] if granted else ["workflow:read"],
        )
        session.add(role)
        await session.flush()
        session.add(
            Membership(org_id="default", user_id=user_id, role=builtin, custom_role_id=role.id)
        )
        await session.commit()
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    response = await client.post("/mcp", json=command(), headers={"Authorization": f"Bearer {pat}"})
    assert response.status_code == 200, response.text
    if granted:
        assert payload(response)["error"] == "human_approval_required"
    else:
        assert "Custom role" in response.text
        assert "does not grant" in response.text
    async with mcp_tools.SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(MCPCommandApproval)) == int(
            granted
        )


async def test_no_auth_development_does_not_bypass_review(client):
    response = await client.post(
        "/mcp", json=command(arguments={"name": "Untrusted", "approved_by_user": True})
    )
    assert response.json()["result"]["isError"] is True
    assert "signed-in" in response.text


async def test_concurrent_retries_consume_one_grant(client):
    approval, body, pat, browser, _ = await request_approval(client)
    assert (await review(client, approval["approval_id"], browser)).status_code == 200
    body["params"]["arguments"]["approval_id"] = approval["approval_id"]
    results = await asyncio.gather(
        *(
            client.post("/mcp", json=body, headers={"Authorization": f"Bearer {pat}"})
            for _ in range(2)
        )
    )
    assert sorted(result.json()["result"]["isError"] for result in results) == [False, True]
    async with mcp_tools.SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Workflow)) == 1


async def test_retries_reuse_pending_request_and_pending_requests_are_bounded(client, monkeypatch):
    monkeypatch.setattr(mcp_approvals, "MAX_PENDING_APPROVALS", 2)
    first, _, pat, _, _ = await request_approval(client)
    same, *_ = await request_approval(client)
    assert first["approval_id"] == same["approval_id"]
    await request_approval(client, arguments={"name": "Second action"})
    response = await client.post(
        "/mcp",
        json=command(arguments={"name": "Third action"}),
        headers={"Authorization": f"Bearer {pat}"},
    )
    assert "Too many pending" in response.text
    async with mcp_tools.SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(MCPCommandApproval)) == 2


async def test_approval_records_follow_audit_retention(client, monkeypatch):
    old, *_ = await request_approval(client)
    recent, *_ = await request_approval(client, arguments={"name": "Retain recent"})
    async with mcp_tools.SessionLocal() as session:
        for row_id, days in (("old-invocation", 91), ("recent-invocation", 1)):
            session.add(
                MCPGatewayInvocation(
                    id=row_id,
                    connection_id="retained-connection",
                    actor_kind="user",
                    tool_name="test_tool",
                    arguments_digest="a" * 64,
                    reason="allowed",
                    created_at=datetime.now(UTC) - timedelta(days=days),
                )
            )
        await session.execute(
            update(MCPCommandApproval)
            .where(MCPCommandApproval.id == old["approval_id"])
            .values(created_at=datetime.now(UTC) - timedelta(days=91))
        )
        await session.commit()
    monkeypatch.setattr(settings, "audit_log_retention_days", 0)
    assert await retention.prune_audit_logs() == 0
    async with mcp_tools.SessionLocal() as session:
        assert await session.get(MCPCommandApproval, old["approval_id"]) is not None
        assert await session.get(MCPGatewayInvocation, "old-invocation") is not None
    monkeypatch.setattr(settings, "audit_log_retention_days", 90)
    await retention.prune_audit_logs()
    async with mcp_tools.SessionLocal() as session:
        assert await session.get(MCPCommandApproval, old["approval_id"]) is None
        assert await session.get(MCPCommandApproval, recent["approval_id"]) is not None
        assert await session.get(MCPGatewayInvocation, "old-invocation") is None
        assert await session.get(MCPGatewayInvocation, "recent-invocation") is not None


async def test_run_approval_target_is_distinct_from_command_grant(client, monkeypatch):
    workflow_id = (await client.post("/workflows", json={"name": "Paused agent"})).json()["id"]
    async with mcp_tools.SessionLocal() as session:
        run = Run(workflow_id=workflow_id, status="waiting")
        session.add(run)
        await session.flush()
        session.add(RunApproval(
            id="underlying-run-approval", run_id=run.id, approval_key="agent|0|call|tool",
            tool_call_id="call", tool_name="tool", status="pending",
        ))
        await session.commit()
    observed = []

    async def resume(run_id, run_approval_id, *, approve_all):
        observed.append((run_id, run_approval_id, approve_all))

    monkeypatch.setattr("app.routers.runs.resume_waiting_run_from_approval", resume)
    approval, body, pat, browser, actor_id = await request_approval(
        client,
        "resolve_run_approval",
        {
            "run_id": run.id,
            "run_approval_id": "underlying-run-approval",
            "decision": "approve",
        },
    )
    assert observed == []
    assert (await review(client, approval["approval_id"], browser)).status_code == 200
    body["params"]["arguments"]["approval_id"] = approval["approval_id"]
    response = await client.post("/mcp", json=body, headers={"Authorization": f"Bearer {pat}"})
    assert response.json()["result"]["isError"] is False, response.text
    assert observed == [(run.id, "underlying-run-approval", False)]
    assert payload(response)["id"] == "underlying-run-approval"
    assert payload(response)["status"] == "approved"
    async with mcp_tools.SessionLocal() as session:
        audit = await session.scalar(select(AuditEvent).where(
            AuditEvent.action == "approval_decision", AuditEvent.target_id == run.id,
        ))
        assert audit is not None
        assert audit.actor_id == actor_id


def test_approval_digests_are_keyed_and_normalized(monkeypatch):
    first = mcp_approvals.argument_digest({"name": "Example", "value": 5})
    assert first == mcp_approvals.argument_digest(
        {"value": 5, "name": "Example", "approved_by_user": True}
    )
    monkeypatch.setattr(settings, "secret_key", "another-test-key")
    assert first != mcp_approvals.argument_digest({"name": "Example", "value": 5})


async def test_tool_contract_and_release_changes_invalidate_pending_grant(client, monkeypatch):
    import dataclasses

    from app.routers import mcp as router

    approval, body, pat, browser, _ = await request_approval(client)
    assert (await review(client, approval["approval_id"], browser)).status_code == 200
    body["params"]["arguments"]["approval_id"] = approval["approval_id"]
    original_version = mcp_approvals.NODYRA_VERSION
    monkeypatch.setattr(mcp_approvals, "NODYRA_VERSION", "99.0.0-contract-test")
    changed_release = await client.post(
        "/mcp", json=body, headers={"Authorization": f"Bearer {pat}"}
    )
    assert changed_release.json()["result"]["isError"] is True
    assert "changed after" in changed_release.text
    monkeypatch.setattr(mcp_approvals, "NODYRA_VERSION", original_version)
    original_get_tool = router.get_tool
    original = original_get_tool("create_workflow")
    changed = dataclasses.replace(
        original, input_schema={**original.input_schema, "description": "New contract revision"}
    )
    monkeypatch.setattr(
        router,
        "get_tool",
        lambda name: changed if name == "create_workflow" else original_get_tool(name),
    )
    changed_schema = await client.post(
        "/mcp", json=body, headers={"Authorization": f"Bearer {pat}"}
    )
    assert changed_schema.json()["result"]["isError"] is True
    async with mcp_tools.SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(Workflow)) == 0


def test_every_static_mutation_has_review_contract():
    for tool in mcp_tools.STATIC_TOOLS:
        if (
            tool.permission
            and not tool.name.startswith(("get_", "list_"))
            and tool.name != "cancel_run"
        ):
            assert tool.requires_approval, tool.name
            assert "approval_id" in tool.descriptor()["inputSchema"]["properties"]
    assert not mcp_tools.get_tool("cancel_run").requires_approval
