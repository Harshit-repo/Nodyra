from httpx import AsyncClient


async def test_system_status(client: AsyncClient) -> None:
    resp = await client.get("/system/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["healthy"] is True
    assert body["version"]
    assert "uptime_seconds" in body
    assert body["workflows"] == 0


async def test_metrics_exposes_prometheus_text(client: AsyncClient) -> None:
    await client.post("/workflows", json={"name": "Tracked"})

    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    text = resp.text
    assert "noodle_workflows" in text
    assert "noodle_runs_total" in text
    assert "noodle_uptime_seconds" in text
