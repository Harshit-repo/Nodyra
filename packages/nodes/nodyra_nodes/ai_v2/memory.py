"""AI Memory supplier nodes + concrete buffer memory adapter — WP10."""

from __future__ import annotations

from typing import Any

from nodyra.ai_runtime import AIMessage, MemoryAdapter, MessageRole
from nodyra.sdk import node

AI_CATEGORY = "AI"


class BufferMemoryAdapter(MemoryAdapter):
    """Bounded, in-process conversation buffer.

    Keeps the most recent ``window`` messages per session in memory.  Suitable
    for single-process runs; not durable across worker restarts.  A Redis-backed
    adapter can be added later without changing the node contract.
    """

    def __init__(self, *, window: int = 20) -> None:
        self._window = max(1, int(window))
        self._store: dict[str, list[AIMessage]] = {}

    def load(self, *, session_id: str) -> list[AIMessage]:
        return list(self._store.get(session_id, []))

    def save(self, *, session_id: str, messages: list[AIMessage]) -> None:
        trimmed = list(messages)[-self._window :]
        self._store[session_id] = trimmed

    def clear(self, *, session_id: str) -> None:
        self._store.pop(session_id, None)

    def as_config(self) -> dict[str, Any]:
        return {"adapter": "buffer_memory", "window": self._window}

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> BufferMemoryAdapter:
        return cls(window=int(config.get("window") or 20))


@node(
    name="AI Buffer Memory",
    id="ai_buffer_memory",
    category=AI_CATEGORY,
    role="supplier",
    icon="ai",
    outputs=["memory"],
    output_kinds={"memory": "ai_memory"},
    params={
        "window": {
            "description": "Maximum number of recent messages to retain per session.",
        },
        "system_prompt": {
            "widget": "textarea",
            "description": "Optional system message seeded at the start of the buffer.",
        },
    },
)
def ai_buffer_memory(
    window: int = 20,
    system_prompt: str = "",
) -> MemoryAdapter:
    """Supply an in-process conversation buffer to downstream AI Agent nodes."""
    adapter = BufferMemoryAdapter(window=int(window or 20))
    if system_prompt.strip():
        adapter.save(
            session_id="__seed__",
            messages=[AIMessage(role=MessageRole.system, content=system_prompt.strip())],
        )
    return adapter
