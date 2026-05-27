from httpx import AsyncClient


def _manual_echo_graph(value: str) -> dict:
    return {
        "nodes": [
            {
                "id": "trigger",
                "type": "manual_trigger",
                "params": {},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "code",
                "type": "code",
                "params": {"code": f"output = {value!r}"},
                "position": {"x": 250, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "trigger",
                "source_output": "main",
                "target": "code",
                "target_input": "input",
            }
        ],
    }


async def test_deployment_runs_pinned_published_version(
    client: AsyncClient,
) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Versioned"})
    ).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": _manual_echo_graph("one")}
    )
    published = (
        await client.post(f"/workflows/{workflow_id}/publish", json={})
    ).json()

    deployment = (
        await client.post(
            "/deployments",
            json={"workflow_id": workflow_id, "name": "prod", "active": True},
        )
    ).json()
    assert deployment["workflow_version_id"] == published["workflow_version_id"]
    assert deployment["workflow_version"] == 2

    await client.put(
        f"/workflows/{workflow_id}", json={"graph": _manual_echo_graph("two")}
    )

    manual_run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    manual = (await client.get(f"/runs/{manual_run_id}")).json()
    manual_nodes = {n["node_id"]: n for n in manual["node_runs"]}
    assert manual_nodes["code"]["output"]["main"] == "two"
    assert manual["workflow_version_id"] is None

    prod_run_id = (
        await client.post(f"/deployments/{deployment['id']}/run")
    ).json()["run_id"]
    prod = (await client.get(f"/runs/{prod_run_id}")).json()
    prod_nodes = {n["node_id"]: n for n in prod["node_runs"]}
    assert prod_nodes["code"]["output"]["main"] == "one"
    assert prod["workflow_version_id"] == published["workflow_version_id"]
    assert prod["deployment_id"] == deployment["id"]


async def test_error_workflow_runs_with_failure_payload(client: AsyncClient) -> None:
    handler_id = (
        await client.post("/workflows", json={"name": "Error handler"})
    ).json()["id"]
    handler_graph = {
        "nodes": [
            {
                "id": "trigger",
                "type": "manual_trigger",
                "params": {},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "capture",
                "type": "code",
                "params": {"code": "output = input"},
                "position": {"x": 250, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "trigger",
                "source_output": "main",
                "target": "capture",
                "target_input": "input",
            }
        ],
    }
    await client.put(f"/workflows/{handler_id}", json={"graph": handler_graph})
    await client.post(f"/workflows/{handler_id}/publish", json={})

    workflow_id = (await client.post("/workflows", json={"name": "Fails"})).json()[
        "id"
    ]
    fail_graph = {
        "nodes": [
            {
                "id": "t",
                "type": "manual_trigger",
                "params": {},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "boom",
                "type": "code",
                "params": {"code": "print('about to fail')\nraise RuntimeError('bad')"},
                "position": {"x": 250, "y": 0},
            },
        ],
        "edges": [
            {
                "id": "e",
                "source": "t",
                "source_output": "main",
                "target": "boom",
                "target_input": "input",
            }
        ],
    }
    await client.put(
        f"/workflows/{workflow_id}",
        json={"graph": fail_graph, "error_workflow_id": handler_id},
    )

    run_id = (
        await client.post(f"/workflows/{workflow_id}/run", json={})
    ).json()["run_id"]
    run = (await client.get(f"/runs/{run_id}")).json()
    assert run["status"] == "error"

    runs = (await client.get("/runs")).json()
    error_runs = [r for r in runs if r["trigger_type"] == "error"]
    assert len(error_runs) == 1
    handler_run = (await client.get(f"/runs/{error_runs[0]['id']}")).json()
    assert handler_run["triggered_by_error_run_id"] == run_id
    node_runs = {n["node_id"]: n for n in handler_run["node_runs"]}
    payload = node_runs["capture"]["output"]["main"]
    assert payload["run_id"] == run_id
    assert payload["failed_node_id"] == "boom"
    assert "bad" in payload["error"]
    assert payload["retry_path"] == f"/executions?run={run_id}"


async def test_ai_builder_returns_and_applies_editable_graph(
    client: AsyncClient,
) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "AI"})).json()["id"]
    response = (
        await client.post(
            f"/workflows/{workflow_id}/ai-draft",
            json={
                "prompt": (
                    "When a GitHub issue is opened, summarize it with OpenAI "
                    "and post to Slack."
                ),
                "apply": True,
            },
        )
    ).json()

    node_types = [node["type"] for node in response["graph"]["nodes"]]
    assert node_types == ["webhook_trigger", "openai_chat", "slack_send_message"]
    assert "OpenAI API key" in response["missing_credentials"]
    assert "Slack bot token" in response["missing_credentials"]

    workflow = (await client.get(f"/workflows/{workflow_id}")).json()
    assert [node["type"] for node in workflow["graph"]["nodes"]] == node_types
    assert workflow["has_unpublished_changes"] is True


async def test_ai_builder_attaches_existing_credentials(
    client: AsyncClient,
) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "AI with creds"})
    ).json()["id"]
    openai_cred = (
        await client.post(
            "/credentials",
            json={
                "name": "OpenAI",
                "type": "openai",
                "scope": "global",
                "data": {"api_key": "sk-test"},
            },
        )
    ).json()
    slack_cred = (
        await client.post(
            "/credentials",
            json={
                "name": "Slack",
                "type": "slack_bot",
                "scope": "global",
                "data": {"bot_token": "xoxb-test"},
            },
        )
    ).json()

    response = (
        await client.post(
            f"/workflows/{workflow_id}/ai-draft",
            json={
                "prompt": "Summarize incoming webhook with OpenAI and post to Slack.",
            },
        )
    ).json()

    nodes = {node["id"]: node for node in response["graph"]["nodes"]}
    summarize_key = nodes["summarize"]["params"]["api_key"]
    slack_token = nodes["notify_slack"]["params"]["bot_token"]

    assert summarize_key == {
        "__noodle_credential__": True,
        "id": openai_cred["id"],
        "key": "api_key",
    }
    assert slack_token == {
        "__noodle_credential__": True,
        "id": slack_cred["id"],
        "key": "bot_token",
    }
    assert response["missing_credentials"] == []


async def test_ai_builder_skips_attach_when_credential_ambiguous(
    client: AsyncClient,
) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "AI"})).json()["id"]
    for name in ("Prod OpenAI", "Dev OpenAI"):
        await client.post(
            "/credentials",
            json={
                "name": name,
                "type": "openai",
                "scope": "global",
                "data": {"api_key": "sk-test"},
            },
        )

    response = (
        await client.post(
            f"/workflows/{workflow_id}/ai-draft",
            json={"prompt": "Summarize input with OpenAI."},
        )
    ).json()

    summarize = next(
        node for node in response["graph"]["nodes"] if node["id"] == "summarize"
    )
    assert summarize["params"]["api_key"] == ""
    assert "OpenAI API key" in response["missing_credentials"]
