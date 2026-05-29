import pytest
from httpx import AsyncClient

import app.services.ai_builder as ai_builder_module


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
    workflow_id = (await client.post("/workflows", json={"name": "Versioned"})).json()["id"]
    await client.put(f"/workflows/{workflow_id}", json={"graph": _manual_echo_graph("one")})
    published = (await client.post(f"/workflows/{workflow_id}/publish", json={})).json()

    deployment = (
        await client.post(
            "/deployments",
            json={"workflow_id": workflow_id, "name": "prod", "active": True},
        )
    ).json()
    assert deployment["workflow_version_id"] == published["workflow_version_id"]
    assert deployment["workflow_version"] == 2

    await client.put(f"/workflows/{workflow_id}", json={"graph": _manual_echo_graph("two")})

    manual_run_id = (await client.post(f"/workflows/{workflow_id}/run", json={})).json()["run_id"]
    manual = (await client.get(f"/runs/{manual_run_id}")).json()
    manual_nodes = {n["node_id"]: n for n in manual["node_runs"]}
    assert manual_nodes["code"]["output"]["main"] == "two"
    assert manual["workflow_version_id"] is None

    prod_run_id = (await client.post(f"/deployments/{deployment['id']}/run")).json()["run_id"]
    prod = (await client.get(f"/runs/{prod_run_id}")).json()
    prod_nodes = {n["node_id"]: n for n in prod["node_runs"]}
    assert prod_nodes["code"]["output"]["main"] == "one"
    assert prod["workflow_version_id"] == published["workflow_version_id"]
    assert prod["deployment_id"] == deployment["id"]


async def test_error_workflow_runs_with_failure_payload(client: AsyncClient) -> None:
    handler_id = (await client.post("/workflows", json={"name": "Error handler"})).json()["id"]
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

    workflow_id = (await client.post("/workflows", json={"name": "Fails"})).json()["id"]
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

    run_id = (await client.post(f"/workflows/{workflow_id}/run", json={})).json()["run_id"]
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
                    "When a GitHub issue is opened, summarize it with OpenAI and post to Slack."
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
    workflow_id = (await client.post("/workflows", json={"name": "AI with creds"})).json()["id"]
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

    summarize = next(node for node in response["graph"]["nodes"] if node["id"] == "summarize")
    assert summarize["params"]["api_key"] == ""
    assert "OpenAI API key" in response["missing_credentials"]


async def test_ai_builder_uses_llm_planner_when_available(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "LLM AI"})).json()["id"]

    async def fake_llm_plan(*args, **kwargs):  # noqa: ANN002, ANN003
        return {
            "graph": {
                "nodes": [
                    {
                        "id": "trigger",
                        "type": "manual_trigger",
                        "params": {},
                        "position": {"x": 0, "y": 0},
                    },
                    {
                        "id": "shape",
                        "type": "edit_fields",
                        "params": {"fields": {"message": "from llm"}, "keep_only_set": False},
                        "position": {"x": 280, "y": 0},
                    },
                ],
                "edges": [
                    {
                        "id": "e_trigger_shape",
                        "source": "trigger",
                        "source_output": "main",
                        "target": "shape",
                        "target_input": "input",
                    }
                ],
            },
            "assumptions": ["LLM chose a simple editable shape step."],
            "missing_credentials": [],
            "required_packages": [],
            "explanation": "LLM generated an editable draft.",
            "change_summary": ["Created a manual trigger and data shaping step."],
            "confidence": "high",
            "focus_node_id": "shape",
        }

    monkeypatch.setattr(ai_builder_module, "_call_llm_json", fake_llm_plan)
    monkeypatch.setattr(ai_builder_module, "_llm_configured", lambda: True)

    response = (
        await client.post(
            f"/workflows/{workflow_id}/ai-draft",
            json={"prompt": "Build a small manual data shaping workflow."},
        )
    ).json()

    assert response["planner"] == "llm"
    assert response["confidence"] == "high"
    assert response["focus_node_id"] == "shape"
    assert response["change_summary"] == ["Created a manual trigger and data shaping step."]
    assert [node["type"] for node in response["graph"]["nodes"]] == [
        "manual_trigger",
        "edit_fields",
    ]


