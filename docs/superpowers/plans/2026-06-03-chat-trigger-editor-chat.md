# Chat Trigger + In-Editor Chat — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Chat Trigger node and a right-docked in-editor chat panel so users can hold a multi-turn conversation with an AI Agent workflow, watching nodes execute live.

**Architecture:** A new `chat_trigger` node is the workflow entry point. A `chat_service.run_chat_turn()` seeds that trigger's output with `{chatInput, sessionId}`, starts a run of its branch (reusing `start_run` + the `_await_run_terminal`/`_last_node_output` helpers), and extracts the last node's text as the reply. A `POST /workflows/{id}/chat` endpoint exposes it; a `ChatPanel` React component drives the UI and reuses the editor's existing `connectRunStream` to animate the canvas.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy (async), pytest; React 18 + TypeScript + Vite + vitest; `uv` workspace.

**Spec:** `docs/superpowers/specs/2026-06-03-chat-trigger-and-editor-chat-design.md`

---

## File Structure

| File | Responsibility | New/Modify |
|---|---|---|
| `packages/nodes/noodle_nodes/ai_v2/agents.py` | Recognize `chatInput`/`sessionId` keys | Modify |
| `packages/nodes/tests/test_ai_v2_nodes.py` | Unit-test the key recognition | Modify |
| `packages/nodes/noodle_nodes/builtin.py` | `chat_trigger` node | Modify |
| `packages/nodes/tests/test_builtin_nodes.py` | Registration + output test | Modify |
| `apps/api/app/services/chat_service.py` | `run_chat_turn`, `_extract_reply`, errors | Create |
| `apps/api/tests/test_chat.py` | Service + endpoint tests | Create |
| `apps/api/app/schemas.py` | `ChatTurnRequest` / `ChatTurnResponse` | Modify |
| `apps/api/app/routers/chat.py` | `POST /workflows/{id}/chat` | Create |
| `apps/api/app/main.py` | Register the router | Modify |
| `apps/web/src/types.ts` | `ChatTurnResponse` type | Modify |
| `apps/web/src/api.ts` | `sendChatMessage()` | Modify |
| `apps/web/src/editor/ChatPanel.tsx` | The chat panel UI | Create |
| `apps/web/src/editor/ChatPanel.test.tsx` | Panel vitest | Create |
| `apps/web/src/EditorPage.tsx` | Chat button + render panel | Modify |
| `apps/web/src/editor.css` | Panel styles | Modify |

Commands assume repo root `D:\noodle`. Backend tests run via `uv run pytest …`; frontend via `npm --prefix apps/web run …`.

---

## Task 1: AI Agent recognizes chat keys

The Chat Trigger emits `{chatInput, sessionId}`. The agent already reads several keys for the task text and `input.session_id` for memory; add the camelCase chat keys so a Chat Trigger wired straight into the agent works with no mapping node.

**Files:**
- Modify: `packages/nodes/noodle_nodes/ai_v2/agents.py` (`_task_text` ~line 39, `_session_id` ~line 50)
- Test: `packages/nodes/tests/test_ai_v2_nodes.py`

- [ ] **Step 1: Write the failing test**

Add to `packages/nodes/tests/test_ai_v2_nodes.py`:

```python
from noodle_nodes.ai_v2.agents import _session_id, _task_text


def test_task_text_reads_chat_input_key() -> None:
    assert _task_text({"chatInput": "hello there"}, "") == "hello there"


def test_session_id_reads_camelcase_session_key() -> None:
    assert _session_id({"sessionId": "abc-123"}, "") == "abc-123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/nodes/tests/test_ai_v2_nodes.py::test_task_text_reads_chat_input_key packages/nodes/tests/test_ai_v2_nodes.py::test_session_id_reads_camelcase_session_key -q`
Expected: FAIL — `_task_text` returns `""` (chatInput not in key list); `_session_id` returns `"default"`.

- [ ] **Step 3: Implement the change**

In `_task_text`, add `"chatInput"` to the key tuple:

```python
    if isinstance(input_value, dict):
        for key in ("task", "prompt", "chatInput", "text", "input"):
            value = input_value.get(key)
            if value:
                return _as_text(value).strip()
```

In `_session_id`, accept the camelCase key too:

```python
def _session_id(input_value: Any, configured: str) -> str:
    if configured.strip():
        return configured.strip()
    if isinstance(input_value, dict):
        for key in ("session_id", "sessionId"):
            if input_value.get(key):
                return str(input_value[key])
    return "default"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/nodes/tests/test_ai_v2_nodes.py -q`
