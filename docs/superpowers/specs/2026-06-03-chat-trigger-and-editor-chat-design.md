# Chat Trigger + In-Editor Chat — Design Spec

**Date:** 2026-06-03
**Status:** Phase 1 design approved; ready for implementation plan
**Topic:** Conversational chat interface for talking to an AI Agent workflow, like n8n's Chat Trigger.

## Problem

Noodle has an `ai_agent_v2` node with engine-mediated tool calls and session
memory, but no way to *talk to it conversationally* from the editor. n8n ships a
"Chat Trigger" that opens a chat window and routes each message into the
workflow, displaying the final node's output as the assistant reply. We want the
same, adapted to Noodle's Python-native, run-based architecture.

## Scope decomposition

The full feature is **one shared core with three surfaces**. They are built and
shipped in sequence, each as its own spec → plan → implementation cycle:

- **Phase 1 (A) — In-editor test chat** *(this spec)*: a Chat Trigger node, a
  reusable chat-run service, an editor endpoint, and a right-docked chat panel.
- **Phase 2 (B) — Hosted public chat page**: a shareable `/chat/<id>` page for
  end users. Reuses the Phase 1 chat-run service; adds public access rules and a
  standalone page. *Future — not specified here.*
- **Phase 3 (C) — Embeddable widget**: a JS snippet dropping a floating chat
  bubble onto any site. Reuses the same core; adds CORS, theming, a widget
  bundle. *Future — not specified here.*

This spec covers **Phase 1 only**. Phases 2 and 3 get their own specs once the
core exists.

## Design decisions (confirmed)

- **Placement:** right-side docked panel in the editor; canvas stays visible so
  node executions animate live as the agent runs.
- **Reply style:** final answer only. A "typing…" indicator shows while the run
  executes; the complete reply appears when it finishes. Token streaming is
  explicitly deferred (would require streaming support in the model adapters and
  a new streaming channel through the engine).
- **Reply source:** the **last node's text output**, extracted generically
  (tries `answer`, then `text`, then `output`, else stringifies). Works whether
  the final node is an AI Agent or anything else — matches n8n, and keeps plain
  non-agent chat workflows possible.
- **Memory:** each chat panel session carries a client-generated `session_id`.
  The same id keeps the conversation threaded through the AI Agent's existing
  session memory. A "Reset chat" button generates a fresh `session_id`.

## Architecture

One chat turn flows:

```
Chat panel (editor)
  │  POST /workflows/{id}/chat  { message, session_id }
  ▼
chat_service.run_chat_turn()
  │  seed chat_trigger output = { chatInput: message, sessionId }
  │  start_run(graph, cache={trigger_id: {"main": payload}}, trigger_node_id, mode="test")
  ▼
Engine runs  Chat Trigger → AI Agent (session_id = sessionId) → … → last node
  │                                    └─ run events stream over /ws/runs/{run_id}
  │                                       → canvas animates live
  ▼
await _await_run_terminal(run_id) → extract last node text → reply
  ▼
return { run_id, reply, session_id }  →  panel renders bot bubble
```

This reuses existing machinery: `start_run` with a seeded trigger cache (exactly
how `dispatch_webhook` seeds `webhook_trigger`), and the
`_await_run_terminal` / `_last_node_output` helpers used by synchronous
"Last Node" webhooks.

## Components

### 1. `chat_trigger` node — `packages/nodes/noodle_nodes/builtin.py`

- `@node(name="Chat Trigger", id="chat_trigger", category="Triggers",
  icon="chat", role="trigger", inputs=[], outputs=["main"])`.
- Returns its seeded payload: `{ "chatInput": <message>, "sessionId": <id> }`.
  When run outside the chat path (e.g. a manual run), returns an empty/default
  payload so the graph is still runnable.
- Params (all optional, grouped under "Options"):
  - `initial_message` (textarea) — assistant greeting shown when the panel opens.
  - `input_placeholder` (text) — placeholder for the message box.
  - `title` (text) — panel header label.

### 2. AI Agent input recognition — `packages/nodes/noodle_nodes/ai_v2/agents.py`

- `_task_text()` already reads `input.task | input.prompt | input.text | input`.
  Add `chatInput` to that key list so a Chat Trigger wired straight into the
  agent works with no extra mapping.
- `_session_id()` already reads `input.session_id`. Add `sessionId` (camelCase)
  as an accepted key so the trigger's output threads memory directly.

### 3. `chat_service.run_chat_turn` — `apps/api/app/services/chat_service.py` (new)

```python
async def run_chat_turn(
    workflow_id: str,
    message: str,
    session_id: str,
    *,
    prefer_draft: bool = True,
    timeout: float = 120.0,
) -> ChatTurnResult:  # { run_id, reply, session_id, status }
```

- Load workflow; pick graph (draft when `prefer_draft`, else latest published).
- Find the `chat_trigger` node. If none → raise a typed `NoChatTriggerError`
  (router maps to 422). If several → use the first (Phase 1 keeps it simple).
