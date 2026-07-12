import sys

from httpx import AsyncClient


async def test_create_and_list_environment(client: AsyncClient) -> None:
    created = (
        await client.post(
            "/environments",
            json={"name": "Data Science", "packages": ["pandas"]},
        )
    ).json()
    assert created["name"] == "Data Science"
    assert created["packages"] == ["pandas"]
    assert created["is_global"] is False

    listed = (await client.get("/environments")).json()
    assert any(e["id"] == created["id"] for e in listed["items"])


async def test_add_and_remove_package(client: AsyncClient) -> None:
    env_id = (await client.post("/environments", json={"name": "Env"})).json()["id"]

    after_add = (
        await client.post(
            f"/environments/{env_id}/packages", json={"package": "httpx"}
        )
    ).json()
    assert "httpx" in after_add["packages"]

    after_remove = (
        await client.delete(f"/environments/{env_id}/packages/httpx")
    ).json()
    assert "httpx" not in after_remove["packages"]


async def test_delete_environment(client: AsyncClient) -> None:
    env_id = (await client.post("/environments", json={"name": "Temp"})).json()["id"]
    assert (await client.delete(f"/environments/{env_id}")).status_code == 204
    assert (await client.get(f"/environments/{env_id}")).status_code == 404


async def test_new_workflow_has_environment_field(client: AsyncClient) -> None:
    workflow = (await client.post("/workflows", json={"name": "Flow"})).json()
    assert "environment_id" in workflow


async def test_environment_runner_pool_binding(client: AsyncClient) -> None:
    pool = (
        await client.post(
            "/runner-pools",
            json={"name": "edge", "provider": "agent", "max_concurrent_runs": 4},
        )
    ).json()

    created = (
        await client.post(
            "/environments",
            json={"name": "Bound", "runner_pool_id": pool["id"]},
        )
    ).json()
    assert created["runner_pool_id"] == pool["id"]
    assert created["runner_pool_name"] == "edge"

    # Binding is visible on list + get.
    got = (await client.get(f"/environments/{created['id']}")).json()
    assert got["runner_pool_id"] == pool["id"]
    assert got["runner_pool_name"] == "edge"

    # Unbind by sending an explicit null.
    unbound = (
        await client.patch(
            f"/environments/{created['id']}",
            json={"runner_pool_id": None},
        )
    ).json()
    assert unbound["runner_pool_id"] is None
    assert unbound["runner_pool_name"] is None


async def test_environment_rejects_unknown_runner_pool(client: AsyncClient) -> None:
    resp = await client.post(
        "/environments",
        json={"name": "Bad", "runner_pool_id": "does-not-exist"},
    )
    assert resp.status_code == 422


