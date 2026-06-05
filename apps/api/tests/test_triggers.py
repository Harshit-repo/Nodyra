import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

from app.models import ProviderTriggerSubscription, ScheduleState
from app.services import provider_triggers, triggers
from noodle_nodes.integrations_v2.providers.github import triggers as github_triggers


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


def _github_trigger_graph() -> dict:
    return {
        "nodes": [
            {
                "id": "github",
                "type": "github_repository_trigger_v2",
                "params": {
                    "credentials": {"token": "ghp_test"},
                    "owner": "octocat",
                    "repo": "hello-world",
                    "events": "push",
                    "webhook_secret": "secret",
                },
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "proc",
                "type": "code",
                "params": {"code": "output = input"},
                "position": {"x": 260, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "github",
                "source_output": "main",
                "target": "proc",
                "target_input": "input",
            }
        ],
    }


class _FakeGithubTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs))
        if method == "POST":
            return {"id": 9876}
        return {"status_code": 204}


def _github_headers(raw: bytes, *, delivery: str = "delivery-1") -> dict[str, str]:
    signature = "sha256=" + hmac.new(
        b"secret",
        raw,
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-GitHub-Event": "push",
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": signature,
    }


async def test_webhook_triggers_active_workflow(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Hooked"})
    ).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}",
        json={"graph": _webhook_graph("orders"), "active": True},
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    response = (await client.post("/webhook/orders", json={"order": 42})).json()
    assert len(response["runs"]) == 1

    run = (await client.get(f"/runs/{response['runs'][0]}")).json()
    assert run["status"] == "success"
    assert run["trigger_type"] == "webhook"
    results = {n["node_id"]: n for n in run["node_runs"]}
    assert results["proc"]["output"]["main"] == {"order": 42}


async def test_webhook_with_no_active_workflow(client: AsyncClient) -> None:
    response = (await client.post("/webhook/unknown", json={})).json()
    assert response["runs"] == []


async def test_inactive_workflow_is_not_triggered(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Off"})
    ).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}",
        json={"graph": _webhook_graph("idle"), "active": False},
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    response = (await client.post("/webhook/idle", json={})).json()
    assert response["runs"] == []


def test_match_webhook_path_exact_no_params() -> None:
    # A flat template with no placeholders matches by exact equality and
    # captures no params — existing webhooks are unchanged.
    assert triggers._match_webhook_path("orders", "orders") == {}
    assert triggers._match_webhook_path("orders", "invoices") is None


def test_match_webhook_path_single_param() -> None:
    assert triggers._match_webhook_path("products/{id}", "products/42") == {"id": "42"}
    # Literal first segment must still match.
    assert triggers._match_webhook_path("products/{id}", "users/42") is None


def test_match_webhook_path_nested_params() -> None:
    assert triggers._match_webhook_path(
        "customers/{cid}/orders/{oid}", "customers/7/orders/A-1"
    ) == {"cid": "7", "oid": "A-1"}


def test_match_webhook_path_segment_count_mismatch() -> None:
    # A param matches exactly one segment, so differing depths never match.
    assert triggers._match_webhook_path("products/{id}", "products") is None
    assert triggers._match_webhook_path("products/{id}", "products/42/reviews") is None


def test_match_webhook_path_ignores_surrounding_slashes() -> None:
    assert triggers._match_webhook_path("/products/{id}/", "products/42") == {"id": "42"}


def test_match_api_route_single_param() -> None:
    routes = [{"method": "GET", "path": "/{id}", "output": "read"}]
    assert triggers._match_api_route("customers", routes, "GET", "customers/42") == (
        "read",
        {"id": "42"},
    )


def test_match_api_route_collection_vs_item() -> None:
    routes = [
        {"method": "GET", "path": "/", "output": "list"},
        {"method": "GET", "path": "/{id}", "output": "read"},
    ]
    assert triggers._match_api_route("customers", routes, "GET", "customers") == (
        "list",
        {},
    )
    assert triggers._match_api_route("customers", routes, "GET", "customers/42") == (
        "read",
        {"id": "42"},
    )


def test_match_api_route_literal_beats_param_regardless_of_order() -> None:
    # `/new` (all-literal) must win over `/{id}` even though it is listed last.
    routes = [
        {"method": "GET", "path": "/{id}", "output": "read"},
        {"method": "GET", "path": "/new", "output": "new_form"},
    ]
    assert triggers._match_api_route("customers", routes, "GET", "customers/new") == (
        "new_form",
        {},
    )


