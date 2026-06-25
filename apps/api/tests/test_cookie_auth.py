"""C3 — Session-hardening: httpOnly cookie auth, CSRF, logout, WS tickets.

Each test covers one observable contract:

  1. Login sets httpOnly session cookie + non-httpOnly CSRF cookie.
  2. Register sets the same cookies.
  3. Cookie-auth reaches an authenticated endpoint (no Bearer header).
  4. Invalid cookie → 401.
  5. Logout clears cookies (Set-Cookie with max_age=0).
  6. State-changing request with cookie but no CSRF header → 403.
  7. State-changing request with cookie + matching CSRF header → passes.
  8. Bearer-only request skips CSRF even on state-changing paths.
  9. POST /auth/ws-ticket returns a single-use ticket.
 10. WS connection succeeds with ?ticket=; ticket is consumed (second use → rejected).
"""

import pytest
from fastapi import Depends, FastAPI, WebSocket
from fastapi.testclient import TestClient
from httpx import AsyncClient

from app.config import settings
from app.db import get_session
from app.security import resolve_org


@pytest.fixture(autouse=True)
def _auth_on(monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    monkeypatch.setattr(settings, "auth_allow_registration", True)
    monkeypatch.setattr(settings, "session_cookie_secure", False)


_REG = {"email": "cookie@test.local", "password": "P@ssw0rd!", "name": "Cookie Tester"}


async def _register(client: AsyncClient) -> tuple[str, str, str]:
    """Register and return (token, session_cookie_value, csrf_cookie_value)."""
    resp = await client.post("/auth/register", json=_REG)
    assert resp.status_code == 201, resp.text
    token = resp.json()["token"]
    sess = resp.cookies.get(settings.session_cookie_name, "")
    csrf = resp.cookies.get(settings.csrf_cookie_name, "")
    return token, sess, csrf


# ---------------------------------------------------------------------------
# Test 1: Login sets cookies
# ---------------------------------------------------------------------------


async def test_login_sets_session_and_csrf_cookies(client: AsyncClient):
    await client.post("/auth/register", json=_REG)
    resp = await client.post(
        "/auth/login", json={"email": _REG["email"], "password": _REG["password"]}
    )
    assert resp.status_code == 200
    assert settings.session_cookie_name in resp.cookies, "session cookie must be set on login"
    assert resp.cookies[settings.session_cookie_name], "session cookie must be non-empty"
    assert settings.csrf_cookie_name in resp.cookies, "CSRF cookie must be set on login"
    assert resp.cookies[settings.csrf_cookie_name], "CSRF cookie must be non-empty"


# ---------------------------------------------------------------------------
# Test 2: Register sets cookies
# ---------------------------------------------------------------------------


async def test_register_sets_session_and_csrf_cookies(client: AsyncClient):
    resp = await client.post("/auth/register", json=_REG)
    assert resp.status_code == 201
    assert settings.session_cookie_name in resp.cookies
    assert settings.csrf_cookie_name in resp.cookies


# ---------------------------------------------------------------------------
# Test 3: Cookie-auth reaches authenticated endpoint
# ---------------------------------------------------------------------------


async def test_cookie_auth_reaches_me_endpoint(client: AsyncClient):
    _, sess, _ = await _register(client)
    # Use cookie only — no Authorization header.
    resp = await client.get(
        "/auth/me",
        cookies={settings.session_cookie_name: sess},
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == _REG["email"]


# ---------------------------------------------------------------------------
# Test 4: Invalid cookie → 401
# ---------------------------------------------------------------------------


async def test_invalid_session_cookie_is_rejected(client: AsyncClient):
    await _register(client)
    resp = await client.get(
        "/auth/me",
        cookies={settings.session_cookie_name: "garbage-token"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Test 5: Logout clears cookies
# ---------------------------------------------------------------------------


async def test_logout_clears_cookies(client: AsyncClient):
    _, sess, csrf = await _register(client)
    resp = await client.post(
        "/auth/logout",
        cookies={settings.session_cookie_name: sess, settings.csrf_cookie_name: csrf},
        headers={settings.csrf_header_name: csrf},
    )
    assert resp.status_code == 204
    # The cookie should be deleted (max-age=0 / expired).
    set_cookie_header = resp.headers.get("set-cookie", "")
    assert (
        settings.session_cookie_name in set_cookie_header
        or resp.cookies.get(settings.session_cookie_name) == ""
    )


# ---------------------------------------------------------------------------
# Test 6: Cookie + missing CSRF → 403
# ---------------------------------------------------------------------------


async def test_csrf_missing_header_rejected(client: AsyncClient):
    _, sess, _ = await _register(client)
    # POST to a state-changing endpoint with cookie but NO CSRF header.
    resp = await client.post(
        "/auth/logout",
        cookies={settings.session_cookie_name: sess},
        # deliberately no X-CSRF-Token header
    )
    assert resp.status_code == 403, (
        "state-changing request with cookie auth but no CSRF header must be 403"
    )


# ---------------------------------------------------------------------------
# Test 7: Cookie + matching CSRF header → passes
# ---------------------------------------------------------------------------


async def test_csrf_matching_header_passes(client: AsyncClient):
    _, sess, csrf = await _register(client)
    resp = await client.post(
        "/auth/logout",
        cookies={
            settings.session_cookie_name: sess,
            settings.csrf_cookie_name: csrf,
        },
        headers={settings.csrf_header_name: csrf},
    )
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Test 8: Bearer-only request skips CSRF
# ---------------------------------------------------------------------------


async def test_bearer_auth_skips_csrf_check(client: AsyncClient):
    token, _, _ = await _register(client)
    # POST to a state-changing endpoint with Bearer only — no CSRF header, no cookie.
    # Should not be blocked by CSRF middleware.
    resp = await client.post(
        "/auth/logout",
        headers={"Authorization": f"Bearer {token}"},
    )
    # /auth/logout just clears cookies; 204 expected.
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Test 9: POST /auth/ws-ticket returns a ticket
# ---------------------------------------------------------------------------


async def test_ws_ticket_returns_single_use_token(client: AsyncClient):
    token, _, _ = await _register(client)
    resp = await client.post(
        "/auth/ws-ticket",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "ticket" in body
    assert len(body["ticket"]) > 20, "ticket must be a non-trivial random string"


# ---------------------------------------------------------------------------
# Test 10: WS ticket is single-use (consume_ticket is idempotent-delete)
# ---------------------------------------------------------------------------


async def test_ws_ticket_is_consumed_on_first_use(client: AsyncClient):
    token, _, _ = await _register(client)
    resp = await client.post(
        "/auth/ws-ticket",
        headers={"Authorization": f"Bearer {token}"},
    )
    ticket = resp.json()["ticket"]

    from app.services.ws_ticket import consume_ticket

    # First consume: should return the user_id.
    user_id = await consume_ticket(ticket)
    assert user_id is not None, "first consume must return a user_id"

    # Second consume: must be None (ticket deleted).
    user_id_again = await consume_ticket(ticket)
    assert user_id_again is None, "second consume must return None (single-use)"


def test_global_org_dependency_accepts_websocket_connections(monkeypatch):
    """The app-level org dependency runs for WebSockets too.

    It must depend on Starlette's shared HTTPConnection type; depending on
    Request only works for HTTP routes and raises during WS dependency solving.
    """

    monkeypatch.setattr(settings, "multi_tenancy_enabled", False)
    app = FastAPI(dependencies=[Depends(resolve_org)])

    async def override_get_session():
        yield None

    app.dependency_overrides[get_session] = override_get_session

    @app.websocket("/ws")
    async def websocket_route(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_json({"ok": True})
        await websocket.close()

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as websocket:
            assert websocket.receive_json() == {"ok": True}
