"""Template gallery endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


async def test_list_templates(client: AsyncClient) -> None:
    response = await client.get("/templates")

    assert response.status_code == 200
    templates = response.json()
    ids = {template["id"] for template in templates}
    assert {"webhook_to_slack", "daily_report_email", "api_poll_transform"} <= ids
    assert all({"id", "name", "description", "tags"} <= set(template) for template in templates)


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


@pytest.mark.parametrize(
    "payload,status",
    [
        ({"email": "buyer@example.com", "amount": 42.5}, 200),
        ({"amount": "invalid"}, 422),
        (None, 422),
    ],
)
async def test_webhook_validation_template_returns_promised_http_status(client, payload, status):
    wid = (
        await client.post(
            "/templates/webhook_validate_respond/instantiate", json={"name": "Intake"}
        )
    ).json()["id"]
    await client.put(f"/workflows/{wid}", json={"active": True})
    published = await client.post(f"/workflows/{wid}/publish", json={})
    assert published.status_code == 200, published.text
    response = await client.post("/webhook/validated-intake", json=payload)
    assert response.status_code == status, response.text
    assert response.json()["valid"] is (status == 200)
    assert bool(response.json()["errors"]) is (status == 422)
