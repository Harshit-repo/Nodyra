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
    # One wired input port (the upstream envelope); both params in inspector.
    assert [p["name"] for p in manifest["inputs"]] == ["input"]
    assert {p["name"] for p in manifest["params"]} == {"x", "y"}
    by_name = {p["name"]: p for p in manifest["params"]}
    assert by_name["x"]["default"] == 0 and by_name["x"]["required"] is False
    assert by_name["y"]["default"] == 0 and by_name["y"]["required"] is False


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
    # Wire the trigger's payload into the node's input port, then pull
    # x out of $json (the upstream payload) and set y as a literal.
    graph = {
        "nodes": [
            {
                "id": "t",
                "type": "manual_trigger",
                "params": {"data": {"x": 5}},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "a",
                "type": add_id,
                "params": {"x": "{{ $json.x }}", "y": 7},
                "position": {"x": 1, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "t",
                "source_output": "main",
                "target": "a",
                "target_input": "input",
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


async def test_single_input_port_with_all_params_in_inspector(
    client: AsyncClient,
) -> None:
    """User-function nodes always have exactly one 'input' port and expose
    every function parameter as an inspector field. Required params keep
    required=True so the engine flags them if nothing supplies a value."""
    workflow_id = (
        await client.post("/workflows", json={"name": "Shape"})
    ).json()["id"]
    source = (
        "def all_defaults(x: int = 0, y: int = 0) -> int:\n"
        "    return x + y\n"
        "\n"
        "def mixed(rows: list, indent: int = 2) -> dict:\n"
        "    return {'rows': len(rows), 'indent': indent}\n"
        "\n"
        "def all_required(a, b):\n"
        "    return (a, b)\n"
    )
    module = (
        await client.post(
            "/code-modules",
            json={
                "scope": "workflow",
                "workflow_id": workflow_id,
                "name": "shape.py",
                "contents": source,
            },
        )
    ).json()
    manifests = {
        m["id"]: m
        for m in (
            await client.get(f"/code-modules/manifests/workflow/{workflow_id}")
        ).json()
    }
    for fname in ("all_defaults", "mixed", "all_required"):
        m = manifests[f"user:{module['id']}:{fname}"]
        assert [p["name"] for p in m["inputs"]] == ["input"]

    ad_params = {p["name"]: p for p in manifests[f"user:{module['id']}:all_defaults"]["params"]}
    assert set(ad_params) == {"x", "y"}
    assert all(p["required"] is False for p in ad_params.values())

    mx_params = {p["name"]: p for p in manifests[f"user:{module['id']}:mixed"]["params"]}
    assert mx_params["rows"]["required"] is True
    assert mx_params["indent"]["required"] is False
    assert mx_params["indent"]["default"] == 2

    ar_params = manifests[f"user:{module['id']}:all_required"]["params"]
    assert {p["name"] for p in ar_params} == {"a", "b"}
    assert all(p["required"] is True for p in ar_params)


async def test_engine_filters_virtual_input_port_from_call(
    client: AsyncClient,
) -> None:
    """An uploaded function without an 'input' parameter should still run
    when an edge is wired into the node's input port — the engine must
    drop the virtual port from the function call kwargs."""
    workflow_id = (await client.post("/workflows", json={"name": "Filter"})).json()["id"]
    module = (
        await client.post(
            "/code-modules",
            json={
                "scope": "workflow",
                "workflow_id": workflow_id,
                "name": "m.py",
                "contents": SIMPLE_MODULE,
            },
        )
    ).json()
    add_id = f"user:{module['id']}:add"
    graph = {
        "nodes": [
            {"id": "t", "type": "manual_trigger", "params": {"data": "ignored"},
             "position": {"x": 0, "y": 0}},
            {"id": "a", "type": add_id, "params": {"x": 4, "y": 6},
             "position": {"x": 1, "y": 0}},
        ],
        "edges": [{"id": "e", "source": "t", "source_output": "main",
                   "target": "a", "target_input": "input"}],
    }
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})
    run = (await client.get(
        f"/runs/{(await client.post(f'/workflows/{workflow_id}/run', json={})).json()['run_id']}"
    )).json()
    assert run["status"] == "success"
    a_run = next(n for n in run["node_runs"] if n["node_id"] == "a")
    assert a_run["output"]["main"] == 10


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
    # Both x and y appear as inspector params; the node has one input port.
    assert [p["name"] for p in manifests[0]["inputs"]] == ["input"]
    assert {p["name"] for p in manifests[0]["params"]} == {"x", "y"}


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

    # Variable args become {{ $json }} (bound to the wired upstream port);
    # literal args are pre-populated as default params on the inspector.
    assert by_id["n_to_frame"]["params"] == {"rows": "{{ $json }}", "indent": 4}
    assert by_id["n_summarise"]["params"] == {"df": "{{ $json }}", "label": "go"}

    # Edges target the single ``input`` port (one upstream per node).
    edges = {(e["source"], e["target"], e["target_input"]) for e in graph["edges"]}
    assert ("n_fetch", "n_to_frame", "input") in edges
    assert ("n_to_frame", "n_summarise", "input") in edges


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
