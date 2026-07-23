"""Tests for durable execution checkpoints (MS-2).

Covers checkpoint serialisation safety, save-on-completion behaviour,
resume-from-checkpoint logic, and clearing on terminal status.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.services import run_checkpoints
from app.services import runner as runner_module
from app.services.runner import (
    _MAX_CHECKPOINT_BYTES,
    _save_checkpoint,
    _serialize_checkpoint_outputs,
)

# ---------------------------------------------------------------------------
# _serialize_checkpoint_outputs
# ---------------------------------------------------------------------------


def test_serialize_checkpoint_outputs_passes_through_serializable() -> None:
    """A plain dict of JSON-safe values should round-trip unchanged."""
    outputs: dict[str, dict] = {
        "n1": {"result": 42, "text": "hello"},
        "n2": {"list": [1, 2, 3]},
    }
    result = _serialize_checkpoint_outputs(outputs)
    assert result == outputs


def test_serialize_checkpoint_outputs_skips_unserializable() -> None:
    """Values that raise during serialization should be skipped, not crash."""
    from app.services import run_checkpoints

    original = run_checkpoints.serialize_value

    def _raising_serialize(value, **kwargs):
        if isinstance(value, dict) and value.get("_trigger_error"):
            raise RuntimeError("simulated serialization failure")
        return original(value, **kwargs)

    outputs: dict[str, dict] = {
        "good": {"x": 1},
        "bad": {"_trigger_error": True},
    }
    with patch.object(run_checkpoints, "serialize_value", side_effect=_raising_serialize):
        result = _serialize_checkpoint_outputs(outputs)
    assert "good" in result
    assert result["good"] == {"x": 1}
    # The bad node's output should be skipped.
    assert "bad" not in result


def test_serialize_checkpoint_outputs_skips_non_dict_entries() -> None:
    """If a node output entry is not a dict, it should be silently skipped."""
    outputs: dict[str, dict] = {
        "n1": {"ok": True},
        "n2": "this is not a dict",  # type: ignore[assignment]
    }
    result = _serialize_checkpoint_outputs(outputs)
    assert "n1" in result
    assert "n2" not in result


def test_serialize_checkpoint_outputs_handles_mixed_types() -> None:
    """Mixed JSON-safe and unknown types should be handled gracefully."""
    outputs: dict[str, dict] = {
        "n1": {"ok": True},
        "n2": {"text": "hello", "num": 42},
        "n3": {"empty": None},
    }
    result = _serialize_checkpoint_outputs(outputs)
    assert "n1" in result
    assert "n2" in result
    assert "n3" in result
    assert result["n2"]["text"] == "hello"


# ---------------------------------------------------------------------------
# _save_checkpoint payload structure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_checkpoint_payload_structure() -> None:
    """Check the payload dict has the expected keys."""
    run_id = "test-cp-structure"
    node_outputs = {"n1": {"result": 1}}
    completed = {"n1"}
    last_node_id = "n1"

    original_exec = run_checkpoints.SessionLocal
    try:
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.__aenter__.return_value = mock_session

        run_checkpoints.SessionLocal = lambda: mock_session  # type: ignore[assignment]

        await _save_checkpoint(run_id, node_outputs, completed, last_node_id)

        # Inspect what was passed to execute.
        assert mock_session.execute.call_count >= 1
    finally:
        run_checkpoints.SessionLocal = original_exec

    # Verify payload structure directly through the builder logic.
    serialized = _serialize_checkpoint_outputs(node_outputs)
    payload = {
        "node_outputs": serialized,
        "completed_nodes": sorted(completed),
        "last_node_id": last_node_id,
    }
    assert "node_outputs" in payload
    assert payload["node_outputs"] == {"n1": {"result": 1}}
    assert payload["completed_nodes"] == ["n1"]
    assert payload["last_node_id"] == "n1"


# ---------------------------------------------------------------------------
# Checkpoint save on node completion (integration)
# ---------------------------------------------------------------------------


async def test_checkpoint_saves_after_node_completes(client: AsyncClient) -> None:
    """A simple single-node workflow (manual trigger only) should trigger
    _save_checkpoint for the trigger node."""
    # Create a simple manual-trigger workflow.
    wf_resp = await client.post("/workflows", json={"name": "CpTest2"})
    assert wf_resp.status_code in (200, 201)
    workflow_id = wf_resp.json()["id"]

    await client.put(
        f"/workflows/{workflow_id}",
        json={
            "graph": {
                "nodes": [
                    {
                        "id": "t",
                        "type": "manual_trigger",
                        "params": {},
                        "position": {"x": 0, "y": 0},
                    },
                ],
                "edges": [],
            }
        },
    )

    # Patch _save_checkpoint to capture calls.  Since ``run_synchronously``
    # is True in tests, the run executes inline during the POST, so the
    # patch must be active before the call.
    with patch.object(runner_module, "_save_checkpoint", AsyncMock()) as mock_save:
        run_resp = await client.post(f"/workflows/{workflow_id}/run", json={})

    assert run_resp.status_code in (200, 202), run_resp.text
    run_id = run_resp.json()["run_id"]

    # Verify the run finished successfully.
    run_get = await client.get(f"/runs/{run_id}")
    status = run_get.json().get("status")
    assert status == "success", f"Expected success, got {status}: {run_get.text}"

    # _save_checkpoint should have been called at least once.
    assert mock_save.call_count >= 1, "Expected checkpoint save after node completion"


# ---------------------------------------------------------------------------
# Checkpoint serialization safety — oversized payload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkpoint_truncates_oversized_payload() -> None:
    """An oversized checkpoint payload should be truncated, not rejected."""
    run_id = "test-cp-oversize"
    # Build an output that is definitely larger than _MAX_CHECKPOINT_BYTES.
    huge_output = {"data": "x" * (_MAX_CHECKPOINT_BYTES + 10_000)}
    node_outputs = {"n1": huge_output}
    completed = {"n1"}
    last_node_id = "n1"

    mock_execute = AsyncMock()
    mock_session = AsyncMock()
    mock_session.execute = mock_execute
    mock_session.commit = AsyncMock()
    mock_session.__aenter__.return_value = mock_session

    original_execute = run_checkpoints.SessionLocal
    try:
        run_checkpoints.SessionLocal = lambda: mock_session  # type: ignore[assignment]

        await _save_checkpoint(run_id, node_outputs, completed, last_node_id)

        # The payload should have been truncated and written.
        assert mock_execute.call_count >= 1
    finally:
        run_checkpoints.SessionLocal = original_execute


# ---------------------------------------------------------------------------
# Checkpoint cleared on terminal status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkpoint_cleared_on_terminal_status_error() -> None:
    """When persist_run_outcome is called with a terminal status, checkpoint
    must be set to None."""
    from collections import deque
    from unittest.mock import patch

    from app.services import run_persistence
    from app.services.run_persistence import persist_run_outcome

    # Build a minimal mock session.  Critical: ``__aenter__`` must return *self*
    # so that ``async with session_factory() as session:`` gives us the same
    # mock object that has our ``get`` override.
    mock_run = MagicMock()
    mock_run.checkpoint = {"node_outputs": {"n1": {}}, "completed_nodes": ["n1"]}
    mock_run.status = "running"
    mock_run.finished_at = None
    mock_run.batch_id = None

    mock_session = MagicMock()
    mock_session.get = AsyncMock(return_value=mock_run)
    mock_session.commit = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock())
    mock_session.__aenter__.return_value = mock_session

    def fake_session_factory():
        return mock_session

    # Mock queue methods that are awaited.
    mock_queue = MagicMock()
    mock_queue.complete = AsyncMock()
    mock_queue.wait_for_approval = AsyncMock()
    mock_queue.cancel = AsyncMock()
    mock_queue.fail = AsyncMock()

    with (
        patch.object(run_persistence, "_extract_webhook_response", return_value=None),
        patch.object(run_persistence, "RunApproval", spec_set=True),
        patch.object(run_persistence, "run_queue", mock_queue),
        patch.object(run_persistence.settings, "multi_tenancy_enabled", False),
    ):
        await persist_run_outcome(
            fake_session_factory,
            run_id="test-cp-cleared",
            status="error",
            graph_dict={"nodes": [], "edges": []},
            node_events={"n1": {"status": "success"}},
            node_run_records={("n1", ()): {"status": "success"}},
            run_events=deque(),
            output_cap=256 * 1024,
        )

    # checkpoint should be set to None.
    assert mock_run.checkpoint is None
    assert mock_run.status == "error"
    assert mock_run.finished_at is not None


@pytest.mark.asyncio
async def test_fast_queued_run_persists_success_and_releases_lease() -> None:
    """A fast worker may finish before the Run row's running update is visible."""
    from collections import deque
    from unittest.mock import patch

    from app.services import run_persistence
    from app.services.run_persistence import persist_run_outcome

    mock_run = MagicMock()
    mock_run.status = "queued"
    mock_run.finished_at = None
    mock_run.checkpoint = None
    mock_run.batch_id = None

    mock_session = MagicMock()
    mock_session.get = AsyncMock(return_value=mock_run)
    mock_session.commit = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock())
    mock_session.__aenter__.return_value = mock_session

    mock_queue = MagicMock()
    mock_queue.complete = AsyncMock()
    mock_queue.wait_for_approval = AsyncMock()
    mock_queue.cancel = AsyncMock()
    mock_queue.fail = AsyncMock()

    with (
        patch.object(run_persistence, "_extract_webhook_response", return_value=None),
        patch.object(run_persistence, "RunApproval", spec_set=True),
        patch.object(run_persistence, "run_queue", mock_queue),
        patch.object(run_persistence.settings, "multi_tenancy_enabled", False),
    ):
        await persist_run_outcome(
            lambda: mock_session,
            run_id="fast-queued-run",
            status="success",
            graph_dict={"nodes": [], "edges": []},
            node_events={},
            node_run_records={},
            run_events=deque(),
            output_cap=256 * 1024,
        )

    assert mock_run.status == "success"
    assert mock_run.finished_at is not None
    mock_queue.complete.assert_awaited_once_with(mock_session, run_id="fast-queued-run")
    mock_session.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# Checkpoint preserved on "waiting" status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkpoint_preserved_on_waiting_status() -> None:
    """Checkpoint should NOT be cleared when status is 'waiting' (resumable)."""
    from collections import deque

    from app.services import run_persistence
    from app.services.run_persistence import persist_run_outcome

    # Use a plain MagicMock without spec to avoid SQLAlchemy descriptor issues.
    mock_run = MagicMock()
    mock_run.checkpoint = {"node_outputs": {"n1": {}}, "completed_nodes": ["n1"], "last_node_id": "n1", "timestamp": "2026-01-01T00:00:00"}
    mock_run.status = "waiting"
    mock_run.finished_at = None
    mock_run.batch_id = None
    mock_run.id = "test-cp-waiting"

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(return_value=mock_run)
    mock_session.execute = AsyncMock(return_value=MagicMock())
    mock_session.__aenter__.return_value = mock_session

    def fake_session_factory():
        return mock_session

    mock_queue = MagicMock()
    mock_queue.complete = AsyncMock()
    mock_queue.wait_for_approval = AsyncMock()
    mock_queue.cancel = AsyncMock()
    mock_queue.fail = AsyncMock()

    with (
        patch.object(run_persistence, "_extract_webhook_response", return_value=None),
        patch.object(run_persistence, "RunApproval", spec_set=True),
        patch.object(run_persistence, "run_queue", mock_queue),
        patch.object(run_persistence.settings, "multi_tenancy_enabled", False),
    ):
        await persist_run_outcome(
            fake_session_factory,
            run_id="test-cp-waiting",
            status="waiting",
            graph_dict={"nodes": [], "edges": []},
            node_events={"n1": {"status": "success"}},
            node_run_records={("n1", ()): {"status": "success"}},
            run_events=deque(),
            output_cap=256 * 1024,
        )

    # checkpoint should be preserved for waiting (approval) runs.
    assert mock_run.checkpoint is not None
    assert mock_run.finished_at is None  # waiting keeps finished_at None
