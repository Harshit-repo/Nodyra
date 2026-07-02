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


async def test_saving_graph_rejects_stale_graph_revision(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Stale Flow"})).json()["id"]
    first_graph = {
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
    saved = (
        await client.put(
            f"/workflows/{workflow_id}",
            json={"graph": first_graph, "expected_graph_revision": 0},
        )
    ).json()
    assert saved["graph_revision"] == 1

    stale_graph = {
        "nodes": [
            {
                "id": "n2",
                "type": "manual_trigger",
                "params": {},
                "position": {"x": 30, "y": 40},
            }
        ],
        "edges": [],
    }
    stale = await client.put(
        f"/workflows/{workflow_id}",
        json={"graph": stale_graph, "expected_graph_revision": 0},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["current_graph_revision"] == 1

    fetched = (await client.get(f"/workflows/{workflow_id}")).json()
    assert fetched["graph_revision"] == 1
    assert fetched["graph"]["nodes"][0]["id"] == "n1"


async def test_saving_graph_records_revision_history(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "History Flow"})).json()["id"]
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
    await client.put(
        f"/workflows/{workflow_id}",
        json={"graph": graph, "expected_graph_revision": 0},
    )

    revisions = (await client.get(f"/workflows/{workflow_id}/revisions")).json()
    assert len(revisions) == 1
    assert revisions[0]["workflow_id"] == workflow_id
    assert revisions[0]["graph_revision"] == 1
    assert revisions[0]["origin"] == "ui"
    assert revisions[0]["operation"] == "set_graph"
    assert revisions[0]["patch"] == {
        "type": "graph_replaced",
        "node_count": 1,
        "edge_count": 0,
    }


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


async def test_update_nullable_runtime_bindings_can_be_cleared(
    client: AsyncClient,
) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Configured"})).json()[
        "id"
    ]
    error_workflow_id = (
        await client.post("/workflows", json={"name": "Error handler"})
    ).json()["id"]
    environment_id = (
        await client.post(
            "/environments",
            json={"name": "Workflow env", "python_version": "3.12"},
        )
    ).json()["id"]
    runner_pool_id = (
        await client.post("/runner-pools", json={"name": "Workflow pool"})
    ).json()["id"]

    configured = (
        await client.put(
            f"/workflows/{workflow_id}",
            json={
                "environment_id": environment_id,
                "default_runner_pool_id": runner_pool_id,
                "error_workflow_id": error_workflow_id,
                "run_timeout_seconds": 12,
                "mcp_tool_name": "configured_tool",
                "mcp_description": "configured",
                "mcp_parameters_schema": {"type": "object"},
            },
        )
    ).json()
    assert configured["environment_id"] == environment_id
    assert configured["default_runner_pool_id"] == runner_pool_id
    assert configured["error_workflow_id"] == error_workflow_id

    cleared = (
        await client.put(
            f"/workflows/{workflow_id}",
            json={
                "environment_id": None,
                "default_runner_pool_id": None,
                "error_workflow_id": None,
                "run_timeout_seconds": None,
                "mcp_tool_name": None,
                "mcp_description": None,
                "mcp_parameters_schema": None,
            },
        )
    ).json()
    for field in (
        "environment_id",
        "default_runner_pool_id",
        "error_workflow_id",
        "run_timeout_seconds",
        "mcp_tool_name",
        "mcp_description",
        "mcp_parameters_schema",
    ):
        assert cleared[field] is None


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


# ---------------------------------------------------------------------------
# T-10: workflow import/export round-trip
# ---------------------------------------------------------------------------


async def test_import_workflow_round_trip_t10(client: AsyncClient) -> None:
    """T-10: POST /import accepts a .module.py export and creates a workflow with the graph."""
    import noodle_nodes  # noqa: F401 - register built-ins
    from noodle.models import WorkflowGraph
    from noodle.sdk import registry
    from noodle_exporter import workflow_to_module

    graph_dict = {
        "nodes": [
            {"id": "t", "type": "manual_trigger", "params": {}, "position": {"x": 0, "y": 0}},
            {
                "id": "c",
                "type": "code",
                "params": {"code": "output = input['x'] + 1"},
                "position": {"x": 300, "y": 0},
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
    source = workflow_to_module(
        WorkflowGraph.model_validate(graph_dict), "Import Test", registry=registry
    )

    resp = await client.post("/import", json={"name": "Imported Flow", "source": source})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Imported Flow"

    wf = (await client.get(f"/workflows/{body['workflow_id']}")).json()
    node_types = {n["type"] for n in wf["graph"]["nodes"]}
    assert "manual_trigger" in node_types
    assert "code" in node_types


async def test_import_workflow_rejects_invalid_source_t10(client: AsyncClient) -> None:
    """POST /import returns 422 when the source is not a valid module export."""
    resp = await client.post("/import", json={"name": "Bad", "source": "x = 1\n"})
    assert resp.status_code == 422
