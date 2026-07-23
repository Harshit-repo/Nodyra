"""Live expression-preview endpoint — faithful to the runtime evaluator."""

import pytest_asyncio
from httpx import AsyncClient

from app.services import expr_preview


@pytest_asyncio.fixture(autouse=True)
async def _stop_preview_worker_after_test():
    yield
    await expr_preview.shutdown()


async def test_preview_string_concat(client: AsyncClient) -> None:
    resp = await client.post(
        "/expression-preview",
        json={"value": '{{ "Hi " + $json.name }}', "json": {"name": "Ada"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"] == "Hi Ada"
    assert body["error"] is None


async def test_preview_parts_segment_literals_and_expressions(
    client: AsyncClient,
) -> None:
    resp = await client.post(
        "/expression-preview",
        json={"value": "Hi {{ $json.name }}!", "json": {"name": "Ada"}},
    )
    parts = resp.json()["parts"]
    assert parts == [
        {"kind": "text", "value": "Hi "},
        {"kind": "expr", "raw": "{{ $json.name }}", "value": "Ada"},
        {"kind": "text", "value": "!"},
    ]


async def test_preview_parts_marks_errors(client: AsyncClient) -> None:
    resp = await client.post(
        "/expression-preview", json={"value": "before {{ 1/0 }} after"}
    )
    parts = resp.json()["parts"]
    assert parts[0] == {"kind": "text", "value": "before "}
    assert parts[1]["kind"] == "error"
    assert "ZeroDivisionError" in parts[1]["error"]
    assert parts[2] == {"kind": "text", "value": " after"}


async def test_preview_function_call(client: AsyncClient) -> None:
    resp = await client.post(
        "/expression-preview",
        json={"value": "{{ len($json.items) }}", "json": {"items": [1, 2, 3]}},
    )
    assert resp.json()["result"] == 3


async def test_preview_node_reference(client: AsyncClient) -> None:
    resp = await client.post(
        "/expression-preview",
        json={
            "value": '{{ $node["n1"].main.x }}',
            "nodes": {"n1": {"main": {"x": 42}}},
        },
    )
    assert resp.json()["result"] == 42


async def test_preview_whole_object_unwraps(client: AsyncClient) -> None:
    resp = await client.post(
        "/expression-preview",
        json={"value": "{{ $json }}", "json": {"a": 1, "b": [2, 3]}},
    )
    assert resp.json()["result"] == {"a": 1, "b": [2, 3]}


async def test_preview_error_is_reported(client: AsyncClient) -> None:
    resp = await client.post("/expression-preview", json={"value": "{{ 1/0 }}"})
    body = resp.json()
    assert body["result"] is None
    assert body["error"]


async def test_preview_plain_text_passthrough(client: AsyncClient) -> None:
    resp = await client.post(
        "/expression-preview", json={"value": "no expression here"}
    )
    assert resp.json()["result"] == "no expression here"


async def test_preview_rejects_oversized_expression(client: AsyncClient) -> None:
    resp = await client.post("/expression-preview", json={"value": "x" * 20_001})
    assert resp.status_code == 422


async def test_api_responses_include_defensive_headers(client: AsyncClient) -> None:
    resp = await client.get("/health/live")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["cross-origin-opener-policy"] == "same-origin"
    assert resp.headers["cross-origin-resource-policy"] == "same-site"
    assert "camera=()" in resp.headers["permissions-policy"]


async def test_control_character_query_is_rejected_before_database(
    client: AsyncClient,
) -> None:
    resp = await client.get("/artifacts", params={"workflow_id": "\x00"})
    assert resp.status_code == 400
    assert "control characters" in resp.json()["detail"]
