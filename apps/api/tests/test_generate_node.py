"""Tests for Slice 3E: AI-Generated Custom Typed Nodes."""

import pytest
from httpx import AsyncClient

# ---- Pure unit tests for ai_builder functions (no DB/HTTP needed) ----

from app.services.ai_builder import (
    _BLOCKED_BUILTINS,
    _BLOCKED_IMPORTS,
    _build_node_gen_system_prompt,
    _generate_fallback_template,
    _parse_and_validate_node_code,
    _strip_markdown_fences,
)


class TestStripMarkdownFences:
    def test_no_fences(self):
        code = "def foo(): pass"
        assert _strip_markdown_fences(code) == code

    def test_basic_fences(self):
        code = "```python\ndef foo(): pass\n```"
        assert _strip_markdown_fences(code) == "def foo(): pass"

    def test_fences_no_lang(self):
        code = "```\ndef foo():\n    return 1\n```"
        assert _strip_markdown_fences(code) == "def foo():\n    return 1"

    def test_already_clean(self):
        code = "def foo():\n    return 1"
        assert _strip_markdown_fences(code) == code

    def test_leading_trailing_whitespace(self):
        code = "  ```python\nx = 1\n```  "
        result = _strip_markdown_fences(code)
        assert result == "x = 1"
        assert result.strip() == result


class TestBuildNodeGenSystemPrompt:
    def test_contains_node_decorator_api(self):
        prompt = _build_node_gen_system_prompt()
        assert "@node(" in prompt
        assert "PortDataKind" in prompt
        assert "from typing import Any" in prompt
        assert "from noodle import node" in prompt

    def test_contains_examples(self):
        prompt = _build_node_gen_system_prompt()
        assert "HTTP GET" in prompt
        assert "Filter Items" in prompt
        assert "noodle" in prompt

    def test_blocked_imports_listed(self):
        prompt = _build_node_gen_system_prompt()
        for mod in ("os", "socket", "subprocess"):
            assert mod in prompt
        assert "eval" in prompt


class TestGenerateFallbackTemplate:
    def test_returns_template(self):
        result = _generate_fallback_template("Send an email via Resend")
        assert result["is_template"] is True
        assert "@node(" in result["code"]
        assert "TODO" in result["code"]
        assert "send_an_email_via_resend" in result["node_id"]
        assert result["node_name"] == "Send An Email Via Resend"
        assert result["input_ports"] == {"main": "any"}
        assert result["output_ports"] == {"main": "any"}
        assert len(result["warnings"]) == 1
        assert "No LLM provider configured" in result["warnings"][0]

    def test_short_description_slug(self):
        result = _generate_fallback_template("Parse CSV")
        assert "custom__parse_csv" in result["node_id"]

    def test_node_id_in_code(self):
        result = _generate_fallback_template("My Custom Action")
        assert "custom__my_custom_action" in result["code"]


