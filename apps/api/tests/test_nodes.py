from httpx import AsyncClient


async def test_list_nodes_returns_manifests(client: AsyncClient) -> None:
    resp = await client.get("/nodes")
    assert resp.status_code == 200
    manifests = resp.json()
    ids = {m["id"] for m in manifests}
    assert {
        "manual_trigger",
        "if",
        "code",
        "http_request",
        "google_sheets",
        "slack",
        "github_get_repo_v2",
        "github_create_issue_v2",
        "github_put_file_contents_v2",
        "stripe_create_customer_v2",
        "stripe_create_subscription_v2",
        "airtable_list_records_v2",
        "airtable_create_record_v2",
        "airtable_batch_update_records_v2",
        "notion_create_page_v2",
        "notion_search_v2",
        "outlook_get_message_attachment_v2",
    } <= ids

    if_manifest = next(m for m in manifests if m["id"] == "if")
    assert [o["name"] for o in if_manifest["outputs"]] == ["true", "false"]

    sheets = next(m for m in manifests if m["id"] == "google_sheets")
    params = {param["name"]: param for param in sheets["params"]}
    assert params["credentials"]["credential"]["type"] == "google_sheets_oauth2"
    assert params["credentials"]["required_scopes"] == [
        "https://www.googleapis.com/auth/spreadsheets"
    ]
    # Consolidated node carries the resource → operation descriptor.
    assert sheets["integration"] is not None
    resources = {r["id"] for r in sheets["integration"]["resources"]}
    assert {"values", "spreadsheet", "sheet", "row"} <= resources

    legacy_sheets = next(m for m in manifests if m["id"] == "google_sheets_append")
    assert legacy_sheets["hidden"] is True
    assert legacy_sheets["deprecated"] is True
    assert legacy_sheets["replacement_id"] == "google_sheets"

    legacy_github = next(m for m in manifests if m["id"] == "github_create_issue")
    assert legacy_github["hidden"] is True
    assert legacy_github["deprecated"] is True
    assert legacy_github["replacement_id"] == "github_create_issue_v2"

    legacy_slack = next(m for m in manifests if m["id"] == "slack_send_message")
    assert legacy_slack["hidden"] is True
    assert legacy_slack["deprecated"] is True
    assert legacy_slack["replacement_id"] == "slack"

    legacy_stripe = next(m for m in manifests if m["id"] == "stripe_create_customer")
    assert legacy_stripe["hidden"] is True
    assert legacy_stripe["deprecated"] is True
    assert legacy_stripe["replacement_id"] == "stripe_create_customer_v2"

    legacy_airtable = next(m for m in manifests if m["id"] == "airtable_create_record")
    assert legacy_airtable["hidden"] is True
    assert legacy_airtable["deprecated"] is True
    assert legacy_airtable["replacement_id"] == "airtable_create_record_v2"

    legacy_notion = next(m for m in manifests if m["id"] == "notion_create_page")
    assert legacy_notion["hidden"] is True
    assert legacy_notion["deprecated"] is True
    assert legacy_notion["replacement_id"] == "notion_create_page_v2"

    legacy_agent = next(m for m in manifests if m["id"] == "ai_agent")
    assert legacy_agent["hidden"] is True
    assert legacy_agent["deprecated"] is True
    assert legacy_agent["replacement_id"] == "ai_agent_v2"


async def test_list_nodes_filters_api_category(client: AsyncClient) -> None:
    resp = await client.get("/nodes?category=API")
    assert resp.status_code == 200
    manifests = resp.json()
    ids = {m["id"] for m in manifests}
    assert {
        "respond_to_webhook",
        "http_request",
        "graphql_request",
        "jwt",
    } <= ids
    assert "webhook_trigger" not in ids
    assert "api_endpoint" not in ids
    assert {m["category"] for m in manifests} == {"API"}

    trigger_resp = await client.get("/nodes?category=Triggers")
    assert trigger_resp.status_code == 200
    triggers = trigger_resp.json()
    trigger_ids = {m["id"] for m in triggers}
    assert {"webhook_trigger", "api_endpoint"} <= trigger_ids
    assert next(m for m in triggers if m["id"] == "api_endpoint")["role"] == "trigger"


async def test_generated_node_source_endpoint_uses_stored_source(
    client: AsyncClient,
) -> None:
    resp = await client.get("/nodes/google_sheets/source")
    assert resp.status_code == 200
    source = resp.json()

    assert source["kind"] == "builtin"
    assert source["editable"] is False
    assert "def google_sheets(" in source["source"]
    assert "execute_integration_operation" in source["source"]
    assert source["fork_source"] == source["source"]


