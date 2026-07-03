from httpx import AsyncClient

from app.config import settings


async def test_scheduler_tick_runs_when_token_unset(client: AsyncClient) -> None:
    """With no shared secret configured, the endpoint is open (local dev)."""
    previous = settings.internal_api_token
    settings.internal_api_token = ""
    try:
        resp = await client.post("/internal/scheduler/tick")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
    finally:
        settings.internal_api_token = previous


async def test_scheduler_tick_requires_matching_token(client: AsyncClient) -> None:
    previous = settings.internal_api_token
    settings.internal_api_token = "secret-abc"
    try:
        # No header → rejected.
        resp = await client.post("/internal/scheduler/tick")
        assert resp.status_code == 401

        # Wrong header → rejected.
        resp = await client.post(
            "/internal/scheduler/tick",
            headers={"X-Nodyra-Internal-Token": "wrong"},
        )
        assert resp.status_code == 401

        # Right header → ok.
        resp = await client.post(
            "/internal/scheduler/tick",
            headers={"X-Nodyra-Internal-Token": "secret-abc"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
    finally:
        settings.internal_api_token = previous
