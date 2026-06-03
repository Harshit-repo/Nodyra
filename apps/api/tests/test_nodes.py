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
        "google_sheets_read_v2",
        "google_sheets_append_v2",
        "github_get_repo_v2",
        "github_create_issue_v2",
        "slack_send_message_v2",
        "stripe_create_customer_v2",
        "airtable_list_records_v2",
        "airtable_create_record_v2",
        "notion_create_page_v2",
    } <= ids

    if_manifest = next(m for m in manifests if m["id"] == "if")
    assert [o["name"] for o in if_manifest["outputs"]] == ["true", "false"]

    sheets = next(m for m in manifests if m["id"] == "google_sheets_append_v2")
    params = {param["name"]: param for param in sheets["params"]}
    assert params["credentials"]["credential"]["type"] == "google_sheets_oauth2"
    assert params["credentials"]["required_scopes"] == [
        "https://www.googleapis.com/auth/spreadsheets"
    ]

    legacy_sheets = next(m for m in manifests if m["id"] == "google_sheets_append")
    assert legacy_sheets["hidden"] is True
    assert legacy_sheets["deprecated"] is True
    assert legacy_sheets["replacement_id"] == "google_sheets_append_v2"

    legacy_github = next(m for m in manifests if m["id"] == "github_create_issue")
    assert legacy_github["hidden"] is True
    assert legacy_github["deprecated"] is True
    assert legacy_github["replacement_id"] == "github_create_issue_v2"

    legacy_slack = next(m for m in manifests if m["id"] == "slack_send_message")
    assert legacy_slack["hidden"] is True
    assert legacy_slack["deprecated"] is True
    assert legacy_slack["replacement_id"] == "slack_send_message_v2"

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


async def test_generated_node_source_endpoint_uses_stored_source(
    client: AsyncClient,
) -> None:
    resp = await client.get("/nodes/google_sheets_append_v2/source")
    assert resp.status_code == 200
    source = resp.json()

    assert source["kind"] == "builtin"
    assert source["editable"] is False
    assert "def google_sheets_append_v2(" in source["source"]
    assert "execute_registered_operation" in source["source"]
    assert "google_sheets.values.append" in source["source"]
    assert source["fork_source"] == source["source"]