async def test_dynamic_options_decrypts_credential_by_id(client: AsyncClient, monkeypatch) -> None:
    import nodyra_nodes.ai_v2.model_options as mo

    captured: dict = {}

    def fake_chat_models(provider, api_key, base_url):
        captured["provider"] = provider
        captured["api_key"] = api_key
        return ["gpt-4.1-mini"]

    monkeypatch.setattr(mo, "_chat_models_for", fake_chat_models)

    cred = (
        await client.post(
            "/credentials",
            json={
                "name": "OpenAI",
                "type": "llm_provider",
                "scope": "global",
                "data": {"provider": "openai", "api_key": "sk-secret"},
            },
        )
    ).json()

    resp = await client.get(
        f"/nodes/dynamic-options/llm_models?credential_id={cred['id']}&provider=openai"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["options"] == [
        {"value": "gpt-4.1-mini", "label": "gpt-4.1-mini", "description": ""}
    ]
    assert captured["api_key"] == "sk-secret"  # decrypted server-side


async def test_dynamic_options_rejects_out_of_scope_credential(client: AsyncClient) -> None:
    wf = (await client.post("/workflows", json={"name": "Flow"})).json()["id"]
    cred = (
        await client.post(
            "/credentials",
            json={
                "name": "Scoped",
                "type": "llm_provider",
                "scope": "workflow",
                "workflow_id": wf,
                "data": {"provider": "openai", "api_key": "sk"},
            },
        )
    ).json()
    # No workflow_id in the query → credential is not visible → 403.
    resp = await client.get(f"/nodes/dynamic-options/llm_models?credential_id={cred['id']}")
    assert resp.status_code == 403


async def test_dynamic_options_unknown_loader_404(client: AsyncClient) -> None:
    resp = await client.get("/nodes/dynamic-options/nope")
    assert resp.status_code == 404


NODE_TEST_GRAPH = {
    "nodes": [
        {
            "id": "t",
            "type": "manual_trigger",
            "params": {"data": {"n": 0}},
            "position": {"x": 0, "y": 0},
        },
        {
            "id": "c",
            "type": "code",
            "params": {"code": "output = input['n'] + 1"},
            "position": {"x": 250, "y": 0},
        },
    ],
    "edges": [
        {
            "id": "e1",
            "source": "t",
            "source_output": "main",
            "target": "c",
            "target_input": "input",
        }
    ],
}


async def test_node_test_runs_target_with_supplied_input(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Node Test"})).json()[
        "id"
    ]
    await client.put(f"/workflows/{workflow_id}", json={"graph": NODE_TEST_GRAPH})

    resp = await client.post(
        f"/workflows/{workflow_id}/nodes/c/test",
        json={"inputs": {"input": {"n": 41}}, "use_pinned": False},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["workflow_id"] == workflow_id
    assert body["node_id"] == "c"
    assert body["status"] == "success"
    assert body["output"] == {"main": 42}
    assert body["cached_node_ids"] == ["t"]
    assert (await client.get(f"/workflows/{workflow_id}/runs")).json()["total"] == 0


async def test_node_test_uses_pinned_upstream_output(client: AsyncClient) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Pinned Node Test"})).json()[
        "id"
    ]
    await client.put(f"/workflows/{workflow_id}", json={"graph": NODE_TEST_GRAPH})
    await client.put(
        f"/workflows/{workflow_id}/pinned/t",
        json={"payload": {"main": {"n": 99}}},
    )

    resp = await client.post(f"/workflows/{workflow_id}/nodes/c/test", json={})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["output"] == {"main": 100}
    assert body["cached_node_ids"] == ["t"]
    assert (await client.get(f"/workflows/{workflow_id}/runs")).json()["total"] == 0


async def test_node_test_rejects_missing_upstream_input(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Missing Node Test"})
    ).json()["id"]
    await client.put(f"/workflows/{workflow_id}", json={"graph": NODE_TEST_GRAPH})

    resp = await client.post(
        f"/workflows/{workflow_id}/nodes/c/test",
        json={"use_pinned": False},
    )

    assert resp.status_code == 400
    assert "Missing cached upstream output" in resp.json()["detail"]


async def test_node_test_requires_workflow_run_permission(
    client: AsyncClient,
) -> None:
    from app.config import settings as app_settings

    app_settings.auth_required = True
    try:
        resp = await client.post(
            "/workflows/missing/nodes/c/test",
            json={"inputs": {"input": {"n": 1}}},
        )
        assert resp.status_code == 401
    finally:
        app_settings.auth_required = False
