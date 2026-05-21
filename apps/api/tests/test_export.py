import io
import zipfile

from httpx import AsyncClient

GRAPH = {
    "nodes": [
        {
            "id": "t",
            "type": "manual_trigger",
            "params": {},
            "position": {"x": 0, "y": 0},
        }
    ],
    "edges": [],
}


async def _workflow(client: AsyncClient) -> str:
    workflow_id = (
        await client.post("/workflows", json={"name": "Export Me"})
    ).json()["id"]
    await client.put(f"/workflows/{workflow_id}", json={"graph": GRAPH})
    return workflow_id


async def test_export_python_script(client: AsyncClient) -> None:
    workflow_id = await _workflow(client)
    resp = await client.get(f"/workflows/{workflow_id}/export.py")
    assert resp.status_code == 200
    assert "WorkflowGraph" in resp.text
    assert "manual_trigger" in resp.text
    assert "attachment" in resp.headers["content-disposition"]


async def test_export_docker_bundle(client: AsyncClient) -> None:
    workflow_id = await _workflow(client)
    resp = await client.get(f"/workflows/{workflow_id}/export/docker")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

    archive = zipfile.ZipFile(io.BytesIO(resp.content))
    assert set(archive.namelist()) == {
        "workflow.py",
        "requirements.txt",
        "Dockerfile",
        "README.md",
    }