Expected: PASS (all existing tests still green).

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/ai_v2/agents.py packages/nodes/tests/test_ai_v2_nodes.py
git commit -m "feat(ai): AI Agent reads chatInput/sessionId from Chat Trigger"
```

---

## Task 2: `chat_trigger` node

**Files:**
- Modify: `packages/nodes/noodle_nodes/builtin.py` (add near `webhook_trigger`, after `error_trigger` ~line 258)
- Test: `packages/nodes/tests/test_builtin_nodes.py`

- [ ] **Step 1: Write the failing test**

Add to `packages/nodes/tests/test_builtin_nodes.py`:

```python
def test_chat_trigger_is_registered_as_trigger() -> None:
    manifests = {m.id: m for m in registry.manifests()}
    chat = manifests["chat_trigger"]
    assert chat.category == "Triggers"
    assert chat.role == "trigger"
    assert chat.inputs == []
    assert chat.outputs == ["main"]


def test_chat_trigger_returns_chat_payload_shape() -> None:
    from noodle_nodes.builtin import chat_trigger

    assert chat_trigger() == {"chatInput": "", "sessionId": ""}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest packages/nodes/tests/test_builtin_nodes.py::test_chat_trigger_is_registered_as_trigger packages/nodes/tests/test_builtin_nodes.py::test_chat_trigger_returns_chat_payload_shape -q`
Expected: FAIL — `KeyError: 'chat_trigger'` / `ImportError`.

- [ ] **Step 3: Implement the node**

Add to `packages/nodes/noodle_nodes/builtin.py` (after the `error_trigger` definition):

```python
@node(name="Chat Trigger", id="chat_trigger", category="Triggers", icon="chat",
      role="trigger", inputs=[], outputs=["main"],
      param_groups={"Options": ["initial_message", "input_placeholder", "title"]},
      params={
          "initial_message": {
              "widget": "textarea", "group": "Options",
              "description": "Assistant greeting shown when the chat panel opens.",
          },
          "input_placeholder": {
              "group": "Options", "placeholder": "Type a message…",
              "description": "Placeholder text for the chat message box.",
          },
          "title": {
              "group": "Options", "placeholder": "Chat",
              "description": "Header label for the chat panel.",
          },
      })
def chat_trigger(initial_message: str = "", input_placeholder: str = "",
                 title: str = "") -> dict:
    """Conversational entry point. When a chat turn runs, the chat service seeds
    this node's output with the user's message and session id; on a plain manual
    run it returns the empty shape so the graph stays runnable."""
    return {"chatInput": "", "sessionId": ""}
```

Note: the `@node` signature mirrors `manual_trigger`/`error_trigger` in the same file (check those for the exact decorator argument style if anything differs).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest packages/nodes/tests/test_builtin_nodes.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/nodes/noodle_nodes/builtin.py packages/nodes/tests/test_builtin_nodes.py
git commit -m "feat(nodes): add Chat Trigger node"
```

---

## Task 3: `chat_service.run_chat_turn` + `_extract_reply`

**Files:**
- Create: `apps/api/app/services/chat_service.py`
- Create (start it here): `apps/api/tests/test_chat.py`

- [ ] **Step 1: Write the failing unit test for `_extract_reply`**

Create `apps/api/tests/test_chat.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && uv run pytest tests/test_chat.py -q`
Expected: FAIL — `ModuleNotFoundError: app.services.chat_service`.

- [ ] **Step 3: Implement `chat_service.py`**

Create `apps/api/app/services/chat_service.py`:

