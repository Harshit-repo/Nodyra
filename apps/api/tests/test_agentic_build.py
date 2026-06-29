"""Tests for the agentic build loop (MS4 Slice 4D).

These tests use the standard ``client`` fixture (which provides a temporary DB,
patches ``SessionLocal`` across all service modules, and runs workers
synchronously).  The AI draft/fix steps are mocked to return deterministic
graphs so tests never call the real LLM.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.schemas import AgenticBuildRequest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SIMPLE_GRAPH = {
    "nodes": [
        {
            "id": "t",
            "type": "manual_trigger",
            "params": {"data": {"text": "hello"}},
            "position": {"x": 0, "y": 0},
        },
        {
            "id": "c",
            "type": "code",
            "params": {"code": "output = {'greeting': f'Hello, {input[\"text\"]}!'}"},
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

FIXED_GRAPH_V2 = {
    "nodes": [
        {
            "id": "t",
            "type": "manual_trigger",
            "params": {"data": {"text": "hello"}},
            "position": {"x": 0, "y": 0},
        },
        {
            "id": "c",
            "type": "code",
            "params": {"code": "output = {'greeting': f'Hello, {input[\"text\"]}!'}"},
            "position": {"x": 250, "y": 0},
        },
        {
            "id": "slack",
            "type": "webhook",
            "params": {"url": "https://hooks.slack.com/..."},
            "position": {"x": 500, "y": 0},
        },
    ],
    "edges": [
        {
            "id": "e1",
            "source": "t",
            "source_output": "main",
            "target": "c",
            "target_input": "input",
        },
        {
            "id": "e2",
            "source": "c",
            "source_output": "main",
            "target": "slack",
            "target_input": "input",
        },
    ],
}


async def _collect_sse_events(response) -> list[dict]:
    """Read all SSE events from an httpx streaming response."""
    events: list[dict] = []
    async for line in response.aiter_lines():
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events


async def _create_workflow(client: AsyncClient) -> str:
    """Helper: create an empty workflow and return its id."""
    wf = (await client.post("/workflows", json={"name": "Agentic Build Test"})).json()
    return wf["id"]


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

async def _mock_ai_draft_one_shot(
    session, workflow_id, prompt_or_body,  # noqa: ARG001
):
    """Mock ``build_workflow_draft`` that always returns a converged graph on
    the first call by also mocking the run outcome."""
    from app.schemas import AiWorkflowDraftResponse
    from noodle.models import WorkflowGraph

    return AiWorkflowDraftResponse(
        workflow_id=workflow_id,
        graph=WorkflowGraph.model_validate(SIMPLE_GRAPH),
        explanation="Generated a simple text → code workflow.",
        mode="draft",
        change_summary=["Created manual_trigger node", "Created code node"],
        confidence="high",
    )


async def _mock_ai_draft_two_iterations(
    session, workflow_id, prompt_or_body,  # noqa: ARG001
):
    """Mock ``build_workflow_draft`` that returns the initial graph (will
    "fail" on first run), then the fixed graph (will "succeed")."""
    from app.schemas import AiWorkflowDraftRequest, AiWorkflowDraftResponse
    from noodle.models import WorkflowGraph

    body = prompt_or_body if isinstance(prompt_or_body, AiWorkflowDraftRequest) else None
    mode = body.mode if body else "draft"

    if mode == "draft":
        return AiWorkflowDraftResponse(
            workflow_id=workflow_id,
            graph=WorkflowGraph.model_validate(SIMPLE_GRAPH),
            explanation="Draft: simple transform.",
            mode="draft",
            confidence="high",
        )
    # refine / fix mode → return improved graph
    return AiWorkflowDraftResponse(
        workflow_id=workflow_id,
        graph=WorkflowGraph.model_validate(FIXED_GRAPH_V2),
        explanation="Added webhook node for Slack integration.",
        mode="refine",
        change_summary=["Added webhook node"],
        confidence="high",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agentic_loop_converges_in_one_iteration(client: AsyncClient) -> None:
    """If the AI draft produces a runnable graph and the test run succeeds, the
    loop should converge immediately (1 iteration) and emit ``converged``."""
    workflow_id = await _create_workflow(client)

    with (
        patch(
            "app.services.agentic_builder.build_workflow_draft",
            _mock_ai_draft_one_shot,
        ),
    ):
        async with client.stream(
            "POST",
            f"/workflows/{workflow_id}/agentic-build",
            json=AgenticBuildRequest(
                goal="Say hello to the user",
                test_data={"text": "world"},
                max_iterations=3,
            ).model_dump(),
        ) as resp:
            assert resp.status_code == 200
            events = await _collect_sse_events(resp)

    event_types = [e["type"] for e in events]
    assert "iteration_start" in event_types
    assert "graph_updated" in event_types
    assert "converged" in event_types

    # Find the converged event
    converged = [e for e in events if e["type"] == "converged"][0]
    assert converged["iterations"] >= 1
    assert "final_graph" in converged


@pytest.mark.asyncio
async def test_agentic_loop_converges_in_two_iterations(client: AsyncClient) -> None:
    """If the first run fails, the loop should fix the graph and converge on
    the second iteration."""
    workflow_id = await _create_workflow(client)

    with (
        patch(
            "app.services.agentic_builder.build_workflow_draft",
            _mock_ai_draft_two_iterations,
        ),
        patch(
            "app.services.agentic_builder._start_test_run",
        ) as mock_start_run,
        patch(
            "app.services.agentic_builder._wait_for_run",
        ) as mock_wait_run,
    ):
        # First run fails, second run succeeds
        mock_wait_run.side_effect = [
            {
                "status": "failed",
                "node_results": {
                    "c": {"status": "error", "error": "NameError: name 'x' is not defined"},
                },
                "error": None,
            },
            {
                "status": "completed",
                "node_results": {
                    "t": {"status": "success", "output": {"main": "hello"}},
                    "c": {"status": "success", "output": {"greeting": "Hello, world!"}},
                    "slack": {"status": "success", "output": {"status": "sent"}},
                },
                "error": None,
            },
        ]
        # start_test_run returns a dummy run_id (doesn't need to be real because _wait_for_run is mocked)
        mock_start_run.side_effect = ["run_a", "run_b"]

        async with client.stream(
            "POST",
            f"/workflows/{workflow_id}/agentic-build",
            json=AgenticBuildRequest(
                goal="Say hello and send to Slack",
                max_iterations=3,
            ).model_dump(),
        ) as resp:
            assert resp.status_code == 200
            events = await _collect_sse_events(resp)

    event_types = [e["type"] for e in events]

    # Two iteration_start events
    starts = [e for e in events if e["type"] == "iteration_start"]
    assert len(starts) == 2
    assert starts[0]["action"] == "draft"
    assert starts[1]["action"] == "fix"

    # run_failed followed by fix_planned then converged
    assert "run_failed" in event_types
    assert "fix_planned" in event_types
    assert "converged" in event_types

    converged = [e for e in events if e["type"] == "converged"][0]
    assert converged["iterations"] == 2


@pytest.mark.asyncio
async def test_agentic_loop_stops_at_max_iterations(client: AsyncClient) -> None:
    """If every run fails, the loop should emit ``max_iterations_reached``
    after the configured max_iterations and NOT emit ``converged``."""
    workflow_id = await _create_workflow(client)

    with (
        patch(
            "app.services.agentic_builder.build_workflow_draft",
            _mock_ai_draft_two_iterations,
        ),
        patch(
            "app.services.agentic_builder._start_test_run",
        ) as mock_start_run,
        patch(
            "app.services.agentic_builder._wait_for_run",
        ) as mock_wait_run,
    ):
        # All runs fail
        mock_wait_run.side_effect = [
            {
                "status": "failed",
                "node_results": {
                    "c": {"status": "error", "error": "TypeError: ..."},
                },
                "error": None,
            },
            {
                "status": "failed",
                "node_results": {
                    "c": {"status": "error", "error": "KeyError: 'missing'"},
                },
                "error": None,
            },
        ]
        mock_start_run.side_effect = ["run_a", "run_b"]

        async with client.stream(
            "POST",
            f"/workflows/{workflow_id}/agentic-build",
            json=AgenticBuildRequest(
                goal="Do something",
                max_iterations=2,
            ).model_dump(),
        ) as resp:
            assert resp.status_code == 200
            events = await _collect_sse_events(resp)

    event_types = [e["type"] for e in events]
    assert "converged" not in event_types
    assert "max_iterations_reached" in event_types

    max_evt = [e for e in events if e["type"] == "max_iterations_reached"][0]
    assert "best_graph" in max_evt
    assert len(max_evt["remaining_errors"]) > 0


@pytest.mark.asyncio
async def test_agentic_loop_requires_valid_workflow(client: AsyncClient) -> None:
    """POST to a non-existent workflow should return 404 before starting the loop."""
    resp = await client.post(
        "/workflows/nonexistent-id/agentic-build",
        json={"goal": "test", "max_iterations": 3},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_agentic_loop_validation_goal_required(client: AsyncClient) -> None:
    """Empty goal should return 422."""
    workflow_id = await _create_workflow(client)

    resp = await client.post(
        f"/workflows/{workflow_id}/agentic-build",
        json={"goal": "", "max_iterations": 3},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_agentic_loop_validation_max_iterations_capped(client: AsyncClient) -> None:
    """max_iterations > 5 should return 422."""
    workflow_id = await _create_workflow(client)

    resp = await client.post(
        f"/workflows/{workflow_id}/agentic-build",
        json={"goal": "test", "max_iterations": 10},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_agentic_loop_emits_error_on_draft_failure(client: AsyncClient) -> None:
    """If the AI draft step raises an exception, an ``error`` event should be
    emitted and the loop should stop."""
    workflow_id = await _create_workflow(client)

    async def _broken_draft(session, workflow_id, prompt_or_body):  # noqa: ARG001
        msg = "LLM call failed: rate limit exceeded"
        raise RuntimeError(msg)

    with (
        patch(
            "app.services.agentic_builder.build_workflow_draft",
            _broken_draft,
        ),
    ):
        async with client.stream(
            "POST",
            f"/workflows/{workflow_id}/agentic-build",
            json=AgenticBuildRequest(
                goal="Say hello",
                max_iterations=3,
            ).model_dump(),
        ) as resp:
            assert resp.status_code == 200
            events = await _collect_sse_events(resp)

    error_evt = [e for e in events if e["type"] == "error"]
    assert len(error_evt) >= 1
    assert "rate limit exceeded" in error_evt[0]["message"]