async def test_ai_builder_invalid_llm_output_falls_back(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Bad LLM"})).json()["id"]

    async def fake_bad_plan(*args, **kwargs):  # noqa: ANN002, ANN003
        return {
            "graph": {
                "nodes": [
                    {
                        "id": "bad",
                        "type": "not_a_real_node",
                        "params": {},
                        "position": {"x": 0, "y": 0},
                    }
                ],
                "edges": [],
            },
            "explanation": "bad",
        }

    monkeypatch.setattr(ai_builder_module, "_call_llm_json", fake_bad_plan)
    monkeypatch.setattr(ai_builder_module, "_llm_configured", lambda: True)

    response = (
        await client.post(
            f"/workflows/{workflow_id}/ai-draft",
            json={"prompt": "Summarize input with OpenAI."},
        )
    ).json()

    assert response["planner"] == "deterministic_fallback"
    assert any(node["type"] == "openai_chat" for node in response["graph"]["nodes"])
    assert any("LLM planner output was invalid" in item for item in response["assumptions"])


async def test_ai_fix_minimal_preserves_existing_graph(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Fix minimal"})).json()["id"]
    graph = _manual_echo_graph("one")

    async def fake_llm_plan(*args, **kwargs):  # noqa: ANN002, ANN003
        fixed = dict(graph)
        fixed["nodes"] = [dict(node) for node in graph["nodes"]]
        fixed["nodes"][1] = {
            **fixed["nodes"][1],
            "params": {"code": "output = input if input is not None else {}"},
        }
        return {
            "graph": fixed,
            "assumptions": ["Added a guard for missing input."],
            "missing_credentials": [],
            "required_packages": [],
            "explanation": "Minimal repair for the failed code node.",
            "change_summary": ["Updated code node to tolerate missing input."],
            "confidence": "high",
            "focus_node_id": "code",
        }

    monkeypatch.setattr(ai_builder_module, "_call_llm_json", fake_llm_plan)
    monkeypatch.setattr(ai_builder_module, "_llm_configured", lambda: True)

    response = (
        await client.post(
            f"/workflows/{workflow_id}/ai-draft",
            json={
                "prompt": "Fix this failed workflow run.",
                "mode": "fix",
                "fix_strategy": "minimal",
                "current_graph": graph,
                "failed_node_id": "code",
                "error": "NameError: input is not defined",
            },
        )
    ).json()

    assert response["mode"] == "fix"
    assert response["planner"] == "llm"
    assert response["focus_node_id"] == "code"
    assert [node["id"] for node in response["graph"]["nodes"]] == ["trigger", "code"]
    code_node = next(node for node in response["graph"]["nodes"] if node["id"] == "code")
    assert "else {}" in code_node["params"]["code"]


async def test_ai_fix_replacement_can_return_new_valid_graph(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow_id = (await client.post("/workflows", json={"name": "Fix replacement"})).json()["id"]
    graph = _manual_echo_graph("one")

    async def fake_llm_plan(*args, **kwargs):  # noqa: ANN002, ANN003
        return {
            "graph": {
                "nodes": [
                    {
                        "id": "trigger",
                        "type": "manual_trigger",
                        "params": {},
                        "position": {"x": 0, "y": 0},
                    },
                    {
                        "id": "safe_shape",
                        "type": "edit_fields",
                        "params": {"fields": {"message": "{{ $json }}"}, "keep_only_set": False},
                        "position": {"x": 280, "y": 0},
                    },
                ],
                "edges": [
                    {
                        "id": "e_trigger_safe_shape",
                        "source": "trigger",
                        "source_output": "main",
                        "target": "safe_shape",
                        "target_input": "input",
                    }
                ],
            },
            "assumptions": ["Replacement removes fragile custom code."],
            "missing_credentials": [],
            "required_packages": [],
            "explanation": "Replacement draft avoids the failed code path.",
            "change_summary": ["Replaced custom code with Edit Fields."],
            "confidence": "medium",
            "focus_node_id": "safe_shape",
        }

    monkeypatch.setattr(ai_builder_module, "_call_llm_json", fake_llm_plan)
    monkeypatch.setattr(ai_builder_module, "_llm_configured", lambda: True)

    response = (
        await client.post(
            f"/workflows/{workflow_id}/ai-draft",
            json={
                "prompt": "Propose a safer version.",
                "mode": "fix",
                "fix_strategy": "replacement",
                "current_graph": graph,
                "failed_node_id": "code",
                "error": "RuntimeError: bad",
            },
        )
    ).json()

    assert response["mode"] == "fix"
    assert response["confidence"] == "medium"
    assert [node["type"] for node in response["graph"]["nodes"]] == [
        "manual_trigger",
        "edit_fields",
    ]
    assert "Replaced custom code" in response["change_summary"][0]
