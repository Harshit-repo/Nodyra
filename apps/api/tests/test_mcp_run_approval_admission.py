"""Reviewed MCP runs hand authority to the transaction that admits execution."""

import asyncio
import copy
import json

import pytest
from sqlalchemy import select

from app.config import settings
from app.mcp import tools as mcp_tools
from app.models import MCPCommandApproval, Run, Workflow
from app.services import mcp_approvals
from tests.mcp_approval_helpers import mcp_post


def _command(name: str, arguments: dict) -> dict:
    return {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def _graph(*, fail: bool = False) -> dict:
    return {
        "nodes": [
            {"id": "trigger", "type": "manual_trigger", "params": {}},
            {"id": "code", "type": "code", "params": {
                "code": "raise ValueError('retry fixture')" if fail else "output = {'reviewed': True}",
            }},
        ],
        "edges": [{
            "id": "edge", "source": "trigger", "source_output": "main",
            "target": "code", "target_input": "input",
        }],
    }


async def _workflow(client, monkeypatch, *, allow_concurrent: bool, fail: bool = False):
    monkeypatch.setattr(settings, "queue_backend", "none")
    # The per-test fixture may use PostgreSQL independently of the developer's
    # application DATABASE_URL. Exercise its actual single-flight row-lock path.
    async with mcp_tools.SessionLocal() as session:
        monkeypatch.setattr(settings, "database_url", str(session.bind.url))
    created = await client.post("/workflows", json={"name": "Reviewed run admission"})
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]
    updated = await client.put(
        f"/workflows/{workflow_id}", json={"graph": _graph(fail=fail)},
    )
    assert updated.status_code == 200, updated.text
    configured = await client.patch(
        f"/workflows/{workflow_id}", json={"allow_concurrent": allow_concurrent},
    )
    assert configured.status_code == 200, configured.text
    return workflow_id


@pytest.mark.parametrize("allow_concurrent", [True, False])
@pytest.mark.parametrize("tool_name", ["run_workflow", "retry_run"])
async def test_reviewed_run_commands_admit_without_cross_session_deadlock(
    client, monkeypatch, allow_concurrent, tool_name,
):
    retry = tool_name == "retry_run"
    workflow_id = await _workflow(
        client, monkeypatch, allow_concurrent=allow_concurrent, fail=retry,
    )
    source_run_id = None
    if retry:
        started = await client.post(f"/workflows/{workflow_id}/run", json={})
        assert started.status_code == 202, started.text
        source_run_id = started.json()["run_id"]
        source = await client.get(f"/runs/{source_run_id}")
        assert source.json()["status"] == "error", source.text
        arguments = {"run_id": source_run_id}
    else:
        arguments = {"workflow_id": workflow_id, "use_draft": True, "wait_seconds": 1}

    response = await asyncio.wait_for(
        mcp_post(client, json=_command(tool_name, arguments)), timeout=15,
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["isError"] is False, response.text
    outcome = json.loads(response.json()["result"]["content"][0]["text"])
    run_id = outcome["new_run_id" if retry else "run_id"]
    assert run_id != source_run_id
    async with mcp_tools.SessionLocal() as session:
        run = await session.get(Run, run_id)
        assert run.workflow_id == workflow_id
        assert run.status == ("error" if retry else "success")
        assert run.initiator_kind == "user"
        approvals = (await session.scalars(select(MCPCommandApproval))).all()
        assert len(approvals) == 1
        assert approvals[0].status == "consumed"
        assert approvals[0].tool_name == tool_name


@pytest.mark.parametrize("allow_concurrent", [True, False])
async def test_reviewed_run_rechecks_snapshot_inside_admission_transaction(
    client, monkeypatch, allow_concurrent,
):
    workflow_id = await _workflow(client, monkeypatch, allow_concurrent=allow_concurrent)
    original_start_run = mcp_tools.start_run
    changed = False

    async def change_after_handler_read(*args, **kwargs):
        nonlocal changed
        async with mcp_tools.SessionLocal() as session:
            workflow = await session.get(Workflow, workflow_id)
            modified = copy.deepcopy(workflow.draft_graph)
            modified["nodes"][1]["params"]["code"] = "output = {'reviewed': False}"
            workflow.draft_graph = modified
            workflow.graph_revision += 1
            await session.commit()
        changed = True
        return await original_start_run(*args, **kwargs)

    monkeypatch.setattr(mcp_tools, "start_run", change_after_handler_read)
    response = await asyncio.wait_for(
        mcp_post(client, json=_command("run_workflow", {
            "workflow_id": workflow_id, "use_draft": True, "wait_seconds": 0,
        })), timeout=15,
    )
    assert changed
    assert response.json()["result"]["isError"] is True, response.text
    assert "changed" in response.text.lower()
    async with mcp_tools.SessionLocal() as session:
        assert await session.scalar(select(Run.id).where(Run.workflow_id == workflow_id)) is None


async def test_admission_guard_cannot_authorize_two_concurrent_admissions(client, monkeypatch):
    workflow_id = await _workflow(client, monkeypatch, allow_concurrent=True)
    arguments = {"workflow_id": workflow_id}
    async with mcp_tools.SessionLocal() as session:
        snapshot = await mcp_approvals.target_snapshot(session, arguments)
    guard = mcp_approvals.run_admission_guard(
        MCPCommandApproval(target_snapshot=snapshot), arguments,
    )
    async with mcp_tools.SessionLocal() as first, mcp_tools.SessionLocal() as second:
        results = await asyncio.wait_for(
            asyncio.gather(
                guard(first, workflow_id), guard(second, workflow_id), return_exceptions=True,
            ), timeout=5,
        )
    assert results[0] is None
    assert isinstance(results[1], mcp_approvals.ApprovalError)
    assert "already been used" in str(results[1])
