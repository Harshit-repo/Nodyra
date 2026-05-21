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
    assert any(e["id"] == created["id"] for e in listed)


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