def test_match_api_route_filters_by_method() -> None:
    routes = [
        {"method": "GET", "path": "/{id}", "output": "read"},
        {"method": "DELETE", "path": "/{id}", "output": "remove"},
    ]
    assert triggers._match_api_route("items", routes, "DELETE", "items/9") == (
        "remove",
        {"id": "9"},
    )
    assert triggers._match_api_route("items", routes, "GET", "items/9") == (
        "read",
        {"id": "9"},
    )


def test_match_api_route_no_match_returns_none() -> None:
    routes = [{"method": "GET", "path": "/{id}", "output": "read"}]
    assert triggers._match_api_route("items", routes, "GET", "items/9/orders") is None
    assert triggers._match_api_route("items", routes, "GET", "other/9") is None


def _api_endpoint_graph(
    base_path: str, routes: list[dict], response_mode: str | None = None
) -> dict:
    """API Endpoint node with one Code branch per route echoing $json.params."""
    api_params: dict = {"base_path": base_path, "routes": routes}
    if response_mode is not None:
        api_params["response_mode"] = response_mode
    nodes: list[dict] = [
        {
            "id": "api",
            "type": "api_endpoint",
            "params": api_params,
            "outputs_override": [r["output"] for r in routes],
            "position": {"x": 0, "y": 0},
        }
    ]
    edges: list[dict] = []
    for route in routes:
        out = route["output"]
        nodes.append(
            {
                "id": f"proc_{out}",
                "type": "code",
                "params": {"code": "output = input['params']"},
                "position": {"x": 250, "y": 0},
            }
        )
        edges.append(
            {
                "id": f"e_{out}",
                "source": "api",
                "source_output": out,
                "target": f"proc_{out}",
                "target_input": "input",
            }
        )
    return {"nodes": nodes, "edges": edges}


async def test_api_endpoint_routes_item_branch_with_params(
    client: AsyncClient,
) -> None:
    routes = [
        {"method": "GET", "path": "/", "output": "list"},
        {"method": "GET", "path": "/{id}", "output": "read"},
    ]
    await _publish(client, "Customers API", _api_endpoint_graph("customers", routes))

    body = (await client.get("/webhook/customers/42")).json()
    assert len(body["runs"]) == 1

    run = (await client.get(f"/runs/{body['runs'][0]}")).json()
    assert run["status"] == "success"
    results = {n["node_id"]: n for n in run["node_runs"]}
    # The item branch ran with the captured id; the collection branch did not.
    assert results["proc_read"]["output"]["main"] == {"id": "42"}
    assert "proc_list" not in results or results["proc_list"].get("status") != "success"


async def test_api_endpoint_routes_collection_branch(client: AsyncClient) -> None:
    routes = [
        {"method": "GET", "path": "/", "output": "list"},
        {"method": "GET", "path": "/{id}", "output": "read"},
    ]
    await _publish(client, "Customers API", _api_endpoint_graph("customers", routes))

    body = (await client.get("/webhook/customers")).json()
    run = (await client.get(f"/runs/{body['runs'][0]}")).json()
    results = {n["node_id"]: n for n in run["node_runs"]}
    assert results["proc_list"]["output"]["main"] == {}
    assert "proc_read" not in results or results["proc_read"].get("status") != "success"


async def test_api_endpoint_last_node_returns_branch_output(
    client: AsyncClient,
) -> None:
    routes = [{"method": "GET", "path": "/{id}", "output": "read"}]
    await _publish(
        client,
        "Read API",
        _api_endpoint_graph("widgets", routes, response_mode="Last Node"),
    )

    response = await client.get("/webhook/widgets/7")
    assert response.json() == {"id": "7"}


async def test_api_endpoint_post_routes_to_create_branch(
    client: AsyncClient,
) -> None:
    routes = [
        {"method": "GET", "path": "/{id}", "output": "read"},
        {"method": "POST", "path": "/", "output": "create"},
    ]
    await _publish(client, "Orders API", _api_endpoint_graph("orders", routes))

    body = (await client.post("/webhook/orders", json={"sku": "X"})).json()
    run = (await client.get(f"/runs/{body['runs'][0]}")).json()
    results = {n["node_id"]: n for n in run["node_runs"]}
    assert results["proc_create"]["output"]["main"] == {}
    assert "proc_read" not in results or results["proc_read"].get("status") != "success"


async def test_api_endpoint_basic_auth_rejects_missing_credentials(
    client: AsyncClient,
) -> None:
    routes = [{"method": "GET", "path": "/{id}", "output": "read"}]
    graph = _api_endpoint_graph("secure", routes)
    graph["nodes"][0]["params"].update(
        {
            "auth_type": "basic",
            "auth_username": "alice",
            "auth_password": "wonderland",
        }
    )
    await _publish(client, "Secure API", graph)

    response = await client.get("/webhook/secure/1")
    assert response.status_code == 401


