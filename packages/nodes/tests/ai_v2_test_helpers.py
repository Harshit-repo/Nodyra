from __future__ import annotations

from typing import Any

from noodle.ai_runtime import (
    ChatModelAdapter,
    ChatRequest,
    ChatResponse,
    ToolAdapter,
    ToolParameterSchema,
    ToolSchema,
)


class ScriptedChatModel(ChatModelAdapter):
    def __init__(self, responses: list[ChatResponse]) -> None:
        self.responses = responses
        self.requests: list[ChatRequest] = []

    def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        return self.responses.pop(0)

    def as_config(self) -> dict[str, Any]:
        return {"adapter": "scripted", "model": "test-model"}


class DummyTool(ToolAdapter):
    def __init__(self, name: str = "lookup") -> None:
        self._name = name

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name=self._name,
            description="Lookup test data.",
            parameters=ToolParameterSchema(
                properties={"query": {"type": "string"}},
                required=["query"],
            ),
        )

    def invoke(self, arguments: dict[str, Any]) -> str:
        return f"result:{arguments.get('query')}"
