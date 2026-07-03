"""Template gallery endpoints."""

from __future__ import annotations

from httpx import AsyncClient


async def test_list_templates(client: AsyncClient) -> None:
    response = await client.get("/templates")

    assert response.status_code == 200
    templates = response.json()
    ids = {template["id"] for template in templates}
    assert {"webhook_to_slack", "daily_report_email", "api_poll_transform"} <= ids
    assert all(
        {"id", "name", "description", "tags"} <= set(template)
        for template in templates
    )


async def test_instantiate_creates_workflow(client: AsyncClient) -> None:
    response = await client.post(
        "/templates/webhook_to_slack/instantiate",
        json={"name": "My alert flow"},
    )

    assert response.status_code == 201, response.text
    workflow_id = response.json()["id"]
    detail = (await client.get(f"/workflows/{workflow_id}")).json()
    graph = detail["graph"]
    assert detail["name"] == "My alert flow"
    assert {node["type"] for node in graph["nodes"]} == {
        "webhook_trigger",
        "code",
        "slack",
    }


async def test_instantiate_unknown_template_404(client: AsyncClient) -> None:
    response = await client.post(
        "/templates/nope/instantiate",
        json={"name": "Unknown template"},
    )

    assert response.status_code == 404