class TestParseAndValidateNodeCode:
    VALID_NODE = (
        '@node(\n'
        '    id="my_action",\n'
        '    name="My Action",\n'
        '    category="AI Generated",\n'
        '    description="Do something",\n'
        '    input_kinds={"input": "text"},\n'
        '    output_kinds={"result": "json"},\n'
        ')\n'
        'def my_action(input: str = "", *, ctx: RuntimeContext) -> dict:\n'
        '    """Do something."""\n'
        '    import httpx\n'
        '    resp = httpx.get("https://example.com", timeout=30)\n'
        '    return resp.json()\n'
    )

    def test_valid_node_syntax(self):
        result = _parse_and_validate_node_code(self.VALID_NODE)
        assert result["is_template"] is False
        assert result["node_id"] == "my_action"
        assert result["node_name"] == "My Action"
        assert result["input_ports"] == {"input": "text"}
        assert result["output_ports"] == {"result": "json"}
        assert result["warnings"] == []

    def test_strips_markdown_fences(self):
        fenced = "```python\n" + self.VALID_NODE + "\n```"
        result = _parse_and_validate_node_code(fenced)
        assert result["is_template"] is False
        assert result["node_id"] == "my_action"

    def test_syntax_error_returns_template(self):
        result = _parse_and_validate_node_code("def foo(:")
        assert result["is_template"] is True
        assert any("Syntax error" in w for w in result["warnings"])

    def test_missing_node_decorator_returns_template(self):
        code = "def foo(x: int) -> int:\n    return x + 1"
        result = _parse_and_validate_node_code(code)
        assert result["is_template"] is True
        assert any("missing the @node decorator" in w for w in result["warnings"])

    def test_blocks_os_import(self):
        code = (
            '@node(id="bad", name="Bad", category="AI Generated", description="x",\n'
            '      input_kinds={"main": "any"}, output_kinds={"main": "any"})\n'
            'def bad(input: Any = None, *, ctx: RuntimeContext) -> Any:\n'
            '    import os\n'
            '    return os.getenv("HOME")\n'
        )
        result = _parse_and_validate_node_code(code)
        assert any("Blocked import 'os'" in w for w in result["warnings"])

    def test_blocks_subprocess_import(self):
        code = (
            '@node(id="bad2", name="Bad2", category="AI Generated", description="x",\n'
            '      input_kinds={"main": "any"}, output_kinds={"main": "any"})\n'
            'def bad2(input: Any = None, *, ctx: RuntimeContext) -> Any:\n'
            '    import subprocess\n'
            '    return subprocess.run(["ls"])\n'
        )
        result = _parse_and_validate_node_code(code)
        assert any("Blocked import 'subprocess'" in w for w in result["warnings"])

    def test_blocks_socket_import(self):
        code = (
            '@node(id="bad3", name="Bad3", category="AI Generated", description="x",\n'
            '      input_kinds={"main": "any"}, output_kinds={"main": "any"})\n'
            'def bad3(input: Any = None, *, ctx: RuntimeContext) -> Any:\n'
            '    import socket\n'
            '    return socket.gethostname()\n'
        )
        result = _parse_and_validate_node_code(code)
        assert any("Blocked import 'socket'" in w for w in result["warnings"])

    def test_blocks_dangerous_builtins(self):
        for builtin in ("eval", "exec", "compile", "open", "__import__"):
            code = (
                f'@node(id="bad_{builtin}", name="Bad", category="AI Generated", description="x",\n'
                f'      input_kinds={{"main": "any"}}, output_kinds={{"main": "any"}})\n'
                f'def bad_{builtin}(input: Any = None, *, ctx: RuntimeContext) -> Any:\n'
                f'    return {builtin}("x")\n'
            )
            result = _parse_and_validate_node_code(code)
            assert any(builtin in w for w in result["warnings"]), (
                f"Expected warning for {builtin}()"
            )

    def test_collision_detection(self):
        existing = ["my_action"]
        result = _parse_and_validate_node_code(self.VALID_NODE, existing)
        assert result["node_id"] == "my_action_1"
        assert any("already exists" in w for w in result["warnings"])

    def test_multiple_collisions(self):
        existing = ["my_action", "my_action_1", "my_action_2"]
        result = _parse_and_validate_node_code(self.VALID_NODE, existing)
        assert result["node_id"] == "my_action_3"

    def test_empty_existing_ids_no_collision(self):
        result = _parse_and_validate_node_code(self.VALID_NODE, [])
        assert result["node_id"] == "my_action"

    def test_none_existing_ids_no_collision(self):
        result = _parse_and_validate_node_code(self.VALID_NODE, None)
        assert result["node_id"] == "my_action"


class TestBlockedImports:
    """Verify the blocked-import list covers the required set."""

    def test_all_blocked_imports_present(self):
        required = {"os", "socket", "subprocess", "sys", "shutil", "ctypes", "importlib"}
        assert required.issubset(_BLOCKED_IMPORTS)

    def test_no_dangerous_builtins(self):
        required = {"eval", "exec", "compile", "open", "__import__"}
        assert required.issubset(_BLOCKED_BUILTINS)


# ---- Integration tests against the API ----

