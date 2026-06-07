import pytest
from httpx import AsyncClient

from app.services.chat_service import _extract_reply, _find_chat_trigger


def test_extract_reply_prefers_answer_then_text_then_output() -> None:
    assert _extract_reply({"answer": "A", "text": "B"}) == "A"
    assert _extract_reply({"text": "B", "output": "C"}) == "B"
    assert _extract_reply({"output": "C"}) == "C"


def test_extract_reply_stringifies_when_no_text_field() -> None:
    assert _extract_reply({"count": 3}) == '{"count": 3}'
    assert _extract_reply("hi") == "hi"
    assert _extract_reply(None) == ""


def test_find_chat_trigger_ignores_node_without_id() -> None:
    assert _find_chat_trigger({"nodes": [{"type": "chat_trigger"}]}) is None
    assert _find_chat_trigger(
        {"nodes": [{"type": "chat_trigger", "id": "c1"}]}
    ) == "c1"


def _chat_echo_graph() -> dict:
    # Chat Trigger → code node that echoes the chat input as {"answer": ...}
    return {
        "nodes": [
            {"id": "chat", "type": "chat_trigger", "params": {},
             "position": {"x": 0, "y": 0}},
            {"id": "echo", "type": "code",
             "params": {"code": "output = {'answer': input['chatInput']}"},
             "position": {"x": 250, "y": 0}},
        ],
        "edges": [
            {"id": "e1", "source": "chat", "source_output": "main",
             "target": "echo", "target_input": "input"},
        ],
    }


@pytest.mark.asyncio
async def test_run_chat_turn_returns_last_node_text(client: AsyncClient) -> None:
    from app.services.chat_service import run_chat_turn

    workflow_id = (
        await client.post("/workflows", json={"name": "Chatty"})
    ).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": _chat_echo_graph()}
    )

    result = await run_chat_turn(workflow_id, "ping", "sess-1", prefer_draft=True)

    assert result.status == "success"
    assert result.reply == "ping"
    assert result.session_id == "sess-1"
    assert result.run_id


@pytest.mark.asyncio
async def test_run_chat_turn_without_chat_trigger_raises(client: AsyncClient) -> None:
    from app.services.chat_service import NoChatTriggerError, run_chat_turn

    workflow_id = (
        await client.post("/workflows", json={"name": "NoChat"})
    ).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}",
        json={"graph": {"nodes": [
            {"id": "m", "type": "manual_trigger", "params": {},
             "position": {"x": 0, "y": 0}}
        ], "edges": []}},
    )

    with pytest.raises(NoChatTriggerError):
        await run_chat_turn(workflow_id, "ping", "s", prefer_draft=True)


@pytest.mark.asyncio
async def test_chat_endpoint_returns_reply(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "EP"})
    ).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": _chat_echo_graph()}
    )

    resp = await client.post(
        f"/workflows/{workflow_id}/chat",
        json={"message": "hello", "session_id": "s-9"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "hello"
    assert body["session_id"] == "s-9"
    assert body["status"] == "success"
    assert body["run_id"]


@pytest.mark.asyncio
async def test_chat_endpoint_422_without_chat_trigger(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "EP2"})
    ).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}",
        json={"graph": {"nodes": [
            {"id": "m", "type": "manual_trigger", "params": {},
             "position": {"x": 0, "y": 0}}
        ], "edges": []}},
    )

    resp = await client.post(
        f"/workflows/{workflow_id}/chat",
        json={"message": "x", "session_id": "s"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_chat_endpoint_404_for_missing_workflow(client: AsyncClient) -> None:
    resp = await client.post(
        "/workflows/does-not-exist/chat",
        json={"message": "x", "session_id": "s"},
    )
    assert resp.status_code == 404


def _chat_error_graph() -> dict:
    # Chat Trigger -> code node that always raises -> run ends in error.
    return {
        "nodes": [
            {"id": "chat", "type": "chat_trigger", "params": {},
             "position": {"x": 0, "y": 0}},
            {"id": "boom", "type": "code",
             "params": {"code": "raise ValueError('boom')"},
             "position": {"x": 250, "y": 0}},
        ],
        "edges": [
            {"id": "e1", "source": "chat", "source_output": "main",
             "target": "boom", "target_input": "input"},
        ],
    }


@pytest.mark.asyncio
async def test_run_chat_turn_failed_run_returns_error_status(client: AsyncClient) -> None:
    from app.services.chat_service import run_chat_turn

    workflow_id = (
        await client.post("/workflows", json={"name": "Boom"})
    ).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": _chat_error_graph()}
    )

    result = await run_chat_turn(workflow_id, "hi", "s-err", prefer_draft=True)

    assert result.status == "error"
    assert result.run_id  # the run exists and is observable
    assert result.session_id == "s-err"


@pytest.mark.asyncio
async def test_chat_endpoint_error_run_returns_error_status(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "Boom2"})
    ).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": _chat_error_graph()}
    )

    resp = await client.post(
        f"/workflows/{workflow_id}/chat",
        json={"message": "hi", "session_id": "s"},
    )
    # The HTTP call succeeds (200); the body reports the run failed.
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"


