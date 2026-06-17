from httpx import AsyncClient


async def test_create_lists_and_fetches_workflow(client: AsyncClient) -> None:
    created = (await client.post("/workflows", json={"name": "My Flow"})).json()
    assert created["version"] == 1
    assert created["graph"] == {"nodes": [], "edges": []}

    listed = (await client.get("/workflows")).json()["items"]
    assert any(w["id"] == created["id"] for w in listed)

    fetched = (await client.get(f"/workflows/{created['id']}")).json()
    assert fetched["name"] == "My Flow"


async def test_saving_a_graph_updates_draft_until_publish(client: AsyncClient) -> None:
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
    assert updated["version"] == 1
    assert updated["has_unpublished_changes"] is True
    assert len(updated["graph"]["nodes"]) == 1

    versions = (await client.get(f"/workflows/{workflow_id}/versions")).json()
    assert [v["version"] for v in versions] == [1]

    published = (
        await client.post(
            f"/workflows/{workflow_id}/publish",
            json={"notes": "first publish"},
        )
    ).json()
    assert published["version"] == 2

    fetched = (await client.get(f"/workflows/{workflow_id}")).json()
    assert fetched["version"] == 2
    assert fetched["has_unpublished_changes"] is False

    versions = (await client.get(f"/workflows/{workflow_id}/versions")).json()
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[-1]["notes"] == "first publish"


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


async def test_workflow_summary_includes_latest_run(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Run Flow"})).json()[
        "id"
    ]
    graph = {
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
                "params": {"code": "output = {'ok': True}"},
                "position": {"x": 250, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e",
                "source": "t",
                "source_output": "main",
                "target": "c",
                "target_input": "input",
            }
        ],
    }
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})
    run_id = (await client.post(f"/workflows/{workflow_id}/run", json={})).json()[
        "run_id"
    ]

    listed = (await client.get("/workflows")).json()["items"]
    summary = next(item for item in listed if item["id"] == workflow_id)

    assert summary["last_run_id"] == run_id
    assert summary["last_run_status"] == "success"
    assert summary["last_run_started_at"] is not None
    assert summary["last_run_finished_at"] is not None


async def test_delete_workflow(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Temp"})).json()["id"]

    assert (await client.delete(f"/workflows/{workflow_id}")).status_code == 204
    assert (await client.get(f"/workflows/{workflow_id}")).status_code == 404


async def test_missing_workflow_returns_404(client: AsyncClient) -> None:
    assert (await client.get("/workflows/nope")).status_code == 404


async def test_saving_a_graph_with_a_metanode_is_accepted(client: AsyncClient) -> None:
    """``meta_node`` is a structural type the engine inlines at run time; it has
    no registry manifest, so node-type validation must exempt it (like ``user:``
    code-module types) instead of rejecting the autosave with 422."""
    workflow_id = (await client.post("/workflows", json={"name": "Meta"})).json()["id"]
    graph = {
        "nodes": [
            {
                "id": "m1",
                "type": "meta_node",
                "params": {
                    "name": "Group",
                    "execution": "transparent",
                    "subgraph": {
                        "nodes": [
                            {
                                "id": "c",
                                "type": "code",
                                "params": {"code": "output = {}"},
                                "position": {"x": 0, "y": 0},
                            }
                        ],
                        "edges": [],
                    },
                    "ports": {"inputs": [], "outputs": []},
                },
                "position": {"x": 10, "y": 20},
            }
        ],
        "edges": [],
    }
    resp = await client.put(f"/workflows/{workflow_id}", json={"graph": graph})
    assert resp.status_code == 200, resp.text
    assert resp.json()["graph"]["nodes"][0]["type"] == "meta_node"