@pytest.mark.asyncio
async def test_generate_node_endpoint_returns_valid_response(client: AsyncClient) -> None:
    """POST /code-modules/generate-node returns code with @node decorator."""
    resp = await client.post(
        "/code-modules/generate-node",
        json={
            "description": "Call the Resend API to send an email",
            "scope": "global",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "code" in data
    assert "@node(" in data["code"]
    assert data["node_id"]
    assert data["node_name"]
    assert "input_ports" in data
    assert "output_ports" in data
    # Without LLM configured, should fall back to template
    assert data["is_template"] is True
    assert len(data["warnings"]) >= 1


@pytest.mark.asyncio
async def test_generate_node_environment_scope(client: AsyncClient) -> None:
    """POST /code-modules/generate-node with environment scope."""
    # Create an environment first
    env = (await client.post("/environments", json={"name": "Test Env"})).json()
    resp = await client.post(
        "/code-modules/generate-node",
        json={
            "description": "Filter a list of items",
            "scope": "environment",
            "scope_id": env["id"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "@node(" in data["code"]


@pytest.mark.asyncio
async def test_generate_node_rejects_empty_description(client: AsyncClient) -> None:
    """Empty description should fail validation."""
    resp = await client.post(
        "/code-modules/generate-node",
        json={
            "description": "",
            "scope": "global",
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_save_generated_node(client: AsyncClient) -> None:
    """Generate a fallback node and save it as a code module."""
    # Generate
    gen = (
        await client.post(
            "/code-modules/generate-node",
            json={"description": "Parse JSON data", "scope": "global"},
        )
    ).json()

    # Save
    module = (
        await client.post(
            "/code-modules",
            json={
                "scope": "global",
                "name": "ParseJson.py",
                "contents": gen["code"],
                "metadata": {"ai_generated": True, "description": "Parse JSON data"},
            },
        )
    ).json()
    assert module["scope"] == "global"
    assert module["metadata"].get("ai_generated") is True
    assert "@node(" in module["contents"]


@pytest.mark.asyncio
async def test_generated_node_executes_in_workflow(client: AsyncClient) -> None:
    """Create a generated node, register it, wire into a workflow, and run it."""
    # Create a workflow first, then scope the module to it (workflow scope
    # ensures the runner's module registry finds it at runtime).
    wf = (await client.post("/workflows", json={"name": "GenNodeTest"})).json()
    wf_id = wf["id"]

    # Generate a fallback pass-through node
    gen = (
        await client.post(
            "/code-modules/generate-node",
            json={"description": "Pass data through unchanged", "scope": "global"},
        )
    ).json()

    code = gen["code"]
    # Make the generated code actually do something deterministic
    code = code.replace(
        "output = input",
        "output = {'received': input}",
    )

    # Save as workflow-scoped
    module = (
        await client.post(
            "/code-modules",
            json={
                "scope": "workflow",
                "workflow_id": wf_id,
                "name": "Passthrough.py",
                "contents": code,
                "metadata": {"ai_generated": True},
            },
        )
    ).json()

    # Fetch manifests to find the node type
    manifests = (await client.get(f"/code-modules/manifests/workflow/{wf_id}")).json()
    assert len(manifests) >= 1
    gen_manifest = [m for m in manifests if module["id"] in m["id"]]
    assert len(gen_manifest) >= 1
    node_type = gen_manifest[0]["id"]

    # Wire it: manual_trigger → generated node
    graph = {
        "nodes": [
            {
                "id": "t",
                "type": "manual_trigger",
                "params": {"data": {"hello": "world"}},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "n1",
                "type": node_type,
                "params": {},
                "position": {"x": 200, "y": 0},
            },
        ],
        "edges": [
            {"id": "e0", "source": "t", "source_output": "main", "target": "n1", "target_input": "input"},
        ],
    }
    resp = await client.put(f"/workflows/{wf_id}", json={"graph": graph})
    assert resp.status_code == 200

    # Run — empty body triggers manual_trigger; ``data`` param is injected
    # from the trigger's default config.
    run = (await client.post(f"/workflows/{wf_id}/run", json={})).json()
    run_id = run["run_id"]
    # Wait for the run to finish
    import asyncio
    for _ in range(30):
        info = (await client.get(f"/runs/{run_id}")).json()
        if info["status"] in ("success", "error", "cancelled"):
            break
        await asyncio.sleep(0.1)
    assert info["status"] == "success", f"Run failed: {info}"
    node_runs = info.get("node_runs", [])
    assert len(node_runs) >= 2  # trigger + generated node
    gen_run = next((nr for nr in node_runs if nr["node_id"] == "n1"), None)
    assert gen_run is not None
    assert gen_run["status"] == "success"
    # The node's output is an envelope dict (main → value).
    output = gen_run.get("output", {})
    assert {"received": {"hello": "world"}} in output.values()
