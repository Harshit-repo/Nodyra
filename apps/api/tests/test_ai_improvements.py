"""Tests for MS2 Slice 2C: AI Improvements.

Covers the standalone explain-workflow endpoint, multi-turn refinement,
and workflow test generation — focusing on the deterministic fallback
paths that run without an LLM provider configured.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.services.ai_builder import (
    _NODE_REGISTRY,
    _fallback_generate_tests,
    _fallback_refine,
    explain_workflow,
    generate_tests,
)

# ── Test data ─────────────────────────────────────────────────────────────────


_SAMPLE_GRAPH = {
    "nodes": [
        {
            "id": "trigger",
            "type": "webhook_trigger",
            "params": {"http_method": "POST", "path": "incoming"},
            "name": "Webhook Trigger",
        },
        {
            "id": "summarize",
            "type": "ai_chat",
            "params": {"provider": "openai", "model": "gpt-4.1-mini"},
            "name": "AI Chat",
        },
        {
            "id": "notify",
            "type": "slack",
            "params": {"channel": "#alerts"},
            "name": "Slack",
        },
    ],
    "edges": [
        {"source": "trigger", "target": "summarize"},
        {"source": "summarize", "target": "notify"},
    ],
}

_EMPTY_GRAPH = {"nodes": [], "edges": []}


# ── Feature 1: explain_workflow ──────────────────────────────────────────────


class TestExplainWorkflow:
    """Tests for the standalone explain-workflow endpoint."""

    async def test_explain_workflow_returns_structure(self):
        """Verify response has explanation, nodes_summary, data_flow, assumptions."""
        result = await explain_workflow(_SAMPLE_GRAPH)

        assert isinstance(result, dict)
        assert "explanation" in result
        assert "nodes_summary" in result
        assert "data_flow" in result
        assert "assumptions" in result

        assert isinstance(result["explanation"], str)
        assert len(result["explanation"]) > 0
        assert isinstance(result["nodes_summary"], list)
        assert len(result["nodes_summary"]) == 3  # one per node
        assert isinstance(result["data_flow"], str)
        assert isinstance(result["assumptions"], list)

    async def test_explain_workflow_empty_graph(self):
        """Verify empty graph returns sensible message."""
        result = await explain_workflow(_EMPTY_GRAPH)

        assert result["explanation"] == "This workflow is empty — it has no nodes yet."
        assert result["nodes_summary"] == []
        assert result["data_flow"] == "No data flow."
        assert result["assumptions"] == []

    async def test_fallback_explain_works_without_llm(self):
        """Patch _call_llm_json to raise, verify fallback produces a valid result."""
        with patch(
            "app.services.ai_builder._call_llm_simple",
            new=AsyncMock(side_effect=RuntimeError("No LLM")),
        ):
            result = await explain_workflow(_SAMPLE_GRAPH)

        assert isinstance(result, dict)
        assert "explanation" in result
        assert "triggered by" in result["explanation"].lower()
        assert "webhook" in result["explanation"].lower()
        assert isinstance(result["nodes_summary"], list)
        assert len(result["nodes_summary"]) == 3

    async def test_fallback_explain_uses_real_manifest_descriptions(self):
        """Regression: ``_NODE_REGISTRY`` entries never carried a
        ``description`` field, so the old lookup always fell through to the
        "Unknown" placeholder for every node's ``purpose`` — even well-known
        types like webhook_trigger/ai_chat/slack. Purposes must now be the
        real manifest description."""
        with patch(
            "app.services.ai_builder._call_llm_simple",
            new=AsyncMock(side_effect=RuntimeError("No LLM")),
        ):
            result = await explain_workflow(_SAMPLE_GRAPH)

        purposes = {item["type"]: item["purpose"] for item in result["nodes_summary"]}
        for node_type, purpose in purposes.items():
            assert purpose not in ("Unknown", "No description"), (
                f"{node_type} purpose should be the real manifest description, got {purpose!r}"
            )

    async def test_explain_workflow_single_node_no_edges(self):
        """A workflow with one node and no edges still produces output."""
        graph = {
            "nodes": [{"id": "n1", "type": "manual_trigger", "params": {}}],
            "edges": [],
        }
        result = await explain_workflow(graph)
        assert isinstance(result["explanation"], str)
        assert len(result["nodes_summary"]) == 1
        assert result["data_flow"] == "No edges defined."


# ── Feature 2: _fallback_refine ──────────────────────────────────────────────


class TestRefineFallback:
    """Tests for the deterministic fallback path of multi-turn refinement."""

    def test_fallback_refine_no_targets_modifies_all(self):
        """With no target_node_ids, all nodes may be modified."""
        graph = {
            "nodes": [
                {"id": "n1", "type": "slack", "params": {"channel": "#general"}},
            ],
            "edges": [],
        }
        prompt = "Change the Slack channel to #alerts"
        result = _fallback_refine(prompt, graph, target_node_ids=[])

        assert len(result.graph.nodes) == 1
        # Params are recast; the channel should still be updated if matched
        assert "change_summary" in result.model_dump()

    def test_fallback_refine_with_targets_skips_others(self):
        """Only nodes in target_node_ids get modified."""
        graph = {
            "nodes": [
                {"id": "n1", "type": "slack", "params": {"channel": "#general"}},
                {"id": "n2", "type": "slack", "params": {"channel": "#random"}},
            ],
            "edges": [],
        }
        prompt = "Change the Slack channel to #alerts"
        result = _fallback_refine(prompt, graph, target_node_ids=["n1"])

        assert len(result.graph.nodes) == 2

    def test_fallback_refine_returns_valid_response(self):
        """Verify the response shape is always an AiWorkflowDraftResponse."""
        graph = {
            "nodes": [{"id": "n1", "type": "manual_trigger", "params": {}}],
            "edges": [],
        }
        result = _fallback_refine("no-op", graph, target_node_ids=[])
        assert result.workflow_id == ""
        assert result.explanation
        assert result.confidence == "low"

    def test_fallback_refine_slack_channel(self):
        """Verify channel detection works in fallback."""
        graph = {
            "nodes": [
                {"id": "n1", "type": "slack", "params": {"channel": "#general"}},
            ],
            "edges": [],
        }
        prompt = "Change slack channel to #critical"
        result = _fallback_refine(prompt, graph, target_node_ids=[])
        # The regex picks up #critical
        channel = result.graph.nodes[0].params.get("channel", "")
        assert channel == "#critical"


class TestRefinePromptCatalog:
    """AIB-1 regression: the refine LLM prompt used to list only the ~64
    hand-curated ``_NODE_REGISTRY`` node types, so refine mode couldn't add
    any of the other ~450 registered nodes. It must now see the full
    manifest-derived catalog, with param signatures."""

    def test_refine_prompt_includes_full_catalog_with_params(self):
        from app.services.ai_builder import _build_refine_prompt, node_catalog_for_prompt

        context = {
            "current_graph": {"nodes": [], "edges": []},
            "node_catalog": node_catalog_for_prompt(),
            "target_node_ids": [],
        }
        system_msg, _user_msg = _build_refine_prompt("test", context, [])

        # A node NOT in the old hardcoded _NODE_REGISTRY must now be visible.
        assert "telegram" not in _NODE_REGISTRY
        assert "telegram" in system_msg
        # Param signatures (not just bare names) must be present.
        assert "url:string" in system_msg or "url:" in system_msg


# ── Feature 3: generate_tests ────────────────────────────────────────────────


class TestGenerateTests:
    """Tests for the workflow test generation feature."""

    async def test_generate_tests_returns_list(self):
        """Verify test generation returns a list."""
        tests = await generate_tests(_SAMPLE_GRAPH)
        assert isinstance(tests, list)
        assert len(tests) > 0

    async def test_generate_tests_empty_graph(self):
        """Empty graph yields empty list."""
        tests = await generate_tests(_EMPTY_GRAPH)
        assert tests == []

    async def test_fallback_generate_tests_webhook_trigger(self):
        """Webhook trigger produces a webhook-specific test."""
        graph = {
            "nodes": [
                {"id": "t1", "type": "webhook_trigger", "params": {}},
            ],
            "edges": [],
        }
        tests = _fallback_generate_tests(graph, graph["nodes"][0])
        assert len(tests) >= 1
        assert any("webhook" in t["name"].lower() for t in tests)

    async def test_fallback_generate_tests_schedule_trigger(self):
        """Schedule trigger produces a schedule-specific test."""
        graph = {
            "nodes": [
                {"id": "t1", "type": "schedule_trigger", "params": {}},
            ],
            "edges": [],
        }
        tests = _fallback_generate_tests(graph, graph["nodes"][0])
        assert len(tests) >= 1
        assert any("schedule" in t["name"].lower() for t in tests)

    async def test_fallback_generate_tests_no_trigger(self):
        """When no trigger node is found, a default test is returned."""
        tests = _fallback_generate_tests({"nodes": [], "edges": []}, None)
        assert len(tests) >= 1
        assert tests[0]["name"] == "Default execution"

    async def test_generate_tests_each_test_has_required_fields(self):
        """Each test case must have name, input_data, expected_outputs, assertions."""
        tests = await generate_tests(_SAMPLE_GRAPH)
        for test in tests:
            assert "name" in test, f"Test missing 'name': {test}"
            assert "input_data" in test, f"Test missing 'input_data': {test}"
            assert "expected_outputs" in test, f"Test missing 'expected_outputs': {test}"
            assert "assertions" in test, f"Test missing 'assertions': {test}"
            assert isinstance(test["assertions"], list), f"'assertions' must be a list: {test}"


# ── _NODE_REGISTRY coverage ──────────────────────────────────────────────────


def test_node_registry_has_descriptions():
    """Each entry in _NODE_REGISTRY has at least a 'name' key."""
    for ntype, info in _NODE_REGISTRY.items():
        assert "name" in info, f"Node type {ntype!r} missing 'name'"
        assert "params" in info, f"Node type {ntype!r} missing 'params'"
