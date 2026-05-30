from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select

from app.config import settings
from app.models import NodeRun, Run
from app.services import retention  # noqa: I001

GRAPH = {
    "nodes": [
        {
            "id": "t",
            "type": "manual_trigger",
            "params": {},
            "position": {"x": 0, "y": 0},
        },
        {
            "id": "c",
            "type": "code",
            "params": {"code": "output = 1"},
            "position": {"x": 1, "y": 0},
        },
    ],
    "edges": [
        {
            "id": "e1",
            "source": "t",
            "source_output": "main",
            "target": "c",
            "target_input": "input",
        }
    ],
}


async def _workflow_with_runs(client: AsyncClient, n: int) -> str:
    workflow_id = (await client.post("/workflows", json={"name": "R"})).json()["id"]
    await client.put(f"/workflows/{workflow_id}", json={"graph": GRAPH})
    for _ in range(n):
        await client.post(f"/workflows/{workflow_id}/run", json={})
    return workflow_id


async def test_age_based_prune_drops_old_runs(client: AsyncClient) -> None:
    await _workflow_with_runs(client, 3)
    previous = settings.run_retention_days
    settings.run_retention_days = 7
    try:
        # Backdate two of the three runs past the retention window.
        async with retention.SessionLocal() as session:
            runs = list(
                (await session.scalars(select(Run).order_by(Run.started_at))).all()
            )
            assert len(runs) == 3
            old = datetime.now(UTC) - timedelta(days=10)
            runs[0].started_at = old
            runs[1].started_at = old
            await session.commit()

        aged_out, capped_out = await retention.prune_old_runs()
        assert aged_out == 2
        assert capped_out == 0

        async with retention.SessionLocal() as session:
            remaining = (await session.scalars(select(Run))).all()
            assert len(list(remaining)) == 1
            # CASCADE took the NodeRun rows with it.
            node_runs = (await session.scalars(select(NodeRun))).all()
            assert all(nr.run_id == remaining[0].id for nr in node_runs)
    finally:
        settings.run_retention_days = previous


async def test_max_per_workflow_keeps_only_recent(client: AsyncClient) -> None:
    workflow_id = await _workflow_with_runs(client, 5)
    previous_days = settings.run_retention_days
    previous_keep = settings.run_retention_max_per_workflow
    settings.run_retention_days = 0  # only count rule active
    settings.run_retention_max_per_workflow = 2
    try:
        aged_out, capped_out = await retention.prune_old_runs()
        assert aged_out == 0
        assert capped_out == 3  # 5 - 2 kept

        async with retention.SessionLocal() as session:
            remaining = (
                await session.scalars(
                    select(Run).where(Run.workflow_id == workflow_id)
                )
            ).all()
            assert len(list(remaining)) == 2
    finally:
        settings.run_retention_days = previous_days
        settings.run_retention_max_per_workflow = previous_keep


async def test_output_cap_truncates_oversize_payloads(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Big"})).json()["id"]
    graph = {
        "nodes": [
            {
                "id": "t",
                "type": "manual_trigger",
                "params": {},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "fat",
                "type": "code",
                "params": {"code": "output = 'x' * 5000"},
                "position": {"x": 250, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e",
                "source": "t",
                "source_output": "main",
                "target": "fat",
                "target_input": "input",
            }
        ],
    }
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})

    previous = settings.max_output_bytes
    settings.max_output_bytes = 1024
    try:
        run_id = (
            await client.post(f"/workflows/{workflow_id}/run", json={})
        ).json()["run_id"]
        run = (await client.get(f"/runs/{run_id}")).json()
        fat_run = next(nr for nr in run["node_runs"] if nr["node_id"] == "fat")
        out = fat_run["output"]["main"]
        assert isinstance(out, dict)
        assert out.get("_truncated") is True
        assert out["size_bytes"] > 1024
        assert isinstance(out["preview"], str) and len(out["preview"]) <= 1024
    finally:
        settings.max_output_bytes = previous


async def test_prune_handles_empty_db(client: AsyncClient) -> None:
    # Sanity: no rows → no crash, both counts zero. Uses ``client`` so the
    # conftest patches ``retention.SessionLocal`` onto the per-test DB.
    aged, capped = await retention.prune_old_runs()
    assert aged == 0
    assert capped == 0
