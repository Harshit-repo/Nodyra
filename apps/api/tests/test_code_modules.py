from httpx import AsyncClient

SIMPLE_MODULE = '''
def add(x: int = 0, y: int = 0) -> int:
    """Return x + y."""
    return x + y
'''


async def test_code_module_crud_and_preview(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "WF"})).json()["id"]
    resp = await client.post(
        "/code-modules",
        json={
            "scope": "workflow",
            "workflow_id": workflow_id,
            "name": "math_utils.py",
            "contents": SIMPLE_MODULE,
        },
    )
    assert resp.status_code == 201
    module = resp.json()
    assert module["scope"] == "workflow"
    assert module["workflow_id"] == workflow_id

    preview = (await client.get(f"/code-modules/{module['id']}/preview")).json()
    assert preview["registered"] == ["add"]
    assert preview["skipped"] == []
    assert preview["syntax_error"] is None

    manifests = (
        await client.get(f"/code-modules/manifests/workflow/{workflow_id}")
    ).json()
    assert len(manifests) == 1
    manifest = manifests[0]
    assert manifest["id"] == f"user:{module['id']}:add"
    assert manifest["name"] == "add"
    assert {p["name"] for p in manifest["params"]} == {"y"}
    assert {p["name"] for p in manifest["inputs"]} == {"x"}


async def test_uploaded_function_executes_when_workflow_runs(
    client: AsyncClient,
) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Sum"})).json()["id"]
    module = (
        await client.post(
            "/code-modules",
            json={
                "scope": "workflow",
                "workflow_id": workflow_id,
                "name": "math.py",
                "contents": SIMPLE_MODULE,
            },
        )
    ).json()

    add_id = f"user:{module['id']}:add"
    graph = {
        "nodes": [
            {
                "id": "t",
                "type": "manual_trigger",
                "params": {"data": 5},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "a",
                "type": add_id,
                "params": {"y": 7},
                "position": {"x": 1, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "t",
                "source_output": "main",
                "target": "a",
                "target_input": "x",
            }
        ],
    }
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    assert run["status"] == "success"
    results = {n["node_id"]: n for n in run["node_runs"]}
    assert results["a"]["output"]["main"] == 12


async def test_preview_surfaces_syntax_errors(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Broken"})).json()["id"]
    module = (
        await client.post(
            "/code-modules",
            json={
                "scope": "workflow",
                "workflow_id": workflow_id,
                "name": "broken.py",
                "contents": "def oops(x:\n    return x\n",
            },
        )
    ).json()
    preview = (await client.get(f"/code-modules/{module['id']}/preview")).json()
    assert preview["syntax_error"] is not None
    assert preview["registered"] == []


async def test_preview_skips_kwargs_and_classes(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Mix"})).json()["id"]
    source = (
        "class Thing:\n"
        "    def method(self): pass\n"
        "\n"
        "def variadic(*args, **kwargs):\n"
        "    return list(args)\n"
        "\n"
        "def ok(x: int = 0) -> int:\n"
        "    return x + 1\n"
    )
    module = (
        await client.post(
            "/code-modules",
            json={
                "scope": "workflow",
                "workflow_id": workflow_id,
                "name": "mix.py",
                "contents": source,
            },
        )
    ).json()
    preview = (await client.get(f"/code-modules/{module['id']}/preview")).json()
    assert preview["registered"] == ["ok"]
    skipped_names = {s["name"] for s in preview["skipped"]}
    assert "variadic" in skipped_names
