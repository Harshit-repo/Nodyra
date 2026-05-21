from httpx import AsyncClient


async def test_create_lists_and_fetches_workflow(client: AsyncClient) -> None:
    created = (await client.post("/workflows", json={"name": "My Flow"})).json()
    assert created["version"] == 1
    assert created["graph"] == {"nodes": [], "edges": []}

    listed = (await client.get("/workflows")).json()
    assert any(w["id"] == created["id"] for w in listed)

    fetched = (await client.get(f"/workflows/{created['id']}")).json()
    assert fetched["name"] == "My Flow"


async def test_saving_a_graph_creates_a_new_version(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Flow"})).json()["id"]

    graph = {
        "nodes": [
            {
                "id": "n1",
                "type": "manual_trigger",
                "params": {},
                "position": {"x": 10, "y": 20},
            }
        ],
        "edges": [],
    }
    updated = (
        await client.put(f"/workflows/{workflow_id}", json={"graph": graph})
    ).json()
    assert updated["version"] == 2
    assert len(updated["graph"]["nodes"]) == 1

    versions = (await client.get(f"/workflows/{workflow_id}/versions")).json()
    assert [v["version"] for v in versions] == [1, 2]


async def test_update_name_and_active_without_new_version(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Flow"})).json()["id"]

    updated = (
        await client.put(
            f"/workflows/{workflow_id}", json={"name": "Renamed", "active": True}
        )
    ).json()
    assert updated["name"] == "Renamed"
    assert updated["active"] is True
    assert updated["version"] == 1


async def test_delete_workflow(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Temp"})).json()["id"]

    assert (await client.delete(f"/workflows/{workflow_id}")).status_code == 204
    assert (await client.get(f"/workflows/{workflow_id}")).status_code == 404


async def test_missing_workflow_returns_404(client: AsyncClient) -> None:
    assert (await client.get("/workflows/nope")).status_code == 404
