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


async def test_export_python_module(client: AsyncClient) -> None:
    workflow_id = await _workflow(client)
    resp = await client.get(f"/workflows/{workflow_id}/export.module.py")
    assert resp.status_code == 200
    assert "@node(" in resp.text
    assert "workflow_registry" in resp.text
    assert "manual_trigger" in resp.text
    assert "attachment" in resp.headers["content-disposition"]


async def test_import_preview_reports_review_required_without_persisting(
    client: AsyncClient,
) -> None:
    source = "import requests\noutput = requests.get('https://example.com').status_code\n"

    resp = await client.post(
        "/workflows/import/preview",
        json={"source": source, "source_format": "python_script"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["source_format"] == "python_script"
    assert body["importable"] is False
    assert body["partial"] is True
    assert body["summary"]["manual"] == 1


async def test_import_python_script_creates_valid_workflow(client: AsyncClient) -> None:
    source = "output = {'message': 'hello from migration'}\n"

    resp = await client.post(
        "/workflows/import",
        json={
            "name": "Migrated script",
            "source": source,
            "source_format": "python_script",
        },
    )

    assert resp.status_code == 201
    workflow = await client.get(f"/workflows/{resp.json()['workflow_id']}")
    assert workflow.status_code == 200
    assert workflow.json()["name"] == "Migrated script"
    assert {node["type"] for node in workflow.json()["graph"]["nodes"]} == {
        "manual_trigger",
        "code",
    }


async def test_import_rejects_invalid_source(client: AsyncClient) -> None:
    resp = await client.post(
        "/workflows/import/preview",
        json={"source": "if :", "source_format": "python_script"},
    )

    assert resp.status_code == 422
    assert "syntax error" in resp.json()["detail"].lower()
