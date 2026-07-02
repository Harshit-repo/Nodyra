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
