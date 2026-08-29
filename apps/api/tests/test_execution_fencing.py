"""Attempt-fencing and idempotent outcome persistence regression tests."""

from collections import deque
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.models import NodeRun, Run, RunEvent, RunQueueEntry
from app.services import run_checkpoints, run_persistence, runner


@pytest.mark.asyncio
async def test_stale_attempt_cannot_overwrite_checkpoint(client) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Checkpoint fencing"})
    ).json()["id"]
    run_id = "fenced-checkpoint-run"
    current_token = "d" * 32

    async with runner.SessionLocal() as session:
        session.add(
            Run(
                id=run_id,
                workflow_id=workflow_id,
                status="running",
                started_at=datetime.now(UTC),
            )
        )
        session.add(
            RunQueueEntry(
                run_id=run_id,
                workflow_id=workflow_id,
                status="running",
                leased_by="worker-current",
                lease_token=current_token,
                attempts=2,
            )
        )
        await session.commit()

    await run_checkpoints._save_checkpoint(
        run_id,
        {"stale": {"main": "wrong"}},
        {"stale"},
        "stale",
        lease_token="c" * 32,
    )
    async with runner.SessionLocal() as session:
        run = await session.get(Run, run_id)
        assert run is not None and run.checkpoint is None

    await run_checkpoints._save_checkpoint(
        run_id,
        {"current": {"main": "right"}},
        {"current"},
        "current",
        lease_token=current_token,
    )
    async with runner.SessionLocal() as session:
        run = await session.get(Run, run_id)
        assert run is not None
        assert run.checkpoint["last_node_id"] == "current"


@pytest.mark.asyncio
async def test_stale_attempt_cannot_persist_or_duplicate_outcome(client) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Attempt fencing"})
    ).json()["id"]
    run_id = "fenced-persistence-run"
    current_token = "b" * 32
    stale_token = "a" * 32

    async with runner.SessionLocal() as session:
        session.add(
            Run(
                id=run_id,
                workflow_id=workflow_id,
                status="running",
                started_at=datetime.now(UTC),
            )
        )
        session.add(
            RunQueueEntry(
                run_id=run_id,
                workflow_id=workflow_id,
                status="running",
                leased_by="worker-b",
                lease_token=current_token,
                attempts=2,
            )
        )
        await session.commit()

    event = {
        "type": "node_finished",
        "node_id": "n1",
        "status": "success",
        "outputs": {"main": 1},
        "iteration_path": [],
    }
    events = deque(
        [
            {
                "sequence": 1,
                "ts": datetime.now(UTC),
                "event": {"type": "agent_tool_finished", "node_id": "n1"},
            }
        ]
    )

    accepted = await run_persistence.persist_run_outcome(
        runner.SessionLocal,
        run_id=run_id,
        status="success",
        graph_dict={"nodes": [], "edges": []},
        node_events={"n1": event},
        node_run_records={("n1", ()): event},
        run_events=events,
        output_cap=256 * 1024,
        lease_token=stale_token,
    )
    assert accepted is False

    accepted = await run_persistence.persist_run_outcome(
        runner.SessionLocal,
        run_id=run_id,
        status="success",
        graph_dict={"nodes": [], "edges": []},
        node_events={"n1": event},
        node_run_records={("n1", ()): event},
        run_events=events,
        output_cap=256 * 1024,
        lease_token=current_token,
    )
    assert accepted is True

    # Retrying after the successful commit is fenced because completion clears
    # the queue token. It therefore cannot duplicate detail rows or metering.
    accepted = await run_persistence.persist_run_outcome(
        runner.SessionLocal,
        run_id=run_id,
        status="success",
        graph_dict={"nodes": [], "edges": []},
        node_events={"n1": event},
        node_run_records={("n1", ()): event},
        run_events=events,
        output_cap=256 * 1024,
        lease_token=current_token,
    )
    assert accepted is False

    async with runner.SessionLocal() as session:
        run = await session.get(Run, run_id)
        entry = await session.scalar(
            select(RunQueueEntry).where(RunQueueEntry.run_id == run_id)
        )
        node_count = await session.scalar(
            select(func.count()).select_from(NodeRun).where(NodeRun.run_id == run_id)
        )
        event_count = await session.scalar(
            select(func.count()).select_from(RunEvent).where(RunEvent.run_id == run_id)
        )

    assert run is not None and run.status == "success"
    assert entry is not None and entry.status == "completed"
    assert entry.lease_token is None
    assert node_count == 1
    assert event_count == 1
