import pytest
from httpx import AsyncClient

from app.services.chat_service import _extract_reply


def test_extract_reply_prefers_answer_then_text_then_output() -> None:
    assert _extract_reply({"answer": "A", "text": "B"}) == "A"
    assert _extract_reply({"text": "B", "output": "C"}) == "B"
    assert _extract_reply({"output": "C"}) == "C"


def test_extract_reply_stringifies_when_no_text_field() -> None:
    assert _extract_reply({"count": 3}) == '{"count": 3}'
    assert _extract_reply("hi") == "hi"
    assert _extract_reply(None) == ""


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
