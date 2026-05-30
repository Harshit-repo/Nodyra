from httpx import AsyncClient


class FakeResponse:
    status_code = 200
    text = '{"ok": true}'
    content = b'{"ok": true}'

    def json(self) -> dict:
        return {"ok": True}


async def test_scoped_credentials_resolve_by_specificity(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Flow"})).json()["id"]
    env_id = (
        await client.post("/environments", json={"name": "Prod", "packages": []})
    ).json()["id"]

    await client.post(
        "/credentials",
        json={
            "name": "github",
            "type": "apiKey",
            "scope": "global",
            "data": {"token": "global-secret"},
        },
    )
    await client.post(
        "/credentials",
        json={
            "name": "github",
            "type": "apiKey",
            "scope": "environment",
            "environment_id": env_id,
            "data": {"token": "env-secret"},
        },
    )
    workflow_cred = (
        await client.post(
            "/credentials",
            json={
                "name": "github",
                "type": "apiKey",
                "scope": "workflow",
                "workflow_id": workflow_id,
                "data": {"token": "workflow-secret"},
            },
        )
    ).json()

    resolved = (
        await client.get(
            "/credentials/resolve",
            params={
                "name": "github",
                "type": "apiKey",
                "workflow_id": workflow_id,
                "environment_id": env_id,
            },
        )
    ).json()
    assert resolved["id"] == workflow_cred["id"]
    assert resolved["scope"] == "workflow"
    assert resolved["keys"] == ["token"]
    assert "workflow-secret" not in str(resolved)


async def test_run_logs_and_outputs_redact_known_secrets(client: AsyncClient) -> None:
    await client.post(
        "/credentials",
        json={
            "name": "service",
            "type": "apiKey",
            "scope": "global",
            "data": {"token": "super-secret-token"},
        },
    )
    workflow_id = (await client.post("/workflows", json={"name": "Redact"})).json()[
        "id"
    ]
    graph = {
        "nodes": [
            {
                "id": "t",
                "type": "manual_trigger",
                "params": {},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "c",
                "type": "code",
                "params": {
                    "code": (
                        "print('token super-secret-token')\n"
                        "output = {'token': 'super-secret-token', "
                        "'message': 'value super-secret-token'}"
                    )
                },
                "position": {"x": 250, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e",
                "source": "t",
                "source_output": "main",
                "target": "c",
                "target_input": "input",
            }
        ],
    }
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    node = next(nr for nr in run["node_runs"] if nr["node_id"] == "c")
    assert node["output"]["main"]["token"] == "***REDACTED***"
    assert node["output"]["main"]["message"] == "value ***REDACTED***"
    assert "super-secret-token" not in "\n".join(node["logs"])


async def test_integration_node_resolves_stored_credential_ref(
    client: AsyncClient, monkeypatch
) -> None:
    calls: list[dict] = []

    def fake_request(method: str, url: str, **kwargs):
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return FakeResponse()

    monkeypatch.setattr("requests.request", fake_request)

    workflow_id = (await client.post("/workflows", json={"name": "Slack"})).json()[
        "id"
    ]
    credential = (
        await client.post(
            "/credentials",
            json={
                "name": "Slack bot",
                "type": "slack_bot",
                "scope": "workflow",
                "workflow_id": workflow_id,
                "data": {"bot_token": "xoxb-stored-secret"},
            },
        )
    ).json()
    graph = {
        "nodes": [
            {
                "id": "t",
                "type": "manual_trigger",
                "params": {},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "s",
                "type": "slack_send_message",
                "params": {
                    "bot_token": {
                        "__noodle_credential__": True,
                        "id": credential["id"],
                        "key": "bot_token",
                    },
                    "channel": "C123",
                    "text": "Hello",
                },
                "position": {"x": 250, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e",
                "source": "t",
                "source_output": "main",
                "target": "s",
                "target_input": "input",
            }
        ],
    }
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()

    assert run["status"] == "success"
    assert calls[0]["kwargs"]["headers"]["Authorization"] == (
        "Bearer xoxb-stored-secret"
    )
    listed = (await client.get("/credentials")).json()
    assert listed[0]["last_used_at"] is not None
    assert "xoxb-stored-secret" not in str(run)


async def test_credential_connection_test_redacts_secret(
    client: AsyncClient, monkeypatch
) -> None:
    async def fake_request(method: str, url: str, **kwargs):  # noqa: ARG001
        return {
            "ok": False,
            "message": "bad xoxb-test-secret",
            "details": {"token": "xoxb-test-secret"},
        }

    monkeypatch.setattr("app.services.credential_tests._request", fake_request)
    credential = (
        await client.post(
            "/credentials",
            json={
                "name": "Slack",
                "type": "slack_bot",
                "scope": "global",
                "data": {"bot_token": "xoxb-test-secret"},
            },
        )
    ).json()

    response = (
        await client.post(f"/credentials/{credential['id']}/test", json={})
    ).json()

    assert response["ok"] is False
    assert response["message"] == "bad ***REDACTED***"
    assert response["details"]["token"] == "***REDACTED***"
    assert "xoxb-test-secret" not in str(response)


async def test_credential_connection_test_respects_scope(
    client: AsyncClient,
) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Flow"})).json()[
        "id"
    ]
    credential = (
        await client.post(
            "/credentials",
            json={
                "name": "Sheets",
                "type": "google_sheets",
                "scope": "workflow",
                "workflow_id": workflow_id,
                "data": {"api_key": "sheet-secret"},
            },
        )
    ).json()

    denied = await client.post(f"/credentials/{credential['id']}/test", json={})
    assert denied.status_code == 403

    allowed = (
        await client.post(
            f"/credentials/{credential['id']}/test",
            json={"workflow_id": workflow_id, "context": {}},
        )
    ).json()
    assert allowed["ok"] is False
    assert "spreadsheet_id" in allowed["message"]


async def test_list_credential_test_handlers_returns_registered_services(
    client: AsyncClient,
) -> None:
    """The editor uses this list to gate "Test connection" actions per credential type."""
    resp = await client.get("/credentials/test-handlers")
    assert resp.status_code == 200
    services = resp.json()
    assert isinstance(services, list)
    assert services == sorted(services)  # stable ordering
    # A few known testers should be present.
    for expected in ("slack_bot", "github", "openai", "smtp"):
        assert expected in services


async def test_credential_spec_carries_test_service_metadata() -> None:
    """``CredentialSpec.test_service`` rides through to node manifests."""
    from noodle.models import CredentialSpec, NodeManifest, ParamSpec

    spec = CredentialSpec(
        type="github",
        key="token",
        label="GitHub Token",
        test_service="github",
    )
    assert spec.test_service == "github"

    # Defaults to None so existing manifests don't change shape.
    assert CredentialSpec().test_service is None

    manifest = NodeManifest(
        id="x",
        name="X",
        params=[ParamSpec(name="token", type="credential", credential=spec)],
    )
    dumped = manifest.model_dump()
    assert dumped["params"][0]["credential"]["test_service"] == "github"