def _param_webhook_graph(path: str, method: str = "GET") -> dict:
    """Webhook whose downstream node echoes the captured path params."""
    return {
        "nodes": [
            {
                "id": "hook",
                "type": "webhook_trigger",
                "params": {"path": path, "http_method": method},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "proc",
                "type": "code",
                "params": {"code": "output = input['params']"},
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


async def _publish(client: AsyncClient, name: str, graph: dict) -> str:
    workflow_id = (await client.post("/workflows", json={"name": name})).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})
    return workflow_id


async def test_webhook_path_param_routes_and_exposes_params(
    client: AsyncClient,
) -> None:
    await _publish(client, "Products", _param_webhook_graph("products/{id}"))

    response = await client.get("/webhook/products/42")
    body = response.json()
    assert len(body["runs"]) == 1

    run = (await client.get(f"/runs/{body['runs'][0]}")).json()
    assert run["status"] == "success"
    results = {n["node_id"]: n for n in run["node_runs"]}
    assert results["proc"]["output"]["main"] == {"id": "42"}


async def test_webhook_method_must_match_node_method(client: AsyncClient) -> None:
    # A GET-configured resource must not fire on a DELETE to the same path,
    # so GET and DELETE workflows for /items/{id} stay independent.
    await _publish(client, "Read item", _param_webhook_graph("items/{id}", "GET"))

    delete_resp = (await client.delete("/webhook/items/9")).json()
    assert delete_resp["runs"] == []

    get_resp = (await client.get("/webhook/items/9")).json()
    assert len(get_resp["runs"]) == 1


async def test_github_provider_trigger_lifecycle_and_dispatch(
    client: AsyncClient,
    monkeypatch,
) -> None:
    transport = _FakeGithubTransport()
    monkeypatch.setattr(github_triggers, "_transport", lambda _credentials: transport)

    workflow_id = (
        await client.post("/workflows", json={"name": "GitHub Hook"})
    ).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}",
        json={"graph": _github_trigger_graph(), "active": True},
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    async with provider_triggers.SessionLocal() as session:
        subscription = (
            await session.scalars(
                select(ProviderTriggerSubscription).where(
                    ProviderTriggerSubscription.workflow_id == workflow_id
                )
            )
        ).one()
        subscription_id = subscription.id
        assert subscription.status == "active"
        assert subscription.external_id == "9876"
        assert subscription.callback_url.endswith(f"/provider-webhook/{subscription_id}")

    assert transport.calls[0][0] == "POST"
    assert transport.calls[0][1] == "/repos/octocat/hello-world/hooks"

    provider_rows = (
        await client.get(f"/workflows/{workflow_id}/provider-triggers")
    ).json()
    assert len(provider_rows) == 1
    assert provider_rows[0]["status"] == "active"
    assert provider_rows[0]["provider"] == "github"
    assert provider_rows[0]["trigger_key"] == "github.repository.webhook"
    assert provider_rows[0]["callback_url"].endswith(
        f"/provider-webhook/{subscription_id}"
    )
    assert (
        provider_rows[0]["config"]["provider_params"]["webhook_secret"]
        == "[redacted]"
    )
    workflows = (await client.get("/workflows")).json()["items"]
    summary = next(item for item in workflows if item["id"] == workflow_id)
    assert summary["provider_trigger_counts"]["active"] == 1
    assert summary["provider_trigger_counts"]["error"] == 0
    audit_events = (await client.get("/audit")).json()["items"]
    provider_audit = [
        event
        for event in audit_events
        if event["target_type"] == "provider_trigger_subscription"
    ]
    assert any(
        event["action"] == "activate" and event["target_id"] == subscription_id
        for event in provider_audit
    )
    assert "secret" not in json.dumps(provider_audit).lower()

    payload = {
        "repository": {"full_name": "octocat/hello-world"},
        "sender": {"login": "octocat"},
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    response = await client.post(
        f"/provider-webhook/{subscription_id}",
        content=raw,
        headers=_github_headers(raw),
    )
    assert response.status_code == 202
    run_ids = response.json()["runs"]
    assert len(run_ids) == 1

    run = (await client.get(f"/runs/{run_ids[0]}")).json()
    assert run["status"] == "success"
    assert run["trigger_type"] == "provider"
    results = {node["node_id"]: node for node in run["node_runs"]}
    output = results["proc"]["output"]["main"]
    assert output["event"] == "push"
    assert output["repository"]["full_name"] == "octocat/hello-world"
    timeline = (await client.get(f"/runs/{run_ids[0]}/timeline")).json()
    provider_events = [
        event
        for event in timeline["events"]
        if event["type"] == "provider_trigger_received"
    ]
    assert provider_events
    provider_data = provider_events[0]["data"]
    assert provider_data["provider"] == "github"
    assert provider_data["event"] == "push"
    assert provider_data["repository"] == "octocat/hello-world"
    assert provider_data["response_status"] == 202
    assert isinstance(provider_data["latency_ms"], int)
    assert "secret" not in json.dumps(provider_data).lower()

    duplicate = await client.post(
        f"/provider-webhook/{subscription_id}",
        content=raw,
        headers=_github_headers(raw),
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["runs"] == []
    provider_rows = (
        await client.get(f"/workflows/{workflow_id}/provider-triggers")
    ).json()
    last_delivery = provider_rows[0]["config"]["last_delivery"]
    assert last_delivery["response_status"] == 200
    assert isinstance(last_delivery["latency_ms"], int)
    assert last_delivery["duplicate"] is True
    assert last_delivery["event"] == "push"
    assert last_delivery["repository"] == "octocat/hello-world"
    assert "secret" not in json.dumps(last_delivery).lower()

    await client.put(f"/workflows/{workflow_id}", json={"active": False})
    async with provider_triggers.SessionLocal() as session:
        subscription = await session.get(ProviderTriggerSubscription, subscription_id)
        assert subscription is not None
        assert subscription.status == "deleted"

    assert (
        await client.get(f"/workflows/{workflow_id}/provider-triggers")
    ).json() == []
    deleted_rows = (
        await client.get(
            f"/workflows/{workflow_id}/provider-triggers?include_deleted=true"
        )
    ).json()
    assert deleted_rows[0]["status"] == "deleted"
    workflows = (await client.get("/workflows")).json()["items"]
    summary = next(item for item in workflows if item["id"] == workflow_id)
    assert summary["provider_trigger_counts"]["active"] == 0
    assert summary["provider_trigger_counts"]["deleted"] == 1
    audit_events = (await client.get("/audit")).json()["items"]
    provider_audit = [
        event
        for event in audit_events
        if event["target_type"] == "provider_trigger_subscription"
    ]
    assert any(
        event["action"] == "deactivate" and event["target_id"] == subscription_id
        for event in provider_audit
    )

    assert transport.calls[-1][0] == "DELETE"
    assert transport.calls[-1][1] == "/repos/octocat/hello-world/hooks/9876"


async def test_schedule_tick_fires_when_due(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Scheduled"})
    ).json()["id"]
    graph = {
        "nodes": [
            {
                "id": "sched",
                "type": "schedule_trigger",
                "params": {"interval": "minutes", "every": 1},
                "position": {"x": 0, "y": 0},
            }
        ],
        "edges": [],
    }
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    await triggers._tick()  # first sighting starts the clock, no run
    assert (await client.get(f"/workflows/{workflow_id}/runs")).json()["items"] == []

    # Rewind the persisted last_fired so the schedule is overdue.
    async with triggers.SessionLocal() as session:
        state = (
            await session.scalars(
                select(ScheduleState).where(
                    ScheduleState.workflow_id == workflow_id
                )
            )
        ).one()
        state.last_fired = datetime.now(UTC) - timedelta(hours=1)
        await session.commit()

    await triggers._tick()  # now overdue — should fire

    runs = (await client.get(f"/workflows/{workflow_id}/runs")).json()["items"]
    assert len(runs) == 1
    assert runs[0]["trigger_type"] == "schedule"


async def test_schedule_tick_honours_cron(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Cron"})
    ).json()["id"]
    graph = {
        "nodes": [
            {
                "id": "sched",
                "type": "schedule_trigger",
                "params": {"cron": "* * * * *"},  # every minute
                "position": {"x": 0, "y": 0},
            }
        ],
        "edges": [],
    }
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    await triggers._tick()  # start the clock
    assert (await client.get(f"/workflows/{workflow_id}/runs")).json()["items"] == []

    async with triggers.SessionLocal() as session:
        state = (
            await session.scalars(
                select(ScheduleState).where(
                    ScheduleState.workflow_id == workflow_id
                )
            )
        ).one()
        state.last_fired = datetime.now(UTC) - timedelta(minutes=5)
        await session.commit()

    await triggers._tick()  # a cron minute has elapsed — should fire
    runs = (await client.get(f"/workflows/{workflow_id}/runs")).json()["items"]
    assert len(runs) == 1
    assert runs[0]["trigger_type"] == "schedule"


def test_is_due_unknown_timezone_does_not_fire() -> None:
    """An invalid IANA name must not silently fall back to UTC."""
    triggers._logged_bad_tz.clear()
    last = datetime.now(UTC) - timedelta(hours=5)
    now = datetime.now(UTC)
    params = {"cron": "* * * * *", "tz": "Mars/Olympus_Mons"}
    assert triggers._is_due(params, last, now) is False
    # And the warning is flood-controlled — only the first invalid hit logs.
    assert "Mars/Olympus_Mons" in triggers._logged_bad_tz


# --- Webhook auth (Slice 21) -------------------------------------------------


def _webhook_graph_with_auth(path: str, auth_params: dict) -> dict:
    graph = _webhook_graph(path)
    graph["nodes"][0]["params"].update(auth_params)
    return graph


async def test_webhook_basic_auth_rejects_missing_credentials(
    client: AsyncClient,
) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Basic"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "secured",
        {
            "auth_type": "basic",
            "auth_username": "alice",
            "auth_password": "wonderland",
        },
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    response = await client.post("/webhook/secured", json={})
    assert response.status_code == 401


async def test_webhook_basic_auth_accepts_valid_credentials(
    client: AsyncClient,
) -> None:
    import base64

    workflow_id = (
        await client.post("/workflows", json={"name": "Basic2"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "secured2",
        {
            "auth_type": "basic",
            "auth_username": "alice",
            "auth_password": "wonderland",
        },
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    token = base64.b64encode(b"alice:wonderland").decode("ascii")
    response = await client.post(
        "/webhook/secured2",
        headers={"Authorization": f"Basic {token}"},
        json={"order": 1},
    )
    body = response.json()
    assert response.status_code == 200
    assert len(body["runs"]) == 1


async def test_webhook_header_auth_checks_value(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Header"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "header-auth",
        {
            "auth_type": "header",
            "auth_header_name": "X-API-Key",
            "auth_header_value": "supersecret",
        },
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    wrong = await client.post(
        "/webhook/header-auth",
        headers={"X-API-Key": "nope"},
        json={},
    )
    assert wrong.status_code == 401
    ok = await client.post(
        "/webhook/header-auth",
        headers={"X-API-Key": "supersecret"},
        json={},
    )
    assert ok.status_code == 200
    assert len(ok.json()["runs"]) == 1


async def test_webhook_query_auth_checks_value(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Query"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "query-auth",
        {
            "auth_type": "query",
            "auth_query_name": "token",
            "auth_query_value": "tokentokentoken",
        },
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    wrong = await client.post("/webhook/query-auth?token=wrong", json={})
    assert wrong.status_code == 401
    ok = await client.post(
        "/webhook/query-auth?token=tokentokentoken", json={}
    )
    assert ok.status_code == 200
    assert len(ok.json()["runs"]) == 1


async def test_webhook_bearer_auth_checks_token(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Bearer"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "bearer-auth",
        {"auth_type": "bearer", "auth_bearer_token": "tok-abc-123"},
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    wrong = await client.post(
        "/webhook/bearer-auth", headers={"Authorization": "Bearer nope"}, json={}
    )
    assert wrong.status_code == 401
    ok = await client.post(
        "/webhook/bearer-auth",
        headers={"Authorization": "Bearer tok-abc-123"},
        json={},
    )
    assert ok.status_code == 200
    assert len(ok.json()["runs"]) == 1


def _make_hs256_jwt(payload: dict, secret: str) -> str:
    import base64
    import hashlib
    import hmac
    import json as _json

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = b64(_json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(_json.dumps(payload).encode())
    signing_input = f"{header}.{body}".encode()
    sig = b64(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    return f"{header}.{body}.{sig}"


async def test_webhook_jwt_auth_verifies_hs256_and_exp(client: AsyncClient) -> None:
    import time

    secret = "jwt-shared-secret"
    workflow_id = (
        await client.post("/workflows", json={"name": "JWT"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "jwt-auth", {"auth_type": "jwt", "auth_jwt_secret": secret}
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    good = _make_hs256_jwt({"sub": "abc", "exp": int(time.time()) + 3600}, secret)
    forged = _make_hs256_jwt({"sub": "abc", "exp": int(time.time()) + 3600}, "wrong")
    expired = _make_hs256_jwt({"sub": "abc", "exp": int(time.time()) - 10}, secret)

    bad_sig = await client.post(
        "/webhook/jwt-auth", headers={"Authorization": f"Bearer {forged}"}, json={}
    )
    assert bad_sig.status_code == 401
    stale = await client.post(
        "/webhook/jwt-auth", headers={"Authorization": f"Bearer {expired}"}, json={}
    )
    assert stale.status_code == 401
    ok = await client.post(
        "/webhook/jwt-auth", headers={"Authorization": f"Bearer {good}"}, json={}
    )
    assert ok.status_code == 200
    assert len(ok.json()["runs"]) == 1


async def test_webhook_hmac_verification_checks_signature(
    client: AsyncClient,
) -> None:
    import hashlib
    import hmac

    secret = "whsec_test"
    workflow_id = (
        await client.post("/workflows", json={"name": "HMAC"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "hmac-hook",
        {
            "auth_type": "none",
            "hmac_verification": "on",
            "hmac_header": "X-Signature",
            "hmac_secret": secret,
            "hmac_algorithm": "sha256",
            "hmac_prefix": "sha256=",
        },
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    body = b'{"order": 42}'
    good_sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    bad = await client.post(
        "/webhook/hmac-hook",
        headers={"X-Signature": "sha256=deadbeef", "Content-Type": "application/json"},
        content=body,
    )
    assert bad.status_code == 401
    ok = await client.post(
        "/webhook/hmac-hook",
        headers={"X-Signature": good_sig, "Content-Type": "application/json"},
        content=body,
    )
    assert ok.status_code == 200
    assert len(ok.json()["runs"]) == 1


async def test_webhook_ip_allowlist_rejects_outside_caller(
    client: AsyncClient,
) -> None:
    """A non-empty ip_allowlist rejects callers outside it with 403.

    The test transport's socket peer is 127.0.0.1, which is outside 10.0.0.0/8.
    """
    workflow_id = (
        await client.post("/workflows", json={"name": "IPDeny"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "ip-deny", {"ip_allowlist": "10.0.0.0/8"}
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    resp = await client.post("/webhook/ip-deny", json={})
    assert resp.status_code == 403


async def test_webhook_ip_allowlist_accepts_listed_caller(
    client: AsyncClient,
) -> None:
    """A caller whose IP is inside the allowlist runs the workflow."""
    workflow_id = (
        await client.post("/workflows", json={"name": "IPAllow"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "ip-allow", {"ip_allowlist": "127.0.0.0/8, 10.0.0.0/8"}
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    resp = await client.post("/webhook/ip-allow", json={"ok": 1})
    assert resp.status_code == 200
    assert len(resp.json()["runs"]) == 1


async def test_webhook_ip_allowlist_honours_xff_only_when_trusted(
    client: AsyncClient,
) -> None:
    """X-Forwarded-For is consulted only when trust_proxy is on.

    With trust_proxy off (default) the spoofed XFF is ignored and the socket
    peer (127.0.0.1) decides — outside 203.0.113.0/24 → 403. With trust_proxy
    on, the forwarded client IP is honoured → 200.
    """
    untrusted_wf = (
        await client.post("/workflows", json={"name": "XFFUntrusted"})
    ).json()["id"]
    await client.put(
        f"/workflows/{untrusted_wf}",
        json={
            "graph": _webhook_graph_with_auth(
                "xff-untrusted", {"ip_allowlist": "203.0.113.0/24"}
            ),
            "active": True,
        },
    )
    await client.post(f"/workflows/{untrusted_wf}/publish", json={})
    spoofed = await client.post(
        "/webhook/xff-untrusted",
        headers={"X-Forwarded-For": "203.0.113.9"},
        json={},
    )
    assert spoofed.status_code == 403

    trusted_wf = (
        await client.post("/workflows", json={"name": "XFFTrusted"})
    ).json()["id"]
    await client.put(
        f"/workflows/{trusted_wf}",
        json={
            "graph": _webhook_graph_with_auth(
                "xff-trusted",
                {"ip_allowlist": "203.0.113.0/24", "trust_proxy": "on"},
            ),
            "active": True,
        },
    )
    await client.post(f"/workflows/{trusted_wf}/publish", json={})
    forwarded = await client.post(
        "/webhook/xff-trusted",
        headers={"X-Forwarded-For": "203.0.113.9, 10.1.1.1"},
        json={"ok": 1},
    )
    assert forwarded.status_code == 200
    assert len(forwarded.json()["runs"]) == 1


async def test_webhook_dedup_acks_repeat_without_second_run(
    client: AsyncClient,
) -> None:
    """With dedup on, a repeated key acks 200 but starts no second run.

    A distinct key runs normally; a repeat is idempotently dropped (200, no
    run) rather than rejected (401).
    """
    workflow_id = (
        await client.post("/workflows", json={"name": "Dedup"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "dedup-hook",
        {"dedup": "on", "dedup_key": "{{ $json.body['id'] }}"},
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    first = await client.post("/webhook/dedup-hook", json={"id": "evt-1"})
    assert first.status_code == 200
    assert len(first.json()["runs"]) == 1

    repeat = await client.post("/webhook/dedup-hook", json={"id": "evt-1"})
    assert repeat.status_code == 200
    assert repeat.json()["runs"] == []

    other = await client.post("/webhook/dedup-hook", json={"id": "evt-2"})
    assert other.status_code == 200
    assert len(other.json()["runs"]) == 1


async def test_webhook_response_data_no_body(client: AsyncClient) -> None:
    """response_data='No Body' returns the configured code and an empty body."""
    workflow_id = (
        await client.post("/workflows", json={"name": "NoBody"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "no-body",
        {"response_mode": "On Received", "response_data": "No Body",
         "response_code": 202},
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    resp = await client.post("/webhook/no-body", json={"x": 1})
    assert resp.status_code == 202
    assert resp.content == b""


async def test_webhook_response_data_all_entries_echoes_body(
    client: AsyncClient,
) -> None:
    """response_data='All Entries' echoes the received body verbatim."""
    workflow_id = (
        await client.post("/workflows", json={"name": "AllEntries"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "all-entries",
        {"response_mode": "On Received", "response_data": "All Entries"},
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    body = [{"id": "A1"}, {"id": "B7"}]
    resp = await client.post("/webhook/all-entries", json=body)
    assert resp.status_code == 200
    assert resp.json() == body


async def test_webhook_response_data_first_entry_json(
    client: AsyncClient,
) -> None:
    """response_data='First Entry JSON' returns the first item of the body."""
    workflow_id = (
        await client.post("/workflows", json={"name": "FirstEntry"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "first-entry",
        {"response_mode": "On Received", "response_data": "First Entry JSON"},
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    resp = await client.post(
        "/webhook/first-entry", json=[{"id": "A1"}, {"id": "B7"}]
    )
    assert resp.status_code == 200
    assert resp.json() == {"id": "A1"}


async def test_webhook_response_data_custom_body_and_headers(
    client: AsyncClient,
) -> None:
    """response_data='Custom' evaluates response_body + response_headers."""
    workflow_id = (
        await client.post("/workflows", json={"name": "Custom"})
    ).json()["id"]
    graph = _webhook_graph_with_auth(
        "custom-resp",
        {
            "response_mode": "On Received",
            "response_data": "Custom",
            "response_body": "{{ $json.body['name'] }}",
            "response_headers": {"X-Foo": "bar"},
            "response_code": 201,
        },
    )
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    resp = await client.post("/webhook/custom-resp", json={"name": "alice"})
    assert resp.status_code == 201
    assert resp.json() == "alice"
    assert resp.headers.get("X-Foo") == "bar"


async def test_webhook_raw_body_captured_as_artifact(
    client: AsyncClient,
) -> None:
    """raw_body='on' writes the raw request bytes as an artifact ref.

    The trigger payload exposes the raw bytes as ``raw_body`` (an artifact
    ref), so a binary upload never bloats the DB. The artifact is persisted
    and downloadable, and the bytes round-trip exactly.
    """
    workflow_id = (
        await client.post("/workflows", json={"name": "RawBody"})
    ).json()["id"]
    graph = _webhook_graph_with_auth("raw-cap", {"raw_body": "on"})
    # Surface the captured ref on the downstream node's output.
    graph["nodes"][1]["params"]["code"] = "output = input.get('raw_body')"
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    body = b"\x89PNG\r\n\x1a\n not-real-png-bytes \x00\x01\x02"
    resp = await client.post(
        "/webhook/raw-cap",
        headers={"Content-Type": "application/octet-stream"},
        content=body,
    )
    assert resp.status_code == 200
    run_id = resp.json()["runs"][0]

    run = (await client.get(f"/runs/{run_id}")).json()
    proc = {n["node_id"]: n for n in run["node_runs"]}["proc"]
    ref = proc["output"]["main"]
    assert ref.get("__noodle_artifact__") is True
    assert ref["size_bytes"] == len(body)

    dl = await client.get(f"/artifacts/{ref['artifact_id']}/download")
    assert dl.status_code == 200
    assert dl.content == body


def _respond_node_graph(path: str, response_mode: str) -> dict:
    """Webhook → respond_to_webhook, for Respond Node mode tests."""
    return {
        "nodes": [
            {
                "id": "hook",
                "type": "webhook_trigger",
                "params": {
                    "path": path,
                    "http_method": "POST",
                    "response_mode": response_mode,
                },
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "resp",
                "type": "respond_to_webhook",
                "params": {"status_code": 201, "body_field": "body"},
                "position": {"x": 250, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "hook",
                "source_output": "main",
                "target": "resp",
                "target_input": "input",
            }
        ],
    }


async def test_webhook_respond_node_returns_recorded_response(
    client: AsyncClient,
) -> None:
    """Respond Node mode returns whatever respond_to_webhook recorded."""
    workflow_id = (
        await client.post("/workflows", json={"name": "RespondNode"})
    ).json()["id"]
    graph = _respond_node_graph("respond-hook", "Respond Node")
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    resp = await client.post("/webhook/respond-hook", json={"hello": "world"})
    assert resp.status_code == 201
    assert resp.json() == {"hello": "world"}


async def test_webhook_last_node_returns_final_output(
    client: AsyncClient,
) -> None:
    """Last Node mode waits and returns the final node's output."""
    workflow_id = (
        await client.post("/workflows", json={"name": "LastNode"})
    ).json()["id"]
    graph = _webhook_graph_with_auth("last-node", {"response_mode": "Last Node"})
    graph["nodes"][1]["params"]["code"] = "output = {'ok': True, 'n': 7}"
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    resp = await client.post("/webhook/last-node", json={})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "n": 7}


async def test_webhook_last_node_error_returns_500(client: AsyncClient) -> None:
    """A failing run in a synchronous webhook mode returns 500."""
    workflow_id = (
        await client.post("/workflows", json={"name": "LastNodeErr"})
    ).json()["id"]
    graph = _webhook_graph_with_auth("last-err", {"response_mode": "Last Node"})
    graph["nodes"][1]["params"]["code"] = "output = input['missing_key']"
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": graph, "active": True}
    )
    await client.post(f"/workflows/{workflow_id}/publish", json={})

    resp = await client.post("/webhook/last-err", json={})
    assert resp.status_code == 500


async def test_wait_for_webhook_result_times_out(client: AsyncClient) -> None:
    """The hybrid wait returns a 504 shape when the run never finishes.

    Takes ``client`` so ``triggers.SessionLocal`` is rebound to the SQLite test
    engine; otherwise the call hits the global Postgres-default engine and fails
    in the CI ``python`` lane (no Postgres) — see also test_leader_election.
    """
    shape = await triggers.wait_for_webhook_result(
        run_id="nonexistent" + "0" * 20,
        mode="Last Node",
        response_code=200,
        timeout=0.3,
    )
    assert shape["status"] == 504


async def test_webhook_unknown_path_still_returns_200(client: AsyncClient) -> None:
    """Unknown paths return 200 with empty runs (existing behaviour).

    Only path matches that *exist but fail auth* return 401.
    """
    response = await client.post("/webhook/nobody-listens", json={})
    assert response.status_code == 200
    assert response.json()["runs"] == []


def test_is_due_different_timezones_fire_at_different_utc() -> None:
    """Same cron expression resolves to different UTC fire times per tz.

    Cron '0 9 * * *' fires daily at 09:00 local. NY (UTC-4 in May DST) fires
    at 13:00 UTC; Sydney (UTC+10) fires at 23:00 UTC. Anchor ``last`` and
    ``now`` so NY has crossed today's 09:00 but Sydney hasn't yet.
    """
    last = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)  # 08:00 NY / 22:00 Sydney
    now = datetime(2026, 5, 26, 14, 0, tzinfo=UTC)  # past 13:00 NY only

    sydney_due = triggers._is_due(
        {"cron": "0 9 * * *", "tz": "Australia/Sydney"}, last, now
    )
    ny_due = triggers._is_due(
        {"cron": "0 9 * * *", "tz": "America/New_York"}, last, now
    )
    assert ny_due is True
    assert sydney_due is False


# --- Task 8: webhook ingress role split --------------------------------------

def test_webhook_role_defaults_to_ingress() -> None:
    """The production-grade ingress posture is the default."""
    from app.config import Settings

    assert Settings().webhook_role == "ingress"


async def test_webhook_role_default_serves_production_path(client: AsyncClient) -> None:
    """Default role — production /webhook/* is reachable (not 404)."""
    resp = await client.post("/webhook/no-such-path", json={})
    # 200 is the "accepted, no matching trigger" reply; we just need NOT 404.
    assert resp.status_code != 404


async def test_webhook_role_disabled_blocks_production_but_keeps_test_paths(
    monkeypatch,
) -> None:
    """webhook_role=='disabled' — production /webhook/{path} is unmounted, but
    the editor capture paths /webhook-test/* stay available so the builder UX
    works on a control-plane-only replica."""
    import importlib

    from app import config as _config
    from app.config import Settings

    new_settings = Settings(webhook_role="disabled")
    monkeypatch.setattr(_config, "settings", new_settings)

    import app.main as _main
    reloaded = importlib.reload(_main)
    paths = {getattr(r, "path", "") for r in reloaded.app.routes}
    assert "/webhook/{path}" not in paths, (
        "production /webhook/{path} must not be mounted when role=disabled"
    )
    assert any(p.startswith("/webhook-test") for p in paths), (
        "editor /webhook-test/* paths must stay mounted when role=disabled"
    )

    # Restore default behaviour for subsequent tests in the session.
    monkeypatch.setattr(_config, "settings", Settings())
    importlib.reload(_main)


