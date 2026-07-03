"""Provider-neutral AI runtime protocols for Nodyra.

These types are the shared contract between:
- AI supplier nodes (chat model, embedding model, memory, tools, parsers)
- The agent executor in the engine
- The AI v2 node implementations

Design goals
------------
- Zero external AI-SDK dependencies (no LangChain, no OpenAI SDK).
  All providers talk JSON-over-HTTP and normalize to these types.
- Pydantic models for serialization / deserialization by the run store.
- Abstract base classes (not Protocol) so ``isinstance`` checks work at
  runtime and adapter wiring can be validated at graph-build time.
- ``AgentActionRequest`` enables engine-mediated tool execution (WP11).
  An agent node raises/returns one of these when it needs tool calls
  dispatched; the engine schedules them and calls ``agent.resume()``.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Message types
# ---------------------------------------------------------------------------


class MessageRole(StrEnum):
    """Valid roles in a conversation thread."""

    system = "system"
    user = "user"
    assistant = "assistant"
    tool = "tool"


class AIMessage(BaseModel):
    """A single message in a conversation thread.

    For ``role=tool`` messages, set ``tool_call_id`` and ``name`` to
    identify which tool call is being answered. For ``role=assistant``
    messages that requested tools, keep ``tool_calls`` populated so a later
    resume call can send provider-native tool-call history back to the model.
    """

    role: MessageRole
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)

    @classmethod
    def system(cls, content: str) -> AIMessage:
        return cls(role=MessageRole.system, content=content)

    @classmethod
    def user(cls, content: str) -> AIMessage:
        return cls(role=MessageRole.user, content=content)

    @classmethod
    def assistant(
        cls,
        content: str,
        *,
        tool_calls: list[ToolCall] | None = None,
    ) -> AIMessage:
        return cls(
            role=MessageRole.assistant,
            content=content,
            tool_calls=tool_calls or [],
        )

    @classmethod
    def tool_result(cls, *, tool_call_id: str, name: str, content: str) -> AIMessage:
        return cls(
            role=MessageRole.tool,
            tool_call_id=tool_call_id,
            name=name,
            content=content,
        )


# ---------------------------------------------------------------------------
# Tool types
# ---------------------------------------------------------------------------


class ToolParameterSchema(BaseModel):
    """JSON Schema object describing a tool's input parameters."""

    type: str = "object"
    properties: dict[str, Any] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)
    additional_properties: bool = Field(False, alias="additionalProperties")

    model_config = {"populate_by_name": True}


class ToolSchema(BaseModel):
    """Description of a callable tool that the model can invoke."""

    name: str
    description: str
    parameters: ToolParameterSchema = Field(default_factory=ToolParameterSchema)


class ToolCall(BaseModel):
    """A single tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """The result of a dispatched tool call, ready to feed back to the model."""

    tool_call_id: str
    name: str
    content: str
    is_error: bool = False


# ---------------------------------------------------------------------------
# Model metadata
# ---------------------------------------------------------------------------


class ModelUsage(BaseModel):
    """Token consumption for a single model call."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float | None = None
    prompt_price_per_1m_tokens: float | None = None
    completion_price_per_1m_tokens: float | None = None

    def __add__(self, other: ModelUsage) -> ModelUsage:
        estimated_cost_usd: float | None = None
        if self.estimated_cost_usd is not None or other.estimated_cost_usd is not None:
            estimated_cost_usd = (self.estimated_cost_usd or 0.0) + (
                other.estimated_cost_usd or 0.0
            )
        return ModelUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            estimated_cost_usd=estimated_cost_usd,
            prompt_price_per_1m_tokens=self.prompt_price_per_1m_tokens,
            completion_price_per_1m_tokens=self.completion_price_per_1m_tokens,
        )

    def with_estimated_cost(
        self,
        *,
        prompt_price_per_1m_tokens: float | int | None = None,
        completion_price_per_1m_tokens: float | int | None = None,
    ) -> ModelUsage:
        """Attach a USD estimate using caller-provided per-million-token rates."""
        prompt_rate = _non_negative_float(prompt_price_per_1m_tokens)
        completion_rate = _non_negative_float(completion_price_per_1m_tokens)
        if prompt_rate is None and completion_rate is None:
            return self
        prompt_cost = (self.prompt_tokens * (prompt_rate or 0.0)) / 1_000_000
        completion_cost = (
            self.completion_tokens * (completion_rate or 0.0)
        ) / 1_000_000
        return self.model_copy(
            update={
                "estimated_cost_usd": round(prompt_cost + completion_cost, 10),
                "prompt_price_per_1m_tokens": prompt_rate,
                "completion_price_per_1m_tokens": completion_rate,
            }
        )


