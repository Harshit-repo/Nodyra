"""Coverage for the env-level + workspace-level settings surface."""

from httpx import AsyncClient


async def test_create_environment_accepts_description_and_pool_size(
    client: AsyncClient,
) -> None:
    response = (
        await client.post(
            "/environments",
            json={
                "name": "Heavy",
                "python_version": "3.12",
                "description": "GPU-bound batch jobs",
                "runner_pool_size": 4,
            },
        )
    ).json()
    assert response["name"] == "Heavy"
    assert response["description"] == "GPU-bound batch jobs"
    assert response["runner_pool_size"] == 4


async def test_create_environment_defaults_to_pool_size_one(
    client: AsyncClient,
) -> None:
    response = (
        await client.post("/environments", json={"name": "Default"})
    ).json()
    assert response["runner_pool_size"] == 1
    assert response["description"] == ""


async def test_update_environment_changes_description_and_pool_size(
    client: AsyncClient,
) -> None:
    env = (
        await client.post(
            "/environments",
            json={"name": "Editable", "runner_pool_size": 1},
        )
    ).json()
    updated = (
        await client.patch(
            f"/environments/{env['id']}",
            json={"description": "Updated notes", "runner_pool_size": 3},
        )
    ).json()
    assert updated["description"] == "Updated notes"
    assert updated["runner_pool_size"] == 3


async def test_create_environment_rejects_invalid_pool_size(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/environments",
        json={"name": "Bad", "runner_pool_size": 0},
    )
    assert response.status_code == 422
    response = await client.post(
        "/environments",
        json={"name": "Bad", "runner_pool_size": 999},
    )
    assert response.status_code == 422


async def test_system_settings_get_returns_defaults(client: AsyncClient) -> None:
    response = (await client.get("/system-settings")).json()
    assert response["max_concurrent_runs"] >= 1
    assert response["run_retention_days"] >= 0
    assert "app_timezone" in response


async def test_system_settings_put_persists_changes(client: AsyncClient) -> None:
    updated = (
        await client.put(
            "/system-settings",
            json={
                "max_concurrent_runs": 12,
                "run_retention_days": 30,
                "max_output_bytes": 4096,
                "app_timezone": "Australia/Sydney",
            },
        )
    ).json()
    assert updated["max_concurrent_runs"] == 12
    assert updated["run_retention_days"] == 30
    assert updated["max_output_bytes"] == 4096
    assert updated["app_timezone"] == "Australia/Sydney"

    re_read = (await client.get("/system-settings")).json()
    assert re_read["max_concurrent_runs"] == 12
    assert re_read["max_output_bytes"] == 4096


async def test_system_settings_put_rejects_out_of_range(
    client: AsyncClient,
) -> None:
    response = await client.put(
        "/system-settings",
        json={"max_concurrent_runs": -1},
    )
    assert response.status_code == 422


# --- Slice 17: elastic pool presets --------------------------------------------


async def test_create_environment_elastic_preset(client: AsyncClient) -> None:
    response = await client.post(
        "/environments",
        json={
            "name": "Elastic",
            "runner_pool_size": 1,
            "runner_pool_max": 4,
        },
    )
    body = response.json()
    assert response.status_code == 201
    assert body["runner_pool_size"] == 1
    assert body["runner_pool_max"] == 4
    assert body["effective_pool_max"] == 4


async def test_create_environment_spawn_per_run_preset(client: AsyncClient) -> None:
    response = await client.post(
        "/environments",
        json={
            "name": "Spawn",
            "runner_pool_size": 0,
            "runner_pool_max": 4,
        },
    )
    body = response.json()
    assert response.status_code == 201
    assert body["runner_pool_size"] == 0
    assert body["runner_pool_max"] == 4
    assert body["effective_pool_max"] == 4


async def test_create_environment_rejects_size_zero_without_max(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/environments",
        json={"name": "Bad", "runner_pool_size": 0},
    )
    assert response.status_code == 422


async def test_create_environment_rejects_max_less_than_size(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/environments",
        json={"name": "Bad", "runner_pool_size": 3, "runner_pool_max": 1},
    )
    assert response.status_code == 422


async def test_update_environment_switches_mode(client: AsyncClient) -> None:
    env = (
        await client.post(
            "/environments",
            json={"name": "Mutable", "runner_pool_size": 2},
        )
    ).json()
    assert env["runner_pool_max"] is None

    updated = (
        await client.patch(
            f"/environments/{env['id']}",
            json={"runner_pool_size": 1, "runner_pool_max": 6},
        )
    ).json()
    assert updated["runner_pool_size"] == 1
    assert updated["runner_pool_max"] == 6
    assert updated["effective_pool_max"] == 6

    # Switching back to Fixed: explicit null clears the max.
    cleared = (
        await client.patch(
            f"/environments/{env['id']}",
            json={"runner_pool_size": 2, "runner_pool_max": None},
        )
    ).json()
    assert cleared["runner_pool_size"] == 2
    assert cleared["runner_pool_max"] is None
    assert cleared["effective_pool_max"] == 2


async def test_system_settings_accepts_worker_rss_soft_budget(
    client: AsyncClient,
) -> None:
    updated = (
        await client.put(
            "/system-settings",
            json={"worker_rss_soft_budget_bytes": 4 * 1024 * 1024 * 1024},
        )
    ).json()
    assert updated["worker_rss_soft_budget_bytes"] == 4 * 1024 * 1024 * 1024