- Seed `cache = {trigger_id: {"main": {"chatInput": message, "sessionId": session_id}}}`.
- `run_id = await start_run(workflow_id, graph, version_number,
  workflow_version_id=..., mode="test" if prefer_draft else "production",
  trigger_type="chat", cache=cache, trigger_node_id=trigger_id)`.
- `status = await _await_run_terminal(run_id, timeout)`.
  - `None` → return status `"timeout"`, reply = friendly timeout message.
  - not `"success"` → status `"error"`, reply = friendly failure message.
  - `"success"` → `body = await _last_node_output(session, run_id)`;
    `reply = _extract_reply(body)`.
- `_extract_reply(value)`: if dict, try `answer` → `text` → `output` → JSON
  dump; else `str(value)`.
- The `_await_run_terminal` and `_last_node_output` helpers are reused. Phase 1
  imports them from `app.services.triggers`; if that coupling feels wrong during
  implementation, lift them into a shared `app.services.run_wait` module and
  have both callers import from there. (Decision deferred to the plan.)

### 4. Editor endpoint — `apps/api/app/routers/chat.py` (new) or in `runs.py`

- `POST /workflows/{workflow_id}/chat`, body `{ message: str, session_id: str }`.
- Auth: same dependency as other workflow routes (editor user).
- Calls `run_chat_turn(..., prefer_draft=True)` (the editor tests the draft).
- Returns `{ run_id, reply, session_id, status }`.
- `422` when the workflow has no `chat_trigger` (body explains why).

### 5. Frontend — `apps/web/src/editor/ChatPanel.tsx` (new)

- Right-docked panel, toggled by a **Chat** button in the editor toolbar. The
  button is shown only when the current graph contains a `chat_trigger` node
  (otherwise a tooltip explains a Chat Trigger is required).
- State: message list (`{role: "user"|"bot", text}`), input value,
  `sessionId` (generated with `crypto.randomUUID()` on open / on reset),
  `sending` flag.
- On open: if the chat_trigger has an `initial_message`, seed the list with it
  as a bot bubble. Use `title`/`input_placeholder` params for chrome.
- On send: append the user bubble, show a "typing…" bot placeholder, call
  `api.sendChatMessage(workflowId, message, sessionId)`; on response replace the
  placeholder with the reply (or an error bubble with a "view run" link to the
  run timeline using the returned `run_id`).
- Subscribe to `/ws/runs/{run_id}` using the editor's existing run-event handling
  so nodes animate live while the turn runs.
- **Reset chat** button: clears the list and generates a new `sessionId`.
- `apps/web/src/api.ts`: add
  `sendChatMessage(workflowId, message, sessionId) -> { run_id, reply, session_id, status }`.
- Minimal styling reusing existing editor panel CSS conventions.

## Error handling

| Case | Behavior |
|---|---|
| Workflow has no `chat_trigger` | Chat button disabled with explanatory tooltip; endpoint returns 422 if called. |
| Run fails (`error`/`cancelled`) | Bot bubble shows a friendly failure message + "view run" link to the timeline. |
| Run times out | Bot bubble shows a timeout message + run link; the run keeps going server-side. |
| Last node output has no text-ish field | `_extract_reply` JSON-dumps the dict / stringifies, so the user always sees *something*. |
| Agent not wired after trigger | Whatever the last node that ran outputs becomes the reply (by design — last-node semantics). |

## Testing

**Backend**
- `chat_trigger` registers; manifest shape (trigger role, no inputs, `main`
  output); source viewer renders.
- `_extract_reply` unit table: agent `{answer}`, `{text}`, `{output}`, bare
  string, arbitrary dict.
- `run_chat_turn` (SQLite): seed → run a tiny graph (Chat Trigger → edit_fields
  echo) → reply matches; session threading (two turns, same `session_id`,
  agent memory persists — use a stub model adapter); non-agent last node returns
  its text; no-trigger raises `NoChatTriggerError`.
- Endpoint: `POST /workflows/{id}/chat` happy path returns reply + run_id; 422
  with no chat trigger; failure path returns `status:"error"`.

**Frontend**
- `ChatPanel` vitest: send renders user + bot bubbles; reset generates a new
  session id; Chat button hidden when no chat_trigger in the graph.

## Out of scope (Phase 1)

- Public access / hosted page (Phase 2), embeddable widget (Phase 3).
- Token streaming, file/image uploads in chat, voice.
- Multiple chat triggers in one graph (use the first; revisit later).

## Success criteria

- A workflow `Chat Trigger → AI Agent v2 → …` can be tested from the editor: type
  a message, watch nodes animate, get the agent's reply in a right-docked panel.
- Multi-turn conversation in one panel session threads the agent's memory; Reset
  starts a clean thread.
- The chat-run service is surface-agnostic, ready for Phases 2 and 3 to reuse.
