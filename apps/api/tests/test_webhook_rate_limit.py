"""H5 — per-(path, IP) rate limiting on public /webhook ingress."""

import pytest
from httpx import AsyncClient

from app.config import settings
from app.services import rate_limit


def _webhook_graph(path: str) -> dict:
    return {
        "nodes": [
            {
                "id": "hook",
                "type": "webhook_trigger",
                "params": {"path": path, "http_method": "POST"},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "proc",
                "type": "code",
                "params": {"code": "output = input['body']"},
                "position": {"x": 250, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "hook",
                "source_output": "main",
                "target": "proc",
                "target_input": "input",
            }
        ],
    }


@pytest.fixture(autouse=True)
def _reset_limiter():
    rate_limit.reset()
    yield
    rate_limit.reset()


async def _publish(client: AsyncClient, path: str) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Hooked"})).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}",
        json={"graph": _webhook_graph(path), "active": True},
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})


async def test_webhook_ingress_throttles_after_limit(
    client: AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "webhook_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "webhook_rate_limit_per_minute", 2)
    await _publish(client, "orders")

    assert (await client.post("/webhook/orders", json={"n": 1})).status_code == 200
    assert (await client.post("/webhook/orders", json={"n": 2})).status_code == 200
    # Third hit in the same window is throttled.
    assert (await client.post("/webhook/orders", json={"n": 3})).status_code == 429


async def test_webhook_rate_limit_can_be_disabled(
    client: AsyncClient, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "webhook_rate_limit_enabled", False)
    monkeypatch.setattr(settings, "webhook_rate_limit_per_minute", 1)
    await _publish(client, "open")

    for _ in range(4):
        resp = await client.post("/webhook/open", json={})
        assert resp.status_code != 429
