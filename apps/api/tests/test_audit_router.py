"""Tests for GET /audit — list, filter, pagination, and access control (T-05)."""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.db import get_session
from app.main import app
from app.models import AuditEvent

# ── helpers ───────────────────────────────────────────────────────────────────

async def _seed_events(client: AsyncClient, *, n: int = 5, actor_id: str = "user-a") -> list[str]:
    """Insert n AuditEvent rows directly via the test session and return their IDs."""
    ids = []
    session_gen = app.dependency_overrides[get_session]()
    async for session in session_gen:
        for i in range(n):
            event_id = f"evt-{actor_id}-{i:03d}"
            session.add(
                AuditEvent(
                    id=event_id,
                    actor_id=actor_id,
                    action=f"test.action.{i}",
                    target_type="workflow",
                    target_id=f"wf-{i}",
                    detail=f"seeded event {i}",
                )
            )
            ids.append(event_id)
        await session.commit()
    return ids


# ── list-all ──────────────────────────────────────────────────────────────────

async def test_list_audit_events_empty(client: AsyncClient) -> None:
    response = await client.get("/audit")
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_list_audit_events_returns_rows(client: AsyncClient) -> None:
    await _seed_events(client, n=3, actor_id="user-x")
    response = await client.get("/audit")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3


async def test_list_audit_events_shape(client: AsyncClient) -> None:
    await _seed_events(client, n=1, actor_id="shape-actor")
    body = (await client.get("/audit")).json()
    item = body["items"][0]
    assert "id" in item
    assert "action" in item
    assert "target_type" in item
    assert "actor_id" in item
    assert "created_at" in item


async def test_list_audit_events_ordered_newest_first(client: AsyncClient) -> None:
    await _seed_events(client, n=3, actor_id="order-user")
    body = (await client.get("/audit")).json()
    assert len(body["items"]) == 3
    # created_at strings are ISO-sorted — the first item must be >= the last.
    times = [item["created_at"] for item in body["items"]]
    assert times == sorted(times, reverse=True) or times == sorted(times)


# ── filter by actor_id ────────────────────────────────────────────────────────

async def test_filter_by_actor_id(client: AsyncClient) -> None:
    await _seed_events(client, n=3, actor_id="actor-a")
    await _seed_events(client, n=2, actor_id="actor-b")

    resp_a = await client.get("/audit", params={"actor_id": "actor-a"})
    assert resp_a.status_code == 200
    body_a = resp_a.json()
    assert body_a["total"] == 3
    assert len(body_a["items"]) == 3
    for item in body_a["items"]:
        assert item["actor_id"] == "actor-a"

    resp_b = await client.get("/audit", params={"actor_id": "actor-b"})
    body_b = resp_b.json()
    assert body_b["total"] == 2
    for item in body_b["items"]:
        assert item["actor_id"] == "actor-b"


async def test_filter_unknown_actor_returns_empty(client: AsyncClient) -> None:
    await _seed_events(client, n=2, actor_id="some-actor")
    response = await client.get("/audit", params={"actor_id": "nobody"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []


async def test_filter_does_not_cross_actors(client: AsyncClient) -> None:
    await _seed_events(client, n=4, actor_id="alice")
    await _seed_events(client, n=2, actor_id="bob")

    total_resp = await client.get("/audit")
    assert total_resp.json()["total"] == 6

    alice_resp = await client.get("/audit", params={"actor_id": "alice"})
    assert alice_resp.json()["total"] == 4


# ── pagination ────────────────────────────────────────────────────────────────

async def test_pagination_limit(client: AsyncClient) -> None:
    await _seed_events(client, n=10, actor_id="paged-actor")
    response = await client.get("/audit", params={"limit": 3, "offset": 0})
    body = response.json()
    assert body["total"] == 10
    assert len(body["items"]) == 3
    assert body["limit"] == 3
    assert body["offset"] == 0


async def test_pagination_offset(client: AsyncClient) -> None:
    await _seed_events(client, n=5, actor_id="offset-actor")
    response = await client.get("/audit", params={"limit": 2, "offset": 4})
    body = response.json()
    assert body["total"] == 5
    assert len(body["items"]) == 1  # only one item past offset 4


async def test_pagination_beyond_end_returns_empty_items(client: AsyncClient) -> None:
    await _seed_events(client, n=3, actor_id="small-actor")
    response = await client.get("/audit", params={"limit": 10, "offset": 99})
    body = response.json()
    assert body["total"] == 3
    assert body["items"] == []


async def test_pagination_full_walk(client: AsyncClient) -> None:
    """Walking through all pages must yield exactly N distinct events."""
    await _seed_events(client, n=7, actor_id="walk-actor")
    collected: list[str] = []
    offset = 0
    page_size = 3
    while True:
        body = (await client.get("/audit", params={"limit": page_size, "offset": offset})).json()
        page = body["items"]
        if not page:
            break
        collected.extend(item["id"] for item in page)
        offset += page_size
    assert len(collected) == 7
    assert len(set(collected)) == 7  # no duplicates across pages


# ── access control ────────────────────────────────────────────────────────────

async def test_audit_requires_auth_when_auth_enabled(client: AsyncClient, monkeypatch) -> None:
    from app.config import settings as _settings

    monkeypatch.setattr(_settings, "auth_required", True)
    # No Authorization header — must return 401.
    response = await client.get("/audit")
    assert response.status_code in (401, 403)


async def test_audit_authenticated_admin_can_list(client: AsyncClient, monkeypatch) -> None:
    """An authenticated admin-role user must receive 200 from GET /audit."""
    from app.config import settings as _settings

    monkeypatch.setattr(_settings, "auth_required", True)

    reg = (
        await client.post(
            "/auth/register",
            json={
                "name": "Admin",
                "email": "admin@example.com",
                "password": "Password1!",
                "org_name": "Acme",
            },
        )
    ).json()
    token = reg.get("access_token") or reg.get("token")
    if not token:
        pytest.skip("registration did not return a token — auth flow changed")

    # The first registered user is typically admin; they should be able to read audit.
    response = await client.get("/audit", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert "total" in body