```python
"""Run one conversational turn against a workflow's Chat Trigger.

Surface-agnostic core reused by the editor endpoint (Phase 1) and, later, the
hosted chat page and embeddable widget. Mirrors how ``triggers.dispatch_webhook``
seeds a trigger node's output and starts a run, then reuses the synchronous
"Last Node" wait/extract helpers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.models import Workflow
from app.services.runner import start_run
from app.services.triggers import _await_run_terminal, _last_node_output


class NoChatTriggerError(Exception):
    """Workflow is missing (or has no) chat_trigger node."""


@dataclass
class ChatTurnResult:
    run_id: str | None
    reply: str
    session_id: str
    status: str  # "success" | "error" | "timeout"


def _extract_reply(value: object) -> str:
    if isinstance(value, dict):
        for key in ("answer", "text", "output"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(value)
    if value is None:
        return ""
    return str(value)


def _find_chat_trigger(graph: dict) -> str | None:
    for node in graph.get("nodes", []):
        if node.get("type") == "chat_trigger":
            return str(node.get("id"))
    return None


async def run_chat_turn(
    workflow_id: str,
    message: str,
    session_id: str,
    *,
    prefer_draft: bool = True,
    timeout: float = 120.0,
) -> ChatTurnResult:
    async with SessionLocal() as session:
        workflow = (
            await session.scalars(
                select(Workflow)
                .options(selectinload(Workflow.versions))
                .where(Workflow.id == workflow_id)
            )
        ).first()
        if workflow is None:
            raise NoChatTriggerError("workflow not found")
        graph: dict | None = None
        version_number = 1
        version_id: str | None = None
        if prefer_draft and getattr(workflow, "draft_graph", None):
            graph = workflow.draft_graph
            if workflow.versions:
                version_number = workflow.versions[-1].version
                version_id = workflow.versions[-1].id
        elif workflow.versions:
            latest = workflow.versions[-1]
            graph = latest.graph or {}
            version_number = latest.version
            version_id = latest.id
        if not graph:
            raise NoChatTriggerError("workflow has no graph")
        trigger_id = _find_chat_trigger(graph)
        if trigger_id is None:
            raise NoChatTriggerError("workflow has no Chat Trigger node")

    payload = {"chatInput": message, "sessionId": session_id}
    run_id = await start_run(
        workflow_id,
        graph,
        version_number,
        workflow_version_id=version_id,
        mode="test" if prefer_draft else "production",
        trigger_type="chat",
        cache={trigger_id: {"main": payload}},
        trigger_node_id=trigger_id,
    )

    status = await _await_run_terminal(run_id, timeout)
    if status is None:
        return ChatTurnResult(
            run_id, "The workflow did not respond in time.", session_id, "timeout"
        )
    if status != "success":
        return ChatTurnResult(
            run_id,
            "The workflow run failed. Open the run to see what happened.",
            session_id,
            "error",
        )
    async with SessionLocal() as session:
        body = await _last_node_output(session, run_id)
    return ChatTurnResult(run_id, _extract_reply(body), session_id, "success")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && uv run pytest tests/test_chat.py -q`
Expected: PASS (the two `_extract_reply` tests).

- [ ] **Step 5: Add the integration test for `run_chat_turn`**

Append to `apps/api/tests/test_chat.py`:

```python
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
```

Note: the `client` fixture sets `settings.run_synchronously = True`, so the run finishes before `_await_run_terminal` returns. `PUT /workflows/{id}` saves the draft graph (it does not publish), which is what `prefer_draft=True` reads.

- [ ] **Step 6: Run the integration tests**

Run: `cd apps/api && uv run pytest tests/test_chat.py -q`
Expected: PASS (4 tests).

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/services/chat_service.py apps/api/tests/test_chat.py
git commit -m "feat(api): chat_service.run_chat_turn seeds Chat Trigger and extracts reply"
```

---

## Task 4: Chat endpoint

**Files:**
- Modify: `apps/api/app/schemas.py` (append near the end)
- Create: `apps/api/app/routers/chat.py`
- Modify: `apps/api/app/main.py` (router import block ~line 16, include block ~line 303)
- Test: `apps/api/tests/test_chat.py`

- [ ] **Step 1: Write the failing endpoint test**

Append to `apps/api/tests/test_chat.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && uv run pytest tests/test_chat.py::test_chat_endpoint_returns_reply -q`
Expected: FAIL — 404 (route not registered).

- [ ] **Step 3: Add schemas**

Append to `apps/api/app/schemas.py`:

```python
class ChatTurnRequest(BaseModel):
    message: str
    session_id: str


class ChatTurnResponse(BaseModel):
    run_id: str | None
    reply: str
    session_id: str
    status: str
```

- [ ] **Step 4: Create the router**

Create `apps/api/app/routers/chat.py`:

```python
from fastapi import APIRouter, Depends, HTTPException

from app.schemas import ChatTurnRequest, ChatTurnResponse
from app.security import require_permission
from app.services.chat_service import NoChatTriggerError, run_chat_turn

router = APIRouter(tags=["chat"])


