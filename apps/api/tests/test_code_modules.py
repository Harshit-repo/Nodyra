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


async def test_preview_and_manifests_do_not_execute_module_body(
    client: AsyncClient,
) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Static"})).json()[
        "id"
    ]
    source = (
        "raise RuntimeError('preview executed uploaded source')\n"
        "\n"
        "def safe(x: int, y: int = 1) -> int:\n"
        "    return x + y\n"
    )
    module = (
        await client.post(
            "/code-modules",
            json={
                "scope": "workflow",
                "workflow_id": workflow_id,
                "name": "static.py",
                "contents": source,
            },
        )
    ).json()

    preview = (await client.get(f"/code-modules/{module['id']}/preview")).json()
    assert preview["syntax_error"] is None
    assert preview["registered"] == ["safe"]

    manifests = (
        await client.get(f"/code-modules/manifests/workflow/{workflow_id}")
    ).json()
    assert [manifest["id"] for manifest in manifests] == [
        f"user:{module['id']}:safe"
    ]
    assert manifests[0]["params"][0]["name"] == "y"


async def test_preview_reports_missing_imports(client: AsyncClient) -> None:
    """Top-level imports should be detected, stdlib filtered, and the
    workflow's env packages should drive missing_in_env."""
    # The global env in tests has no packages by default.
    env = (
        await client.post(
            "/environments",
            json={"name": "Test env", "packages": ["pandas"]},
        )
    ).json()
    workflow_id = (await client.post("/workflows", json={"name": "Imp"})).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}",
        json={"environment_id": env["id"], "graph": {"nodes": [], "edges": []}},
    )
    source = (
        "import os\n"  # stdlib — filtered
        "import json\n"  # stdlib — filtered
        "import pandas as pd\n"  # installed in env — not missing
        "import requests\n"  # missing
        "from bs4 import BeautifulSoup\n"  # → beautifulsoup4 (known map)
        "from . import sibling  # ignored: relative\n"
        "\n"
        "def use(x):\n"
        "    return x\n"
    )
    module = (
        await client.post(
            "/code-modules",
            json={
                "scope": "workflow",
                "workflow_id": workflow_id,
                "name": "imps.py",
                "contents": source,
            },
        )
    ).json()
    preview = (await client.get(f"/code-modules/{module['id']}/preview")).json()
    assert "os" not in preview["imports"]
    assert "json" not in preview["imports"]
    assert "pandas" in preview["imports"]
    assert "requests" in preview["imports"]
    assert "bs4" in preview["imports"]
    # pandas is installed → not missing; requests + bs4 (mapped) are missing.
    assert "pandas" not in preview["missing_in_env"]
    assert "requests" in preview["missing_in_env"]
    assert "beautifulsoup4" in preview["missing_in_env"]
    assert preview["environment_id"] == env["id"]
    assert preview["environment_name"] == "Test env"


async def test_global_and_env_modules_visible_to_workflow(
    client: AsyncClient,
) -> None:
    """A workflow's palette should include global + its env's + its own modules."""
    env = (
        await client.post("/environments", json={"name": "shared", "packages": []})
    ).json()
    wf = (await client.post("/workflows", json={"name": "consumer"})).json()
    await client.put(
        f"/workflows/{wf['id']}",
        json={"environment_id": env["id"], "graph": {"nodes": [], "edges": []}},
    )
    # One module per scope, each defining a uniquely-named function.
    glob = (
        await client.post(
            "/code-modules",
            json={
                "scope": "global",
                "name": "g.py",
                "contents": "def g(): return 'g'\n",
            },
        )
    ).json()
    envmod = (
        await client.post(
            "/code-modules",
            json={
                "scope": "environment",
                "environment_id": env["id"],
                "name": "e.py",
                "contents": "def e(): return 'e'\n",
            },
        )
    ).json()
    wfmod = (
        await client.post(
            "/code-modules",
            json={
                "scope": "workflow",
                "workflow_id": wf["id"],
                "name": "w.py",
                "contents": "def w(): return 'w'\n",
            },
        )
    ).json()

    visible = (
        await client.get(f"/code-modules?visible_to_workflow={wf['id']}")
    ).json()
    ids = {m["id"] for m in visible}
    assert ids == {glob["id"], envmod["id"], wfmod["id"]}

    manifests = (
        await client.get(f"/code-modules/manifests/workflow/{wf['id']}")
    ).json()
    names = {m["id"].split(":")[-1] for m in manifests}
    assert names == {"g", "e", "w"}


async def test_starter_graph_wires_variable_chain(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Starter"})).json()["id"]
    source = (
        "def fetch():\n"
        "    return [1, 2, 3]\n"
        "\n"
        "def to_frame(rows, indent=2):\n"
        "    return rows\n"
        "\n"
        "def summarise(df, label='ok'):\n"
        "    return label\n"
        "\n"
        "result = fetch()\n"
        "df = to_frame(result, indent=4)\n"
        "summarise(df, label='go')\n"
    )
    module = (
        await client.post(
            "/code-modules",
            json={
                "scope": "workflow",
                "workflow_id": workflow_id,
                "name": "chain.py",
                "contents": source,
            },
        )
    ).json()
    graph = (
        await client.post(f"/code-modules/{module['id']}/starter-graph")
    ).json()
    by_id = {n["id"]: n for n in graph["nodes"]}
    assert set(by_id) == {"n_fetch", "n_to_frame", "n_summarise"}

    # Literal kwargs are pre-populated as default params.
    assert by_id["n_to_frame"]["params"] == {"indent": 4}
    assert by_id["n_summarise"]["params"] == {"label": "go"}

    edges = {(e["source"], e["target"], e["target_input"]) for e in graph["edges"]}
    assert ("n_fetch", "n_to_frame", "rows") in edges
    assert ("n_to_frame", "n_summarise", "df") in edges


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
