import pytest


def test_ws_workflow_streaming_replays_buffered_events(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import workflows as workflows_router
    from app.services import events as events_mod

    async def _no_redis_connect() -> str:
        return "inprocess"

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def scalar(self, _stmt):
            return "wf-live"

    monkeypatch.setattr(events_mod.broker, "connect", _no_redis_connect)
    monkeypatch.setattr(events_mod.workflow_broker, "connect", _no_redis_connect)
    monkeypatch.setattr(workflows_router, "SessionLocal", lambda: _Session())

    events_mod.workflow_broker._publish_inprocess(
        "wf-live",
        {
            "type": "workflow_graph_changed",
            "workflow_id": "wf-live",
            "graph_revision": 3,
            "origin": "mcp",
            "operation": "patch_node",
        },
    )

    with TestClient(app) as client:
        with client.websocket_connect("/ws/workflows/wf-live") as ws:
            event = ws.receive_json()

    assert event["type"] == "workflow_graph_changed"
    assert event["workflow_id"] == "wf-live"
    assert event["graph_revision"] == 3


def test_ws_workflow_streaming_scopes_visibility_to_resolved_org(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers import workflows as workflows_router
    from app.services import events as events_mod
    from app.tenancy import current_org_id

    async def _no_redis_connect() -> str:
        return "inprocess"

    async def _principal(_websocket):
        return None, "org-live"

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def scalar(self, _stmt):
            assert current_org_id.get() == "org-live"
            return "wf-live"

    monkeypatch.setattr(events_mod.broker, "connect", _no_redis_connect)
    monkeypatch.setattr(events_mod.workflow_broker, "connect", _no_redis_connect)
    monkeypatch.setattr(workflows_router, "_workflow_ws_principal", _principal)
    monkeypatch.setattr(workflows_router, "SessionLocal", lambda: _Session())

    events_mod.workflow_broker._publish_inprocess(
        "wf-live",
        {
            "type": "workflow_graph_changed",
            "workflow_id": "wf-live",
            "org_id": "org-live",
            "graph_revision": 4,
            "origin": "mcp",
            "operation": "patch_node",
        },
    )

    with TestClient(app) as client:
        with client.websocket_connect("/ws/workflows/wf-live") as ws:
            event = ws.receive_json()

    assert event["org_id"] == "org-live"
    assert event["graph_revision"] == 4


# ── IP-bound tokens must be enforced on the WebSocket too (F-22) ────────────
#
# ``auth_bind_token_to_ip`` pins a session token to the address it was minted
# from. The HTTP ``auth_gate`` middleware enforces that — but middleware does
# not run for WebSocket connections, so the token that was refused on every
# HTTP route still opened a live event stream. The whole value of pinning is
# that a stolen token is useless elsewhere, and one unenforced door is enough
# to remove it.


def _ip_bound_token(ip: str) -> str:
    from app.config import settings
    from app.services.crypto import create_token

    settings.auth_bind_token_to_ip = True
    return create_token("user-ws", client_ip=ip)


def test_a_token_bound_to_another_ip_cannot_open_the_stream(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app
    from app.models import User
    from app.routers import workflows as workflows_router
    from app.services import events as events_mod

    monkeypatch.setattr(settings, "auth_bind_token_to_ip", True)
    monkeypatch.setattr(settings, "auth_required", True)
    # The pinned address is not the one TestClient connects from.
    token = _ip_bound_token("203.0.113.9")

    async def _no_redis_connect() -> str:
        return "inprocess"

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def scalar(self, _stmt):
            return "wf-live"

        async def get(self, _model, _pk):
            return User(id="user-ws", email="ws@example.test", role="owner")

    monkeypatch.setattr(events_mod.broker, "connect", _no_redis_connect)
    monkeypatch.setattr(events_mod.workflow_broker, "connect", _no_redis_connect)
    monkeypatch.setattr(workflows_router, "SessionLocal", lambda: _Session())

    with TestClient(app) as client:
        with pytest.raises(Exception):  # noqa: B017 - starlette closes the handshake
            with client.websocket_connect(f"/ws/workflows/wf-live?token={token}"):
                pass


def test_an_unpinned_token_still_connects(monkeypatch) -> None:
    """The check must not break the ordinary case: with binding off, a token
    carries no ip claim and nothing is compared."""
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app
    from app.models import User
    from app.routers import workflows as workflows_router
    from app.services import events as events_mod
    from app.services.crypto import create_token

    monkeypatch.setattr(settings, "auth_bind_token_to_ip", False)
    monkeypatch.setattr(settings, "auth_required", True)
    token = create_token("user-ws")

    async def _no_redis_connect() -> str:
        return "inprocess"

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def scalar(self, _stmt):
            return "wf-live"

        async def get(self, _model, _pk):
            return User(id="user-ws", email="ws@example.test", role="owner")

    monkeypatch.setattr(events_mod.broker, "connect", _no_redis_connect)
    monkeypatch.setattr(events_mod.workflow_broker, "connect", _no_redis_connect)
    monkeypatch.setattr(workflows_router, "SessionLocal", lambda: _Session())

    events_mod.workflow_broker._publish_inprocess(
        "wf-live", {"type": "workflow_graph_changed", "workflow_id": "wf-live"}
    )
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/workflows/wf-live?token={token}") as ws:
            assert ws.receive_json()["type"] == "workflow_graph_changed"