@router.post(
    "/workflows/{workflow_id}/chat",
    response_model=ChatTurnResponse,
    dependencies=[Depends(require_permission("workflow:run"))],
)
async def chat_turn(workflow_id: str, body: ChatTurnRequest) -> ChatTurnResponse:
    try:
        result = await run_chat_turn(
            workflow_id, body.message, body.session_id, prefer_draft=True
        )
    except NoChatTriggerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ChatTurnResponse(
        run_id=result.run_id,
        reply=result.reply,
        session_id=result.session_id,
        status=result.status,
    )
```

- [ ] **Step 5: Register the router in `main.py`**

Add `chat,` to the alphabetized import block at `app/main.py:16`:

```python
from app.routers import (
    artifacts,
    audit,
    auth,
    chat,
    code_modules,
    ...
)
```

Add the include alongside the other workflow routes (after `app.include_router(runs.router)` ~line 303):

```python
app.include_router(chat.router)
```

- [ ] **Step 6: Run the endpoint tests**

Run: `cd apps/api && uv run pytest tests/test_chat.py -q`
Expected: PASS (6 tests total).

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/schemas.py apps/api/app/routers/chat.py apps/api/app/main.py apps/api/tests/test_chat.py
git commit -m "feat(api): POST /workflows/{id}/chat endpoint"
```

---

## Task 5: Frontend API client

**Files:**
- Modify: `apps/web/src/types.ts` (append)
- Modify: `apps/web/src/api.ts` (add to the `api` object near `runWorkflow`)

- [ ] **Step 1: Add the response type**

Append to `apps/web/src/types.ts`:

```typescript
export interface ChatTurnResponse {
  run_id: string | null;
  reply: string;
  session_id: string;
  status: "success" | "error" | "timeout";
}
```

- [ ] **Step 2: Add the API method**

In `apps/web/src/api.ts`, import the type (add `ChatTurnResponse` to the existing `import type { … } from "./types";` block), then add this method to the exported `api` object (next to `runWorkflow`):

```typescript
  sendChatMessage: (
    workflowId: string,
    message: string,
    sessionId: string,
  ) =>
    request<ChatTurnResponse>(`/workflows/${workflowId}/chat`, {
      method: "POST",
      body: JSON.stringify({ message, session_id: sessionId }),
    }),
```

- [ ] **Step 3: Typecheck**

Run: `npm --prefix apps/web run typecheck`
Expected: PASS (no type errors).

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/types.ts apps/web/src/api.ts
git commit -m "feat(web): sendChatMessage API client"
```

---

## Task 6: ChatPanel component

**Files:**
- Create: `apps/web/src/editor/ChatPanel.tsx`
- Create: `apps/web/src/editor/ChatPanel.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/editor/ChatPanel.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChatPanel } from "./ChatPanel";
import { api } from "../api";

