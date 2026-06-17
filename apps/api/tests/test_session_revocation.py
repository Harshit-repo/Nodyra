"""C1 — session revocation via User.sessions_valid_after + token iat.

Stateless HMAC session tokens have no server-side store, so "log out
everywhere" / admin lockout is enforced by stamping a per-user cutoff: any
token whose ``iat`` predates the cutoff is rejected by the auth gate.
"""

import pytest
from httpx import AsyncClient

from app.config import settings


@pytest.fixture(autouse=True)
def _auth_on(monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "auth_allow_registration", True)
    monkeypatch.setattr(settings, "session_cookie_secure", False)


_REG = {"email": "revoke@test.local", "password": "P@ssw0rd!", "name": "Revoke Tester"}


async def _register(client: AsyncClient, **overrides) -> str:
    body = {**_REG, **overrides}
    resp = await client.post("/auth/register", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_logout_all_revokes_existing_token(client: AsyncClient) -> None:
    token = await _register(client)
    # Token works before revocation.
    assert (await client.get("/auth/me", headers=_bearer(token))).status_code == 200

    resp = await client.post("/auth/logout-all", headers=_bearer(token))
    assert resp.status_code == 204

    # The same token is now rejected — every prior session is invalidated.
    assert (await client.get("/auth/me", headers=_bearer(token))).status_code == 401


async def test_fresh_login_after_logout_all_works(client: AsyncClient) -> None:
    token = await _register(client)
    await client.post("/auth/logout-all", headers=_bearer(token))

    fresh = (
        await client.post(
            "/auth/login", json={"email": _REG["email"], "password": _REG["password"]}
        )
    ).json()["token"]
    assert (await client.get("/auth/me", headers=_bearer(fresh))).status_code == 200


async def test_admin_can_revoke_another_users_sessions(client: AsyncClient) -> None:
    owner_token = await _register(client)  # first user → owner
    victim_token = await _register(
        client, email="victim@test.local", name="Victim"
    )
    me = (await client.get("/auth/me", headers=_bearer(victim_token))).json()

    resp = await client.post(
        f"/auth/users/{me['id']}/revoke-sessions", headers=_bearer(owner_token)
    )
    assert resp.status_code == 204
    # Victim's outstanding token is dead; owner's own token is unaffected.
    assert (await client.get("/auth/me", headers=_bearer(victim_token))).status_code == 401
    assert (await client.get("/auth/me", headers=_bearer(owner_token))).status_code == 200


async def test_revoke_sessions_requires_user_manage(client: AsyncClient) -> None:
    owner_token = await _register(client)
    # Non-first registrations default to the "viewer" role (auth_registration_role).
    viewer_token = await _register(client, email="viewer@test.local", name="Viewer")
    me = (await client.get("/auth/me", headers=_bearer(owner_token))).json()
    resp = await client.post(
        f"/auth/users/{me['id']}/revoke-sessions", headers=_bearer(viewer_token)
    )
    assert resp.status_code == 403