def _non_negative_float(value: float | int | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


class ModelCapabilities(BaseModel):
    """Advertised capabilities of a specific model/provider combination."""

    supports_tools: bool = False
    supports_vision: bool = False
    supports_json_mode: bool = False
    supports_streaming: bool = False
    context_window: int | None = None
    max_output_tokens: int | None = None


# ---------------------------------------------------------------------------
# Chat request / response
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """Normalized input for a single chat completion call."""

    messages: list[AIMessage]
    model: str
    temperature: float = 0.2
    max_tokens: int | None = None
    tools: list[ToolSchema] = Field(default_factory=list)
    response_format: Literal["text", "json_object"] = "text"
    timeout_seconds: int = 75


class ChatResponse(BaseModel):
    """Normalized output from a single chat completion call."""

    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: ModelUsage = Field(default_factory=ModelUsage)
    model: str = ""
    finish_reason: str = ""
    provider: str = ""


# ---------------------------------------------------------------------------
# Embedding types
# ---------------------------------------------------------------------------


class EmbeddingRequest(BaseModel):
    """Input for a batch embedding request."""

    texts: list[str]
    model: str
    timeout_seconds: int = 60


class EmbeddingResponse(BaseModel):
    """Normalized output from an embedding request."""

    embeddings: list[list[float]]
    model: str = ""
    usage: ModelUsage = Field(default_factory=ModelUsage)


# ---------------------------------------------------------------------------
# RAG document / retrieval types
# ---------------------------------------------------------------------------


class Document(BaseModel):
    """Text document or chunk used by RAG nodes."""

    id: str = ""
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievedDocument(Document):
    """Document returned by a retriever with similarity score metadata."""

    score: float = 0.0


# ---------------------------------------------------------------------------
# Agent action — engine-mediated tool execution (WP11 target)
# ---------------------------------------------------------------------------


class AgentActionRequest(BaseModel):
    """Emitted by an agent node when it needs tool calls dispatched.

    The engine receives this, dispatches each tool call through connected
    ``ToolAdapter`` instances, then re-invokes the agent node with the
    collected ``ToolResult`` list. Tool invocations are observable through run
    events and approval records; graph ``NodeRun`` rows remain one row per
    workflow node.
    """

    tool_calls: list[ToolCall]
    messages_so_far: list[AIMessage]
    step: int = 0
    max_steps: int = 10
    allow_side_effects: bool = False
    approved_tool_call_ids: list[str] = Field(default_factory=list)
    rejected_tool_call_ids: list[str] = Field(default_factory=list)
    # Results of calls that already executed before an approval pause. A
    # request can pause more than once (one approval per side-effecting call),
    # and each resume re-dispatches the same request — without this ledger an
    # already-approved call would execute again on every later resume.
    completed_results: list[ToolResult] = Field(default_factory=list)


class AgentResumeInput(BaseModel):
    """Passed back to the agent node after tool calls are dispatched."""

    tool_results: list[ToolResult]
    messages_so_far: list[AIMessage]
    step: int
    max_steps: int
    allow_side_effects: bool = False


class AgentActionResponse(BaseModel):
    """Engine output after dispatching an ``AgentActionRequest``.

    If the emitting agent node can accept the hidden ``agent_resume`` runtime
    kwarg, the engine converts this to ``AgentResumeInput`` and re-invokes the
    node. Otherwise this response is returned as the node output so simple
    nodes can still observe dispatched tool results.
    """

    tool_results: list[ToolResult]
    messages_so_far: list[AIMessage]
    step: int
    max_steps: int
    allow_side_effects: bool = False

    def as_resume_input(self) -> AgentResumeInput:
        return AgentResumeInput(
            tool_results=self.tool_results,
            messages_so_far=self.messages_so_far,
            step=self.step,
            max_steps=self.max_steps,
            allow_side_effects=self.allow_side_effects,
        )


class AgentStepEvent(BaseModel):
    """Serializable description of an agent step/tool event."""

    type: str
    agent_node_id: str
    step: int
    max_steps: int
    tool_call_id: str | None = None
    tool_name: str | None = None
    approval_key: str | None = None
    status: str | None = None
    message: str = ""


class AgentApprovalRequired(RuntimeError):
    """Raised when a side-effecting tool call needs operator approval."""

    def __init__(
        self,
        *,
        request: AgentActionRequest,
        tool_call: ToolCall,
        approval_key: str,
        message: str,
    ) -> None:
        super().__init__(message)
        self.request = request
        self.tool_call = tool_call
        self.approval_key = approval_key
        self.message = message


# ---------------------------------------------------------------------------
# Abstract adapters
# ---------------------------------------------------------------------------


class ChatModelAdapter(ABC):
    """Provider-neutral chat model interface.

    Concrete implementations live in
    ``nodyra_nodes.ai_v2.providers.<provider>``.

    The ``complete`` method is synchronous because workflow nodes are
    executed in a thread pool; async wrappers can call
    ``asyncio.get_event_loop().run_in_executor`` if needed.
    """

    @property
    def capabilities(self) -> ModelCapabilities:
        """Return static capability metadata for this model/provider."""
        return ModelCapabilities()

    @abstractmethod
    def complete(self, request: ChatRequest) -> ChatResponse:
        """Perform a single chat completion call."""

    def as_config(self) -> dict[str, Any]:
        """Serialize the adapter configuration for storage or passing as a
        supplier node output.  The dict must round-trip through
        ``from_config``."""
        return {}

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> ChatModelAdapter:  # noqa: ARG003
        """Re-create an adapter from a previously serialized config dict.

        Concrete adapters override this to read their specific keys.
        """
        raise NotImplementedError(f"{cls.__name__}.from_config is not implemented")


class EmbeddingModelAdapter(ABC):
    """Provider-neutral embedding model interface."""

    @abstractmethod
    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Embed a batch of texts."""

    def as_config(self) -> dict[str, Any]:
        return {}

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> EmbeddingModelAdapter:  # noqa: ARG003
        raise NotImplementedError(f"{cls.__name__}.from_config is not implemented")


class DocumentLoaderAdapter(ABC):
    """Loads raw documents for ingestion workflows."""

    @abstractmethod
    def load(self) -> list[Document]:
        """Return documents with text and metadata."""

    def as_config(self) -> dict[str, Any]:
        return {}


class VectorStoreAdapter(ABC):
    """Stores embedded documents and returns nearest matches."""

    @abstractmethod
    def upsert(
        self,
        *,
        documents: list[Document],
        embeddings: list[list[float]],
    ) -> None:
        """Insert or update documents and their embedding vectors."""

    @abstractmethod
    def query(self, *, vector: list[float], top_k: int = 5) -> list[RetrievedDocument]:
        """Return nearest documents for an embedding vector."""

    def delete(self, *, ids: list[str]) -> None:
        """Delete documents by id when the backend supports deletion."""
        raise NotImplementedError(f"{type(self).__name__}.delete is not implemented")

    def as_config(self) -> dict[str, Any]:
        return {}


class RetrieverAdapter(ABC):
    """Retrieves relevant documents for a text query."""

    @abstractmethod
    def retrieve(self, query: str, *, top_k: int = 5) -> list[RetrievedDocument]:
        """Return top matching documents for ``query``."""

    def as_config(self) -> dict[str, Any]:
        return {}


class MemoryAdapter(ABC):
    """Provider-neutral conversation memory interface."""

    @abstractmethod
    def load(self, *, session_id: str) -> list[AIMessage]:
        """Load recent messages for a session."""

    @abstractmethod
    def save(self, *, session_id: str, messages: list[AIMessage]) -> None:
        """Persist updated messages for a session."""

    @abstractmethod
    def clear(self, *, session_id: str) -> None:
        """Wipe all messages for a session."""


class OutputParserAdapter(ABC):
    """Provider-neutral output parser / validator."""

    @abstractmethod
    def parse(self, text: str) -> Any:
        """Parse and validate model output text."""

    @property
    def format_instructions(self) -> str:
        """Human-readable format instructions to inject into the prompt."""
        return ""


class ToolAdapter(ABC):
    """A callable tool that can be invoked by the agent executor."""

    @property
    @abstractmethod
    def schema(self) -> ToolSchema:
        """JSON schema descriptor for this tool."""

    @property
    def side_effecting(self) -> bool:
        """Whether invoking this tool may change external state."""
        return False

    @abstractmethod
    def invoke(self, arguments: dict[str, Any]) -> str:
        """Execute the tool and return a string result.

        Raise an exception to signal a tool error; the executor will catch
        it and produce an is_error=True ``ToolResult``.
        """

    async def invoke_async(self, arguments: dict[str, Any]) -> str:
        """Async invocation hook used by the engine.

        Synchronous tools can implement only ``invoke`` and use this default
        thread wrapper. Tools that need async host callbacks, such as workflow
        tools, can override this method without hiding execution inside an
        opaque node.
        """
        return await asyncio.to_thread(self.invoke, arguments)


class GuardrailAdapter(ABC):
    """Validates a model response before it leaves the agent node."""

    @abstractmethod
    def check(self, response: ChatResponse) -> ChatResponse:
        """Inspect or transform a response.

        Raise ``ValueError`` to block the response and trigger a retry.
        Return the (possibly modified) response to allow it through.
        """
