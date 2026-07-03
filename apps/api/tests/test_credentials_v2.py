import re
from urllib.parse import parse_qs, urlparse

from httpx import AsyncClient

from app.config import settings


def test_oauth_popup_escapes_provider_message_and_uses_nonce_csp() -> None:
    from app.routers.credentials import _oauth_popup_html

    response = _oauth_popup_html(
        success=False,
        message='</script><script>alert("x")</script>\nfailed',
        credential_id='credential-"-id',
    )
    body = response.body.decode()
    csp = response.headers["content-security-policy"]

    assert "unsafe-inline" not in csp
    nonce = re.search(r"script-src 'nonce-([^']+)'", csp)
    assert nonce is not None
    assert body.count(f'nonce="{nonce.group(1)}"') == 2
    assert "</script><script>alert" not in body
    assert "&lt;/script&gt;" in body


class FakeResponse:
    status_code = 200
    text = '{"ok": true}'
    content = b'{"ok": true}'

    def json(self) -> dict:
        return {"ok": True}


async def test_scoped_credentials_resolve_by_specificity(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Flow"})).json()["id"]
    env_id = (await client.post("/environments", json={"name": "Prod", "packages": []})).json()[
        "id"
    ]

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
    workflow_id = (await client.post("/workflows", json={"name": "Redact"})).json()["id"]
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

    run_id = (await client.post(f"/workflows/{workflow_id}/run", json={})).json()["run_id"]
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

    workflow_id = (await client.post("/workflows", json={"name": "Slack"})).json()["id"]
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
                        "__nodyra_credential__": True,
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

    run_id = (await client.post(f"/workflows/{workflow_id}/run", json={})).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()

    assert run["status"] == "success"
    assert calls[0]["kwargs"]["headers"]["Authorization"] == ("Bearer xoxb-stored-secret")
    listed = (await client.get("/credentials")).json()
    assert listed["items"][0]["last_used_at"] is not None
    assert "xoxb-stored-secret" not in str(run)


async def test_credential_connection_test_redacts_secret(client: AsyncClient, monkeypatch) -> None:
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

    response = (await client.post(f"/credentials/{credential['id']}/test", json={})).json()

    assert response["ok"] is False
    assert response["message"] == "bad ***REDACTED***"
    assert response["details"]["token"] == "***REDACTED***"
    assert "xoxb-test-secret" not in str(response)


async def test_llm_provider_connection_test_supports_openrouter(
    client: AsyncClient, monkeypatch
) -> None:
    calls: list[dict] = []

    async def fake_request(method: str, url: str, **kwargs):
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return {"ok": True, "message": "Connected", "details": {"status_code": 200}}

    monkeypatch.setattr("app.services.credential_tests._request", fake_request)
    credential = (
        await client.post(
            "/credentials",
            json={
                "name": "OpenRouter",
                "type": "llm_provider",
                "scope": "global",
                "data": {"provider": "openrouter", "api_key": "sk-or-test"},
            },
        )
    ).json()

    response = (await client.post(f"/credentials/{credential['id']}/test", json={})).json()

    assert response["ok"] is True
    assert calls == [
        {
            "method": "GET",
            "url": "https://openrouter.ai/api/v1/key",
            "kwargs": {"headers": {"Authorization": "Bearer sk-or-test"}},
        }
    ]


async def test_llm_provider_connection_test_ollama_without_api_key(
    client: AsyncClient, monkeypatch
) -> None:
    """An Ollama credential legitimately has a blank api_key — testing it must
    probe the local Ollama tags endpoint, not fail with "Missing api_key"."""
    calls: list[dict] = []

    async def fake_request(method: str, url: str, **kwargs):
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return {"ok": True, "message": "Connected", "details": {"status_code": 200}}

    monkeypatch.setattr("app.services.credential_tests._request", fake_request)
    credential = (
        await client.post(
            "/credentials",
            json={
                "name": "Ollama",
                "type": "llm_provider",
                "scope": "global",
                "data": {"provider": "ollama", "base_url": "http://localhost:11434"},
            },
        )
    ).json()

    response = (await client.post(f"/credentials/{credential['id']}/test", json={})).json()

    assert response["ok"] is True
    assert calls == [
        {
            "method": "GET",
            "url": "http://localhost:11434/api/tags",
            "kwargs": {},
        }
    ]


async def test_test_draft_runs_without_persisting(client: AsyncClient, monkeypatch) -> None:
    calls: list[dict] = []

    async def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url})
        return {"ok": True, "message": "Connected", "details": {"status_code": 200}}

    monkeypatch.setattr("app.services.credential_tests._request", fake_request)

    before = (await client.get("/credentials")).json()
    resp = await client.post(
        "/credentials/test-draft",
        json={
            "type": "llm_provider",
            "data": {"provider": "openrouter", "api_key": "sk-or-test"},
            "context": {},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    after = (await client.get("/credentials")).json()
    assert len(after) == len(before)  # nothing persisted


async def test_credential_connection_test_respects_scope(
    client: AsyncClient,
) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Flow"})).json()["id"]
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
    for expected in ("slack_bot", "github", "openai", "openrouter", "qdrant", "smtp"):
        assert expected in services


async def test_credential_type_test_services_have_handlers(client: AsyncClient) -> None:
    services = set((await client.get("/credentials/test-handlers")).json())
    specs = (await client.get("/credentials/types")).json()
    missing = [
        spec["id"]
        for spec in specs
        if spec.get("test_service") and spec["test_service"] not in services
    ]
    assert missing == []


async def test_list_credential_types_returns_backend_owned_specs(
    client: AsyncClient,
) -> None:
    resp = await client.get("/credentials/types")
    assert resp.status_code == 200
    specs = resp.json()
    by_id = {spec["id"]: spec for spec in specs}

    for expected in (
        "openai",
        "anthropic",
        "slack_bot",
        "github",
        "github_oauth2",
        "slack_oauth2",
        "google_sheets_oauth2",
        "microsoft_outlook_oauth2",
    ):
        assert expected in by_id

    openai = by_id["openai"]
    assert openai["auth_method"] == "api_key"
    assert openai["fields"][0]["key"] == "api_key"
    assert openai["fields"][0]["secret"] is True
    assert openai["test_service"] == "openai"

    google = by_id["google_sheets_oauth2"]
    assert google["auth_method"] == "oauth2"
    assert google["oauth"]["auth_url"].startswith("https://accounts.google.com/")
    assert "https://www.googleapis.com/auth/spreadsheets" in google["oauth"]["scopes"]
    assert google["fields"] == []

    microsoft = by_id["microsoft_outlook_oauth2"]
    assert microsoft["auth_method"] == "oauth2"
    assert "offline_access" in microsoft["default_scopes"]
    assert microsoft["oauth"]["token_url"].endswith("/oauth2/v2.0/token")


async def test_oauth_credential_connection_uses_declared_test_service(
    client: AsyncClient,
    monkeypatch,
) -> None:
    calls: list[dict] = []

    async def fake_request(method: str, url: str, **kwargs):
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        return {"ok": True, "message": "Connected", "details": {"status_code": 200}}

    monkeypatch.setattr("app.services.credential_tests._request", fake_request)
    credential = (
        await client.post(
            "/credentials",
            json={
                "name": "Outlook",
                "type": "microsoft_outlook_oauth2",
                "scope": "global",
                "data": {
                    "access_token": "outlook-access-token",
                    "refresh_token": "outlook-refresh-token",
                },
            },
        )
    ).json()

    response = (await client.post(f"/credentials/{credential['id']}/test", json={})).json()

    assert response["ok"] is True
    assert response["service"] == "microsoft_outlook"
    assert calls == [
        {
            "method": "GET",
            "url": "https://graph.microsoft.com/v1.0/me",
            "kwargs": {
                "headers": {"Authorization": "Bearer outlook-access-token"},
            },
        }
    ]
    assert "outlook-access-token" not in str(response)


async def test_oauth_start_and_callback_create_encrypted_credential(
    client: AsyncClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "google_oauth_client_id", "google-client")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "google-secret")
    calls: list[dict] = []

    async def fake_post_token_form(url: str, data: dict[str, str]) -> dict:
        calls.append({"url": url, "data": data})
        return {
            "access_token": "google-access-token",
            "refresh_token": "google-refresh-token",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/spreadsheets",
            "token_type": "Bearer",
        }

    monkeypatch.setattr("app.services.oauth._post_token_form", fake_post_token_form)

    started = (
        await client.post(
            "/credentials/oauth/start",
            json={
                "credential_type": "google_sheets_oauth2",
                "name": "Sheets OAuth",
            },
        )
    ).json()

    parsed = urlparse(started["authorization_url"])
    query = parse_qs(parsed.query)
    assert parsed.netloc == "accounts.google.com"
    assert query["client_id"] == ["google-client"]
    assert query["redirect_uri"] == ["http://test/credentials/oauth/callback"]
    assert query["state"] == [started["state"]]
    assert query["access_type"] == ["offline"]
    assert "https://www.googleapis.com/auth/spreadsheets" in query["scope"][0]

    callback = await client.get(
        "/credentials/oauth/callback",
        params={"code": "provider-code", "state": started["state"]},
    )
    assert callback.status_code == 200
    assert callback.headers["content-type"].startswith("text/html")
    html = callback.text
    assert "nodyra_oauth_success" in html
    assert "Sheets OAuth" in html
    assert "google-access-token" not in html
    assert calls == [
        {
            "url": "https://oauth2.googleapis.com/token",
            "data": {
                "grant_type": "authorization_code",
                "code": "provider-code",
                "redirect_uri": "http://test/credentials/oauth/callback",
                "client_id": "google-client",
                "client_secret": "google-secret",
                "scope": "https://www.googleapis.com/auth/spreadsheets",
            },
        }
    ]


async def test_oauth_start_reports_missing_provider_client_config(
    client: AsyncClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "microsoft_oauth_client_id", "")
    monkeypatch.setattr(settings, "microsoft_oauth_client_secret", "")

    response = await client.post(
        "/credentials/oauth/start",
        json={
            "credential_type": "microsoft_outlook_oauth2",
            "name": "Outlook",
        },
    )

    assert response.status_code == 503
    assert "MICROSOFT_OAUTH_CLIENT_ID" in response.text


async def test_oauth_refresh_updates_stored_token(
    client: AsyncClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "google_oauth_client_id", "google-client")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "google-secret")
    calls: list[dict] = []

    async def fake_post_token_form(url: str, data: dict[str, str]) -> dict:
        calls.append({"url": url, "data": data})
        return {
            "access_token": "fresh-access-token",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

    monkeypatch.setattr("app.services.oauth._post_token_form", fake_post_token_form)
    credential = (
        await client.post(
            "/credentials",
            json={
                "name": "Sheets",
                "type": "google_sheets_oauth2",
                "scope": "global",
                "data": {
                    "access_token": "expired-access-token",
                    "refresh_token": "refresh-token",
                    "expires_at": "2000-01-01T00:00:00Z",
                    "scope": "https://www.googleapis.com/auth/spreadsheets",
                    "token_type": "Bearer",
                },
            },
        )
    ).json()

    refreshed = (
        await client.post(f"/credentials/{credential['id']}/refresh")
    ).json()

    assert refreshed["keys"] == [
        "access_token",
        "expires_at",
        "refresh_token",
        "scope",
        "token_type",
    ]
    assert "fresh-access-token" not in str(refreshed)
    assert calls[0]["data"]["grant_type"] == "refresh_token"
    assert calls[0]["data"]["refresh_token"] == "refresh-token"
    assert calls[0]["data"]["client_secret"] == "google-secret"


async def test_expired_oauth_credential_refreshes_before_workflow_run(
    client: AsyncClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "google_oauth_client_id", "google-client")
    monkeypatch.setattr(settings, "google_oauth_client_secret", "google-secret")
    token_calls: list[dict] = []
    request_calls: list[dict] = []

    async def fake_post_token_form(url: str, data: dict[str, str]) -> dict:
        token_calls.append({"url": url, "data": data})
        return {
            "access_token": "runtime-fresh-access",
            "expires_in": 3600,
            "token_type": "Bearer",
        }

    def fake_request(method: str, url: str, **kwargs):
        request_calls.append({"method": method, "url": url, "kwargs": kwargs})
        return FakeResponse()

    monkeypatch.setattr("app.services.oauth._post_token_form", fake_post_token_form)
    monkeypatch.setattr("requests.request", fake_request)

    workflow_id = (await client.post("/workflows", json={"name": "Refresh run"})).json()[
        "id"
    ]
    credential = (
        await client.post(
            "/credentials",
            json={
                "name": "Sheets",
                "type": "google_sheets_oauth2",
                "scope": "workflow",
                "workflow_id": workflow_id,
                "data": {
                    "access_token": "runtime-expired-access",
                    "refresh_token": "runtime-refresh-token",
                    "expires_at": "2000-01-01T00:00:00Z",
                    "scope": "https://www.googleapis.com/auth/spreadsheets",
                    "token_type": "Bearer",
                },
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
                "id": "sheets",
                "type": "google_sheets_read",
                "params": {
                    "spreadsheet_id": "spreadsheet-id",
                    "range_name": "Sheet1!A1:B2",
                    "access_token": {
                        "__nodyra_credential__": True,
                        "id": credential["id"],
                        "key": "access_token",
                    },
                },
                "position": {"x": 250, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e",
                "source": "t",
                "source_output": "main",
                "target": "sheets",
                "target_input": "input",
            }
        ],
    }
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})

    run_id = (await client.post(f"/workflows/{workflow_id}/run", json={})).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()

    assert run["status"] == "success"
    assert token_calls[0]["data"]["refresh_token"] == "runtime-refresh-token"
    assert request_calls[0]["kwargs"]["headers"]["Authorization"] == (
        "Bearer runtime-fresh-access"
    )
    assert "runtime-expired-access" not in str(run)
    assert "runtime-fresh-access" not in str(run)


async def test_credential_spec_carries_test_service_metadata() -> None:
    """``CredentialSpec.test_service`` rides through to node manifests."""
    from nodyra.models import CredentialSpec, NodeManifest, ParamSpec

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
