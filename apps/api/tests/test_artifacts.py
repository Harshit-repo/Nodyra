from datetime import UTC, datetime, timedelta
from pathlib import Path

from httpx import AsyncClient
from sqlalchemy import select

from app.config import settings
from app.models import Artifact, Run
from app.services import retention


async def test_code_node_creates_downloadable_artifact_and_downstream_reads_it(
    client: AsyncClient,
) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Artifacts"})).json()[
        "id"
    ]
    graph = {
        "nodes": [
            {
                "id": "writer",
                "type": "code",
                "params": {
                    "code": "output = artifacts.write_text('hello world', name='hello.txt')"
                },
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "reader",
                "type": "code",
                "params": {"code": "output = artifacts.read_text(input).upper()"},
                "position": {"x": 200, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "writer",
                "source_output": "main",
                "target": "reader",
                "target_input": "input",
            }
        ],
    }
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    results = {node["node_id"]: node for node in run["node_runs"]}
    ref = results["writer"]["output"]["main"]

    assert ref["__noodle_artifact__"] is True
    assert ref["name"] == "hello.txt"
    assert ref["size_bytes"] == len("hello world")
    assert results["reader"]["output"]["main"] == "HELLO WORLD"

    artifacts = (await client.get(f"/runs/{run_id}/artifacts")).json()
    assert len(artifacts) == 1
    assert artifacts[0]["id"] == ref["artifact_id"]
    assert artifacts[0]["node_id"] == "writer"

    download = await client.get(f"/artifacts/{ref['artifact_id']}/download")
    assert download.status_code == 200
    assert download.text == "hello world"


async def test_binary_artifact_keeps_bytes_out_of_node_output(
    client: AsyncClient,
) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Binary"})).json()[
        "id"
    ]
    graph = {
        "nodes": [
            {
                "id": "blob",
                "type": "code",
                "params": {
                    "code": "output = artifacts.write_bytes(b'x' * 2048, name='blob.bin')"
                },
                "position": {"x": 0, "y": 0},
            }
        ],
        "edges": [],
    }
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    ref = run["node_runs"][0]["output"]["main"]

    assert ref["size_bytes"] == 2048
    assert "base64" not in ref
    assert "preview" not in ref
    download = await client.get(f"/artifacts/{ref['artifact_id']}/download")
    assert download.content == b"x" * 2048


async def test_artifact_size_limit_fails_cleanly(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Too Big"})).json()[
        "id"
    ]
    graph = {
        "nodes": [
            {
                "id": "blob",
                "type": "code",
                "params": {
                    "code": "output = artifacts.write_bytes(b'x' * 16, name='blob.bin')"
                },
                "position": {"x": 0, "y": 0},
            }
        ],
        "edges": [],
    }
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})

    previous = settings.max_artifact_bytes
    settings.max_artifact_bytes = 8
    try:
        run_id = (
            await client.post(f"/workflows/{workflow_id}/run", json={})
        ).json()["run_id"]
        run = (await client.get(f"/runs/{run_id}")).json()
    finally:
        settings.max_artifact_bytes = previous

    assert run["status"] == "error"
    assert "limit is 8 bytes" in run["node_runs"][0]["error"]


async def test_retention_prune_deletes_artifact_metadata_and_file(
    client: AsyncClient,
) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Prune"})).json()[
        "id"
    ]
    graph = {
        "nodes": [
            {
                "id": "writer",
                "type": "code",
                "params": {
                    "code": "output = artifacts.write_text('old', name='old.txt')"
                },
                "position": {"x": 0, "y": 0},
            }
        ],
        "edges": [],
    }
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})
    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]

    async with retention.SessionLocal() as session:
        artifact = (await session.scalars(select(Artifact))).one()
        artifact_path = Path(settings.artifacts_dir) / artifact.storage_key
        assert artifact_path.exists()
        run = (await session.scalars(select(Run).where(Run.id == run_id))).one()
        run.started_at = datetime.now(UTC) - timedelta(days=10)
        await session.commit()

    previous = settings.run_retention_days
    settings.run_retention_days = 1
    try:
        aged_out, capped_out = await retention.prune_old_runs()
    finally:
        settings.run_retention_days = previous

    assert aged_out == 1
    assert capped_out == 0
    assert not artifact_path.exists()
    async with retention.SessionLocal() as session:
        remaining = (await session.scalars(select(Artifact))).all()
    assert remaining == []