describe("ChatPanel", () => {
  it("sends a message and renders user + bot bubbles", async () => {
    vi.spyOn(api, "sendChatMessage").mockResolvedValue({
      run_id: "r1",
      reply: "Hi back",
      session_id: "s1",
      status: "success",
    });

    render(
      <ChatPanel
        workflowId="wf1"
        title="Chat"
        placeholder="Type…"
        initialMessage=""
        onRun={() => {}}
        onClose={() => {}}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("Type…"), {
      target: { value: "hello" },
    });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));

    expect(await screen.findByText("hello")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("Hi back")).toBeTruthy());
  });

  it("reset starts a new session id", async () => {
    const sent: string[] = [];
    vi.spyOn(api, "sendChatMessage").mockImplementation(
      async (_wf, _msg, sessionId) => {
        sent.push(sessionId);
        return { run_id: "r", reply: "ok", session_id: sessionId, status: "success" };
      },
    );

    render(
      <ChatPanel
        workflowId="wf1"
        title="Chat"
        placeholder="Type…"
        initialMessage=""
        onRun={() => {}}
        onClose={() => {}}
      />,
    );

    const input = screen.getByPlaceholderText("Type…");
    fireEvent.change(input, { target: { value: "a" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await screen.findByText("ok");

    fireEvent.click(screen.getByRole("button", { name: /reset/i }));

    fireEvent.change(input, { target: { value: "b" } });
    fireEvent.click(screen.getByRole("button", { name: /send/i }));
    await waitFor(() => expect(sent.length).toBe(2));

    expect(sent[0]).not.toBe(sent[1]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix apps/web test -- ChatPanel`
Expected: FAIL — cannot resolve `./ChatPanel`.

- [ ] **Step 3: Implement the component**

Create `apps/web/src/editor/ChatPanel.tsx`:

```tsx
import { useState } from "react";

import { api } from "../api";

interface ChatMessage {
  role: "user" | "bot";
  text: string;
  runId?: string | null;
  error?: boolean;
}

interface ChatPanelProps {
  workflowId: string;
  title: string;
  placeholder: string;
  initialMessage: string;
  onRun: (runId: string) => void;
  onClose: () => void;
}

function newSessionId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `sess-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function ChatPanel({
  workflowId,
  title,
  placeholder,
  initialMessage,
  onRun,
  onClose,
}: ChatPanelProps) {
  const [sessionId, setSessionId] = useState<string>(newSessionId);
  const [messages, setMessages] = useState<ChatMessage[]>(
    initialMessage ? [{ role: "bot", text: initialMessage }] : [],
  );
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);

  async function send(): Promise<void> {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text }]);
    setSending(true);
    try {
      const res = await api.sendChatMessage(workflowId, text, sessionId);
      if (res.run_id) onRun(res.run_id);
      setMessages((m) => [
        ...m,
        {
          role: "bot",
          text: res.reply,
          runId: res.run_id,
          error: res.status !== "success",
        },
      ]);
    } catch {
      setMessages((m) => [
        ...m,
        { role: "bot", text: "Could not reach the workflow.", error: true },
      ]);
    } finally {
      setSending(false);
    }
  }

  function reset(): void {
    setSessionId(newSessionId());
    setMessages(initialMessage ? [{ role: "bot", text: initialMessage }] : []);
  }

  return (
    <div className="chat-panel">
      <div className="chat-panel-header">
        <span>{title || "Chat"}</span>
        <div className="chat-panel-actions">
          <button type="button" onClick={reset} aria-label="Reset chat">
            Reset
          </button>
          <button type="button" onClick={onClose} aria-label="Close chat">
            ✕
          </button>
        </div>
      </div>
      <div className="chat-panel-messages">
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`chat-bubble chat-bubble-${msg.role}${
              msg.error ? " chat-bubble-error" : ""
            }`}
          >
            {msg.text}
          </div>
        ))}
        {sending ? (
          <div className="chat-bubble chat-bubble-bot chat-typing">…</div>
        ) : null}
      </div>
      <div className="chat-panel-input">
        <input
          value={input}
          placeholder={placeholder || "Type a message…"}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") send();
          }}
        />
        <button type="button" onClick={send} disabled={sending} aria-label="Send">
          Send
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm --prefix apps/web test -- ChatPanel`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/editor/ChatPanel.tsx apps/web/src/editor/ChatPanel.test.tsx
git commit -m "feat(web): ChatPanel component"
```

---

## Task 7: Wire ChatPanel into the editor

Show a **Chat** button when the graph contains a `chat_trigger`; clicking it opens the right-docked panel. Reuse `connectRunStream` to animate the canvas while a turn runs.

**Files:**
- Modify: `apps/web/src/EditorPage.tsx`
- Modify: `apps/web/src/editor.css`

- [ ] **Step 1: Add panel state and a helper to detect a chat trigger**

In `EditorPage.tsx`, add the import near the other editor imports:

```tsx
import { ChatPanel } from "./editor/ChatPanel";
```

Add state alongside the other `useState` hooks (~line 147):

```tsx
const [chatOpen, setChatOpen] = useState(false);
```

Add a helper near `triggerTypes` (~line 85, module scope):

```tsx
function chatTriggerNode(graph: WorkflowGraph | null | undefined) {
  return graph?.nodes?.find((n) => n.type === "chat_trigger") ?? null;
}
```

- [ ] **Step 2: Render the Chat button**

Find where editor toolbar buttons are rendered (search for the existing run / `Functions` buttons, e.g. `functionsOpen` usage). Add, guarded by the presence of a chat trigger (use the current graph from the editor store — match how other buttons read the graph; below assumes a `graph` value in scope where the toolbar renders):

```tsx
{chatTriggerNode(graph) ? (
  <button type="button" onClick={() => setChatOpen(true)}>
    Chat
  </button>
) : null}
```

If the toolbar does not already have the live `graph` in scope, read it the same way the neighboring buttons do (e.g. from the zustand store selector used elsewhere in the file). Do not introduce a new data source.

- [ ] **Step 3: Render the panel**

Near where other right-side panels/modals render (e.g. `functionsOpen && <FunctionsPanel … />`), add:

```tsx
{chatOpen && workflow ? (
  (() => {
    const node = chatTriggerNode(graph);
    const params = (node?.params ?? {}) as Record<string, string>;
    return (
      <ChatPanel
        workflowId={workflow.id}
        title={params.title ?? "Chat"}
        placeholder={params.input_placeholder ?? "Type a message…"}
        initialMessage={params.initial_message ?? ""}
        onRun={(runId) => connectRunStream(runId)}
        onClose={() => setChatOpen(false)}
      />
    );
  })()
) : null}
```

`connectRunStream(runId)` already exists in this file (~line 524) and drives the canvas node animation.

- [ ] **Step 4: Add panel styles**

Append to `apps/web/src/editor.css`:

```css
.chat-panel {
  position: absolute;
  top: 0;
  right: 0;
  width: 340px;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--panel-bg, #fff);
  border-left: 1px solid var(--border, #e2e8f0);
  z-index: 20;
}
.chat-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border, #e2e8f0);
  font-weight: 600;
}
.chat-panel-actions button {
  margin-left: 6px;
}
.chat-panel-messages {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.chat-bubble {
  max-width: 80%;
  padding: 6px 10px;
  border-radius: 10px;
  white-space: pre-wrap;
  word-break: break-word;
}
.chat-bubble-user {
  align-self: flex-end;
  background: #2563eb;
  color: #fff;
}
.chat-bubble-bot {
  align-self: flex-start;
  background: #f1f5f9;
  color: #0f172a;
}
.chat-bubble-error {
  background: #fee2e2;
  color: #991b1b;
}
.chat-typing {
  opacity: 0.6;
}
.chat-panel-input {
  display: flex;
  gap: 6px;
  padding: 10px;
  border-top: 1px solid var(--border, #e2e8f0);
}
.chat-panel-input input {
  flex: 1;
}
```

(Match the CSS variable names already used in `editor.css`; the fallbacks above keep it working if a variable is absent.)

- [ ] **Step 5: Typecheck + build + tests**

Run: `npm --prefix apps/web run typecheck && npm --prefix apps/web test -- ChatPanel`
Expected: typecheck PASS; ChatPanel tests PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/EditorPage.tsx apps/web/src/editor.css
git commit -m "feat(web): open Chat panel from the editor and animate the run"
```

---

## Task 8: Full regression + manual verification

- [ ] **Step 1: Backend suites**

Run: `uv run pytest packages/nodes/tests/test_builtin_nodes.py packages/nodes/tests/test_ai_v2_nodes.py -q` then `cd apps/api && uv run pytest tests/test_chat.py -q`
Expected: all PASS.

- [ ] **Step 2: Lint the changed backend code**

Run: `uv run ruff check apps/api/app/services/chat_service.py apps/api/app/routers/chat.py apps/api/tests/test_chat.py packages/nodes/noodle_nodes/builtin.py packages/nodes/noodle_nodes/ai_v2/agents.py`
Expected: no errors.

- [ ] **Step 3: Frontend typecheck + full vitest**

Run: `npm --prefix apps/web run typecheck && npm --prefix apps/web test`
Expected: all PASS.

- [ ] **Step 4: Manual smoke (optional but recommended)**

Rebuild and restart the stack (`docker compose -f deploy/docker-compose.yml --project-directory deploy up -d --build`), open the editor, build `Chat Trigger → AI Agent v2 (+ a Chat Model)`, click **Chat**, send a message, confirm the reply appears and nodes animate.

- [ ] **Step 5: Final commit (if any uncommitted polish)**

```bash
git add -A && git commit -m "chore: chat trigger phase 1 cleanup"
```

---

## Notes for the implementer

- **Do not publish in tests.** `PUT /workflows/{id}` saves the draft graph; `run_chat_turn(prefer_draft=True)` reads `workflow.draft_graph`. No `publish` call is needed.
- **Runs are synchronous in tests** (`settings.run_synchronously = True` in `conftest.py`), so `_await_run_terminal` returns quickly.
- **The seeded cache replaces the trigger's output** — the `chat_trigger` function body only runs on non-chat (manual) executions.
- **`require_permission("workflow:run")`** is the same guard the run endpoints use; keep it so chat respects existing roles.
- If importing the underscore helpers from `triggers` feels wrong, lift `_await_run_terminal` and `_last_node_output` into a new `app/services/run_wait.py` and import from there in both modules (optional refactor; keep the behavior identical and re-run `test_triggers.py`).
