"""Tests for Map Items and Map Dataset nodes."""

from __future__ import annotations

import pytest

import noodle_nodes  # noqa: F401 - registers nodes
from noodle.artifacts import LocalArtifactStore
from noodle.context import artifact_store, current_node_id, workflow_caller


@pytest.fixture
def store_ctx(tmp_path):
    store = LocalArtifactStore(tmp_path, run_id="test-run")
    a = artifact_store.set(store)
    n = current_node_id.set("test-node")
    yield store
    current_node_id.reset(n)
    artifact_store.reset(a)


# ---------------------------------------------------------------------------
# map_items
# ---------------------------------------------------------------------------


async def test_map_items_calls_child_once_per_item(store_ctx) -> None:
    from noodle_nodes.builtin import map_items

    calls: list[dict] = []

    async def caller(wf_id: str, payload: dict) -> dict:
        calls.append(payload)
        return {"doubled": payload["item"]["v"] * 2}

    items = [{"v": 1}, {"v": 2}, {"v": 3}]
    token = workflow_caller.set(caller)
    try:
        result = await map_items(input=items, workflow_id="wf-1")
    finally:
        workflow_caller.reset(token)

    assert len(calls) == 3
    assert result["main"] == [{"doubled": 2}, {"doubled": 4}, {"doubled": 6}]
    assert result["errors"] == []


async def test_map_items_preserves_order(store_ctx) -> None:
    import asyncio

    from noodle_nodes.builtin import map_items

    async def caller(wf_id: str, payload: dict) -> dict:
        await asyncio.sleep(0.01 * (3 - payload["index"]))  # reverse latency order
        return {"i": payload["index"]}

    token = workflow_caller.set(caller)
    try:
        result = await map_items(
            input=[{}, {}, {}], workflow_id="wf-1", preserve_order=True
        )
    finally:
        workflow_caller.reset(token)

    assert [r["i"] for r in result["main"]] == [0, 1, 2]


async def test_map_items_concurrency_does_not_change_output_order(store_ctx) -> None:
    import asyncio

    from noodle_nodes.builtin import map_items

    async def caller(wf_id: str, payload: dict) -> dict:
        await asyncio.sleep(0)
        return {"i": payload["index"]}

    token = workflow_caller.set(caller)
    try:
        result = await map_items(
            input=[{}, {}, {}, {}, {}],
            workflow_id="wf-1",
            concurrency=3,
            preserve_order=True,
        )
    finally:
        workflow_caller.reset(token)

    assert [r["i"] for r in result["main"]] == [0, 1, 2, 3, 4]


async def test_map_items_on_error_fail_raises(store_ctx) -> None:
    from noodle_nodes.builtin import map_items

    async def caller(wf_id: str, payload: dict) -> dict:
        if payload["index"] == 1:
            raise RuntimeError("boom")
        return {"ok": True}

    token = workflow_caller.set(caller)
    try:
        with pytest.raises(RuntimeError, match="item 1"):
            await map_items(input=[{}, {}, {}], workflow_id="wf-1", on_error="fail")
    finally:
        workflow_caller.reset(token)


async def test_map_items_on_error_continue_splits_outputs(store_ctx) -> None:
    from noodle_nodes.builtin import map_items

    async def caller(wf_id: str, payload: dict) -> dict:
        if payload["index"] == 1:
            raise RuntimeError("fail on 1")
        return {"i": payload["index"]}

    token = workflow_caller.set(caller)
    try:
        result = await map_items(
            input=[{}, {}, {}], workflow_id="wf-1", on_error="continue"
        )
    finally:
        workflow_caller.reset(token)

    assert len(result["main"]) == 2
    assert len(result["errors"]) == 1
    assert result["errors"][0]["index"] == 1
    assert "fail on 1" in result["errors"][0]["error"]


async def test_map_items_missing_caller_raises(store_ctx) -> None:
    from noodle_nodes.builtin import map_items

    with pytest.raises(RuntimeError, match="no host caller"):
        await map_items(input=[{}], workflow_id="wf-1")


async def test_map_items_workflow_id_required(store_ctx) -> None:
    # Was using asyncio.get_event_loop().run_until_complete, which raises
    # "no current event loop" on 3.12; the suite runs asyncio_mode=auto so an
    # async test awaits directly (TEST-1).
    from noodle_nodes.builtin import map_items

    with pytest.raises(ValueError, match="workflow_id"):
        await map_items(input=[{}])


async def test_map_items_artifact_ref_passes_through(store_ctx) -> None:
    """Rows containing ArtifactRefs are forwarded to child workflow untouched."""
    from noodle_nodes.builtin import map_items

    artifact_ref = {"__noodle_artifact__": True, "artifact_id": "pdf_1"}
    received: list[dict] = []

    async def caller(wf_id: str, payload: dict) -> dict:
        received.append(payload)
        return {"got_pdf": payload["item"]["pdf"]}

    items = [{"invoice_id": "A", "pdf": artifact_ref}]
    token = workflow_caller.set(caller)
    try:
        result = await map_items(input=items, workflow_id="wf-1")
    finally:
        workflow_caller.reset(token)

    assert received[0]["item"]["pdf"] == artifact_ref
    assert result["main"][0]["got_pdf"] == artifact_ref