@pytest.mark.asyncio
async def test_run_chat_turn_threads_session_across_turns(client: AsyncClient) -> None:
    # Lightweight session-propagation check: the chat_trigger's sessionId reaches
    # the downstream node on every turn for the same session id. (Full agent
    # memory persistence with a model stub is a separate, heavier test.)
    from app.services.chat_service import run_chat_turn

    graph = {
        "nodes": [
            {"id": "chat", "type": "chat_trigger", "params": {},
             "position": {"x": 0, "y": 0}},
            {"id": "echo", "type": "code",
             "params": {"code": "output = {'answer': input['sessionId']}"},
             "position": {"x": 250, "y": 0}},
        ],
        "edges": [
            {"id": "e1", "source": "chat", "source_output": "main",
             "target": "echo", "target_input": "input"},
        ],
    }
    workflow_id = (
        await client.post("/workflows", json={"name": "Threaded"})
    ).json()["id"]
    await client.put(f"/workflows/{workflow_id}", json={"graph": graph})

    first = await run_chat_turn(workflow_id, "one", "sess-keep", prefer_draft=True)
    second = await run_chat_turn(workflow_id, "two", "sess-keep", prefer_draft=True)

    assert first.reply == "sess-keep"
    assert second.reply == "sess-keep"


@pytest.mark.asyncio
async def test_start_chat_turn_returns_run_id_without_awaiting(
    client: AsyncClient,
) -> None:
    from app.services.chat_service import start_chat_turn

    workflow_id = (
        await client.post("/workflows", json={"name": "StreamCore"})
    ).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": _chat_echo_graph()}
    )

    run_id, session_id = await start_chat_turn(
        workflow_id, "ping", "sess-s", prefer_draft=True
    )

    assert run_id
    assert session_id == "sess-s"


@pytest.mark.asyncio
async def test_start_chat_turn_without_chat_trigger_raises(
    client: AsyncClient,
) -> None:
    from app.services.chat_service import NoChatTriggerError, start_chat_turn

    workflow_id = (
        await client.post("/workflows", json={"name": "StreamNoChat"})
    ).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}",
        json={"graph": {"nodes": [
            {"id": "m", "type": "manual_trigger", "params": {},
             "position": {"x": 0, "y": 0}}
        ], "edges": []}},
    )

    with pytest.raises(NoChatTriggerError):
        await start_chat_turn(workflow_id, "ping", "s", prefer_draft=True)


@pytest.mark.asyncio
async def test_chat_stream_endpoint_returns_run_id(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "StreamEP"})
    ).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": _chat_echo_graph()}
    )

    resp = await client.post(
        f"/workflows/{workflow_id}/chat/stream",
        json={"message": "hello", "session_id": "s-stream"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"]
    assert body["session_id"] == "s-stream"


@pytest.mark.asyncio
async def test_chat_stream_then_result_returns_reply(client: AsyncClient) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "StreamResult"})
    ).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}", json={"graph": _chat_echo_graph()}
    )

    start = await client.post(
        f"/workflows/{workflow_id}/chat/stream",
        json={"message": "hello", "session_id": "s-r"},
    )
    run_id = start.json()["run_id"]

    resp = await client.get(
        f"/workflows/{workflow_id}/chat/result/{run_id}",
        params={"session_id": "s-r"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "hello"
    assert body["session_id"] == "s-r"
    assert body["status"] == "success"
    assert body["run_id"] == run_id


@pytest.mark.asyncio
async def test_chat_stream_endpoint_422_without_chat_trigger(
    client: AsyncClient,
) -> None:
    workflow_id = (
        await client.post("/workflows", json={"name": "StreamEP422"})
    ).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}",
        json={"graph": {"nodes": [
            {"id": "m", "type": "manual_trigger", "params": {},
             "position": {"x": 0, "y": 0}}
        ], "edges": []}},
    )

    resp = await client.post(
        f"/workflows/{workflow_id}/chat/stream",
        json={"message": "x", "session_id": "s"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_chat_stream_endpoint_404_for_missing_workflow(
    client: AsyncClient,
) -> None:
    resp = await client.post(
        "/workflows/does-not-exist/chat/stream",
        json={"message": "x", "session_id": "s"},
    )
    assert resp.status_code == 404