async def test_put_packages_replaces_list_and_dedups(client: AsyncClient) -> None:
    env = (await client.post("/environments", json={"name": "pkgtest"})).json()
    resp = await client.put(
        f"/environments/{env['id']}/packages",
        json={"packages": ["pandas", "pandas==2.1", "  ", "duckdb"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    # blank dropped; duplicate canonical name collapsed (last specifier wins)
    assert body["packages"] == ["pandas==2.1", "duckdb"]
    assert body["status"] == "pending"


async def test_package_usage_maps_packages_to_nodes(client: AsyncClient) -> None:
    glob = (await client.post("/environments", json={"name": "shared"})).json()
    wf = (await client.post("/workflows", json={"name": "uses-duckdb"})).json()
    graph = {
        "nodes": [
            {"id": "n1", "type": "duckdb_sql", "label": "My Query",
             "params": {}, "position": {"x": 0, "y": 0}},
        ],
        "edges": [],
    }
    await client.put(
        f"/workflows/{wf['id']}",
        json={"environment_id": glob["id"], "graph": graph},
    )
    resp = await client.get(f"/environments/{glob['id']}/package-usage")
    assert resp.status_code == 200
    usage = {u["package"]: u["used_by"] for u in resp.json()["packages"]}
    assert "duckdb" in usage
    assert any(
        e["workflow_id"] == wf["id"] and e["node_id"] == "n1"
        for e in usage["duckdb"]
    )


async def test_environment_backend_config_roundtrip(client: AsyncClient) -> None:
    created = (
        await client.post(
            "/environments",
            json={
                "name": "CUDA env",
                "backend": "venv",
                "backend_config": {"index_urls": ["https://download.pytorch.org/whl/cu121"]},
            },
        )
    ).json()
    assert created["backend"] == "venv"
    assert created["backend_config"] == {"index_urls": ["https://download.pytorch.org/whl/cu121"]}

    fetched = (await client.get(f"/environments/{created['id']}")).json()
    assert fetched["backend"] == "venv"
    assert fetched["backend_config"] == {"index_urls": ["https://download.pytorch.org/whl/cu121"]}


async def test_backends_endpoint_returns_platform(client: AsyncClient) -> None:
    resp = await client.get("/environments/backends")
    assert resp.status_code == 200
    data = resp.json()
    assert data["platform"] == sys.platform
    assert data["supported_python_versions"] == ["3.12", "3.13", "3.14"]
    assert data["venv"]["available"] is True
    assert "conda" in data
    assert "pixi" in data
    assert "docker" in data


async def test_backends_platform_field_is_valid_string(client: AsyncClient) -> None:
    data = (await client.get("/environments/backends")).json()
    assert isinstance(data["platform"], str)


async def test_backends_endpoint_returns_interpreters_and_flags(client: AsyncClient) -> None:
    data = (await client.get("/environments/backends")).json()
    assert data["supported_interpreters"]["cpython"] == ["3.12", "3.13", "3.14"]
    assert data["supported_interpreters"]["cpython-ft"] == ["3.13", "3.14"]
    assert data["supported_interpreters"]["pypy"] == ["3.10", "3.11"]
    assert data["supported_runtime_flags"] == ["jit", "lazy_imports"]


async def test_create_default_environment_is_cpython(client: AsyncClient) -> None:
    created = (await client.post("/environments", json={"name": "Default"})).json()
    assert created["interpreter"] == "cpython"
    assert created["runtime_flags"] == {}


async def test_create_environment_cpython_ft(client: AsyncClient) -> None:
    resp = await client.post(
        "/environments",
        json={"name": "FT env", "interpreter": "cpython-ft", "python_version": "3.14"},
    )
    assert resp.status_code == 201
    assert resp.json()["interpreter"] == "cpython-ft"


async def test_create_environment_cpython_ft_unsupported_version(client: AsyncClient) -> None:
    resp = await client.post(
        "/environments",
        json={"name": "FT env bad", "interpreter": "cpython-ft", "python_version": "3.12"},
    )
    assert resp.status_code == 422


async def test_create_environment_pypy_requires_venv_backend(client: AsyncClient) -> None:
    resp = await client.post(
        "/environments",
        json={
            "name": "PyPy conda",
            "interpreter": "pypy",
            "python_version": "3.11",
            "backend": "conda",
        },
    )
    assert resp.status_code == 422


async def test_create_environment_pypy_requires_venv_backend_pixi(client: AsyncClient) -> None:
    resp = await client.post(
        "/environments",
        json={
            "name": "PyPy pixi",
            "interpreter": "pypy",
            "python_version": "3.11",
            "backend": "pixi",
        },
    )
    assert resp.status_code == 422


async def test_create_environment_pypy_unsupported_version(client: AsyncClient) -> None:
    resp = await client.post(
        "/environments",
        json={"name": "PyPy bad version", "interpreter": "pypy", "python_version": "3.14"},
    )
    assert resp.status_code == 422


async def test_runtime_flags_roundtrip(client: AsyncClient) -> None:
    created = (
        await client.post(
            "/environments",
            json={"name": "Flagged", "runtime_flags": {"jit": True}},
        )
    ).json()
    assert created["runtime_flags"] == {"jit": True}


async def test_runtime_flags_reject_non_bool_value(client: AsyncClient) -> None:
    resp = await client.post(
        "/environments",
        json={"name": "Bad flag value", "runtime_flags": {"jit": "yes"}},
    )
    assert resp.status_code == 422


async def test_runtime_flags_reject_unknown_key(client: AsyncClient) -> None:
    resp = await client.post(
        "/environments",
        json={"name": "Bad flag key", "runtime_flags": {"unknown": True}},
    )
    assert resp.status_code == 422


async def test_runtime_flags_patch_does_not_trigger_rebuild(client: AsyncClient) -> None:
    created = (await client.post("/environments", json={"name": "Patchable"})).json()
    patched = await client.patch(
        f"/environments/{created['id']}",
        json={"runtime_flags": {"lazy_imports": True}},
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["runtime_flags"] == {"lazy_imports": True}
    # No rebuild enqueued: status is unchanged from the original create, not "pending"/"building".
    assert body["status"] == created["status"]


async def test_pypy_jit_flag_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/environments",
        json={
            "name": "PyPy jit",
            "interpreter": "pypy",
            "python_version": "3.11",
            "runtime_flags": {"jit": True},
        },
    )
    assert resp.status_code == 422


async def test_backend_config_accelerate_valid_on_venv_cpython(client: AsyncClient) -> None:
    resp = await client.post(
        "/environments",
        json={
            "name": "Accelerated",
            "backend_config": {"accelerate": {"mypyc_modules": ["my_pkg.transforms"]}},
        },
    )
    assert resp.status_code == 201
    assert resp.json()["backend_config"]["accelerate"]["mypyc_modules"] == ["my_pkg.transforms"]


async def test_backend_config_accelerate_rejected_on_pypy(client: AsyncClient) -> None:
    resp = await client.post(
        "/environments",
        json={
            "name": "Accelerated pypy",
            "interpreter": "pypy",
            "python_version": "3.11",
            "backend_config": {"accelerate": {"mypyc_modules": ["my_pkg.transforms"]}},
        },
    )
    assert resp.status_code == 422


async def test_backend_config_accelerate_malformed_module_name(client: AsyncClient) -> None:
    resp = await client.post(
        "/environments",
        json={
            "name": "Bad module name",
            "backend_config": {"accelerate": {"mypyc_modules": ["not a valid module!"]}},
        },
    )
    assert resp.status_code == 422


async def test_backend_config_accelerate_too_many_modules(client: AsyncClient) -> None:
    resp = await client.post(
        "/environments",
        json={
            "name": "Too many modules",
            "backend_config": {
                "accelerate": {"mypyc_modules": [f"pkg.mod{i}" for i in range(51)]}
            },
        },
    )
    assert resp.status_code == 422
