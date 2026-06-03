"""WP10 — AI v2 supplier node tests.

Verifies that the AI v2 supplier nodes register with the correct typed output
ports/roles and that their functions return the expected adapter instances.
"""

from __future__ import annotations

import json
from typing import Any

import noodle_nodes  # noqa: F401 - registers nodes
from noodle.ai_runtime import (
    AgentActionRequest,
    AgentResumeInput,
    AIMessage,
    ChatModelAdapter,
    ChatRequest,
    ChatResponse,
    Document,
    DocumentLoaderAdapter,
    EmbeddingModelAdapter,
    EmbeddingRequest,
    EmbeddingResponse,
    GuardrailAdapter,
    MemoryAdapter,
    MessageRole,
    OutputParserAdapter,
    RetrieverAdapter,
    ToolAdapter,
    ToolCall,
    ToolParameterSchema,
    ToolResult,
    ToolSchema,
    VectorStoreAdapter,
)
from noodle.context import node_debug, workflow_caller
from noodle.engine import execute
from noodle.models import Edge, GraphNode, WorkflowGraph
from noodle.sdk import NodeRegistry, node, registry

# ---------------------------------------------------------------------------
# Registration + typed ports
# ---------------------------------------------------------------------------


def _output_kind(node_id: str, port: str) -> str:
    manifest = registry.get(node_id).manifest
    spec = next(o for o in manifest.outputs if o.name == port)
    return spec.data_kind


def _input_kind(node_id: str, port: str) -> str:
    manifest = registry.get(node_id).manifest
    spec = next(i for i in manifest.inputs if i.name == port)
    return spec.data_kind


class ScriptedChatModel(ChatModelAdapter):
    def __init__(self, responses: list[ChatResponse]) -> None:
        self.responses = responses
        self.requests: list[ChatRequest] = []

    def complete(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        return self.responses.pop(0)

    def as_config(self) -> dict[str, Any]:
        return {"adapter": "scripted", "model": "test-model"}


class ScriptedEmbeddingModel(EmbeddingModelAdapter):
    def __init__(self) -> None:
        self.requests: list[EmbeddingRequest] = []

    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.requests.append(request)
        embeddings: list[list[float]] = []
        for text in request.texts:
            lowered = text.lower()
            embeddings.append([1.0, 0.0] if "ada" in lowered else [0.0, 1.0])
        return EmbeddingResponse(embeddings=embeddings, model=request.model or "test-emb")

    def as_config(self) -> dict[str, Any]:
        return {"adapter": "scripted_embedding", "model": "test-emb"}


class FakeResponse:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> object:
        return self._payload


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


def test_chat_model_openai_registered_with_typed_port() -> None:
    node_def = registry.get("ai_chat_model_openai")
    assert node_def.manifest.role.value == "supplier"
    assert _output_kind("ai_chat_model_openai", "model") == "ai_language_model"


def test_chat_model_anthropic_registered_with_typed_port() -> None:
    assert registry.get("ai_chat_model_anthropic").manifest.role.value == "supplier"
    assert _output_kind("ai_chat_model_anthropic", "model") == "ai_language_model"


def test_chat_model_azure_registered_with_typed_port() -> None:
    assert registry.get("ai_chat_model_azure").manifest.role.value == "supplier"
    assert _output_kind("ai_chat_model_azure", "model") == "ai_language_model"


def test_embedding_model_registered_with_typed_port() -> None:
    assert registry.get("ai_embedding_model").manifest.role.value == "supplier"
    assert _output_kind("ai_embedding_model", "model") == "ai_embedding_model"


def test_buffer_memory_registered_with_typed_port() -> None:
    assert registry.get("ai_buffer_memory").manifest.role.value == "supplier"
    assert _output_kind("ai_buffer_memory", "memory") == "ai_memory"


def test_http_tool_registered_with_typed_port() -> None:
    assert registry.get("ai_http_tool").manifest.role.value == "tool"
    assert _output_kind("ai_http_tool", "tool") == "ai_tool"


def test_workflow_tool_registered_with_typed_port() -> None:
    assert registry.get("ai_workflow_tool").manifest.role.value == "tool"
    assert _output_kind("ai_workflow_tool", "tool") == "ai_tool"


def test_tool_bundle_registered_with_typed_ports() -> None:
    node_def = registry.get("ai_tool_bundle")
    assert node_def.manifest.role.value == "tool"
    assert _input_kind("ai_tool_bundle", "tool_1") == "ai_tool"
    assert _output_kind("ai_tool_bundle", "tools") == "ai_tool"


def test_agent_v2_registered_with_typed_ports() -> None:
    node_def = registry.get("ai_agent_v2")
    assert node_def.manifest.role.value == "executable"
    assert _input_kind("ai_agent_v2", "model") == "ai_language_model"
    assert _input_kind("ai_agent_v2", "tool") == "ai_tool"
    assert _input_kind("ai_agent_v2", "memory") == "ai_memory"
    assert _input_kind("ai_agent_v2", "parser") == "ai_output_parser"
    assert _input_kind("ai_agent_v2", "guardrail") == "ai_guardrail"
    params = {param.name: param for param in node_def.manifest.params}
    assert params["side_effect_approval"].choices == [
        "require_approval",
        "auto_approve",
    ]
    assert params["side_effect_approval"].display_name == "Tool approval"
    assert params["side_effect_approval"].group is None


def test_output_parser_registered_with_typed_port() -> None:
    assert registry.get("ai_structured_output_parser").manifest.role.value == "output_parser"
    assert _output_kind("ai_structured_output_parser", "parser") == "ai_output_parser"


def test_guardrail_registered_with_typed_port() -> None:
    assert registry.get("ai_guardrail").manifest.role.value == "supplier"
    assert _output_kind("ai_guardrail", "guardrail") == "ai_guardrail"


def test_document_loader_registered_with_typed_port() -> None:
    assert registry.get("ai_text_document_loader").manifest.role.value == "supplier"
    assert _output_kind("ai_text_document_loader", "loader") == "ai_document_loader"


def test_recursive_text_splitter_accepts_document_loader() -> None:
    assert _input_kind("ai_recursive_text_splitter", "loader") == "ai_document_loader"
    assert _output_kind("ai_recursive_text_splitter", "documents") == "main"


def test_vector_store_and_retriever_ports_are_typed() -> None:
    assert _output_kind("ai_in_memory_vector_store", "store") == "ai_vector_store"
    assert _output_kind("ai_qdrant_vector_store", "store") == "ai_vector_store"
    assert _input_kind("ai_vector_store_upsert", "model") == "ai_embedding_model"
    assert _input_kind("ai_vector_store_upsert", "store") == "ai_vector_store"
    assert _output_kind("ai_vector_store_upsert", "store") == "ai_vector_store"
    assert _input_kind("ai_vector_store_delete", "store") == "ai_vector_store"
    assert _output_kind("ai_vector_store_delete", "store") == "ai_vector_store"
    assert _input_kind("ai_vector_retriever_v2", "store") == "ai_vector_store"
    assert _output_kind("ai_vector_retriever_v2", "retriever") == "ai_retriever"
    assert _input_kind("ai_rag_chain", "model") == "ai_language_model"
    assert _input_kind("ai_rag_chain", "retriever") == "ai_retriever"


# ---------------------------------------------------------------------------
# Chat model suppliers return adapters
# ---------------------------------------------------------------------------


def test_chat_model_openai_returns_adapter() -> None:
    fn = registry.get("ai_chat_model_openai").func
    adapter = fn(
        credentials={"api_key": "sk-test"},
        provider="openai",
        model="gpt-4.1-mini",
        temperature=0.3,
        max_tokens=256,
        prompt_price_per_1m_tokens=2.0,
        completion_price_per_1m_tokens=8.0,
    )
    assert isinstance(adapter, ChatModelAdapter)
    config = adapter.as_config()
    assert config["model"] == "gpt-4.1-mini"
    assert config["temperature"] == 0.3
    assert config["max_tokens"] == 256
    assert config["prompt_price_per_1m_tokens"] == 2.0
    assert config["completion_price_per_1m_tokens"] == 8.0


def test_chat_model_openai_ollama_provider() -> None:
    fn = registry.get("ai_chat_model_openai").func
    adapter = fn(credentials={}, provider="ollama", model="llama3.2")
    assert isinstance(adapter, ChatModelAdapter)
    assert adapter.as_config()["provider"] == "ollama"


def test_chat_model_anthropic_returns_adapter() -> None:
    fn = registry.get("ai_chat_model_anthropic").func
    adapter = fn(
        credentials={"api_key": "sk-ant"},
        model="claude-3-5-haiku-latest",
        temperature=0.1,
        prompt_price_per_1m_tokens=3.0,
        completion_price_per_1m_tokens=15.0,
    )
    assert isinstance(adapter, ChatModelAdapter)
    config = adapter.as_config()
    assert config["adapter"] == "anthropic"
    assert config["temperature"] == 0.1
    assert config["prompt_price_per_1m_tokens"] == 3.0
    assert config["completion_price_per_1m_tokens"] == 15.0


def test_chat_model_azure_returns_adapter() -> None:
    fn = registry.get("ai_chat_model_azure").func
    adapter = fn(
        credentials={
            "api_key": "az-key",
            "azure_endpoint": "https://example.openai.azure.com",
            "deployment": "gpt-4o-mini",
        },
        model="gpt-4o-mini",
        api_version="2025-01-01-preview",
        prompt_price_per_1m_tokens=2.5,
        completion_price_per_1m_tokens=10.0,
    )
    assert isinstance(adapter, ChatModelAdapter)
    config = adapter.as_config()
    assert config["adapter"] == "azure_openai"
    assert config["azure_api_version"] == "2025-01-01-preview"
    assert config["prompt_price_per_1m_tokens"] == 2.5
    assert config["completion_price_per_1m_tokens"] == 10.0


# ---------------------------------------------------------------------------
# Embedding supplier
# ---------------------------------------------------------------------------


def test_embedding_model_openai_returns_adapter() -> None:
    fn = registry.get("ai_embedding_model").func
    adapter = fn(
        credentials={"api_key": "sk-test"},
        provider="openai",
        model="text-embedding-3-small",
        prompt_price_per_1m_tokens=0.02,
    )
    assert isinstance(adapter, EmbeddingModelAdapter)
    assert adapter.as_config()["model"] == "text-embedding-3-small"
    assert adapter.as_config()["prompt_price_per_1m_tokens"] == 0.02


def test_embedding_model_cohere_returns_adapter() -> None:
    fn = registry.get("ai_embedding_model").func
    adapter = fn(
        credentials={"api_key": "co-key"},
        provider="cohere",
        model="embed-english-v3.0",
    )
    assert isinstance(adapter, EmbeddingModelAdapter)
    assert adapter.as_config()["adapter"] == "cohere_embedding"


# ---------------------------------------------------------------------------
# RAG suppliers and executable nodes
# ---------------------------------------------------------------------------


def test_document_loader_and_splitter_return_chunks() -> None:
    loader = registry.get("ai_text_document_loader").func(
        text=(
            "Ada wrote detailed notes about computing machines and symbolic "
            "instructions.\n\nGrace built practical compiler systems and helped "
            "make programming languages usable."
        ),
        document_id="history",
        metadata='{"topic": "computing"}',
    )
    assert isinstance(loader, DocumentLoaderAdapter)
    loaded = loader.load()
    assert loaded[0].metadata["topic"] == "computing"

    splitter = registry.get("ai_recursive_text_splitter").func
    chunks = splitter(loader=loader, chunk_size=100, chunk_overlap=0)
    assert chunks["count"] == 2
    assert chunks["documents"][0]["metadata"]["source_document_id"] == "history"


def test_url_document_loader_blocks_private_targets(monkeypatch) -> None:
    def fake_get(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("private target should be blocked before requests")

    monkeypatch.setattr("requests.get", fake_get)
    loader = registry.get("ai_url_document_loader").func(
        url="http://localhost/private"
    )
    try:
        loader.load()
    except ValueError as exc:
        assert "private" in str(exc)
    else:
        raise AssertionError("private URL document target should be blocked")


def test_vector_store_upsert_and_retriever_returns_nearest_document() -> None:
    embedding_model = ScriptedEmbeddingModel()
    store = registry.get("ai_in_memory_vector_store").func(namespace="test")
    assert isinstance(store, VectorStoreAdapter)

    upsert = registry.get("ai_vector_store_upsert").func
    updated = upsert(
        documents={
            "documents": [
                {"id": "ada", "text": "Ada wrote notes on computation."},
                {"id": "grace", "text": "Grace built compiler systems."},
            ]
        },
        model=embedding_model,
        store=store,
    )
    assert updated is store
    assert store.as_config()["count"] == 2

    retriever = registry.get("ai_vector_retriever_v2").func(
        model=embedding_model,
        store=store,
        top_k=1,
    )
    assert isinstance(retriever, RetrieverAdapter)
    docs = retriever.retrieve("What did Ada write?", top_k=1)
    assert docs[0].id == "ada"

    retrieve_node = registry.get("ai_retrieve_documents").func
    out = retrieve_node(input={"query": "Ada"}, retriever=retriever, top_k=1)
    assert out["count"] == 1
    assert out["documents"][0]["id"] == "ada"

    deleted = registry.get("ai_vector_store_delete").func(
        input={"ids": ["ada"]},
        store=store,
    )
    assert deleted is store
    assert store.as_config()["count"] == 1


def test_qdrant_vector_store_uses_rest_api(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_request(method: str, url: str, **kwargs: Any) -> FakeResponse:
        calls.append({"method": method, "url": url, "kwargs": kwargs})
        if method == "GET" and url.endswith("/collections/docs"):
            return FakeResponse({"status": "not found"}, status_code=404)
        if method == "POST" and url.endswith("/collections/docs/points/search"):
            return FakeResponse(
                {
                    "result": [
                        {
                            "id": "point-1",
                            "score": 0.98,
                            "payload": {
                                "id": "ada",
                                "text": "Ada wrote notes.",
                                "metadata": {"topic": "computing"},
                            },
                        }
                    ]
                }
            )
        return FakeResponse({"result": {"ok": True}})

    monkeypatch.setattr("requests.request", fake_request)

    store = registry.get("ai_qdrant_vector_store").func(
        credentials={"url": "https://qdrant.test", "api_key": "qd-key"},
        collection="docs",
        create_if_missing=True,
    )
    assert isinstance(store, VectorStoreAdapter)
    store.upsert(
        documents=[
            Document(
                id="ada",
                text="Ada wrote notes.",
                metadata={"topic": "computing"},
            )
        ],
        embeddings=[[1.0, 0.0]],
    )
    docs = store.query(vector=[1.0, 0.0], top_k=1)
    registry.get("ai_vector_store_delete").func(
        store=store,
        document_ids='["ada"]',
    )

    assert [call["method"] for call in calls] == ["GET", "PUT", "PUT", "POST", "POST"]
    assert calls[1]["kwargs"]["json"]["vectors"]["size"] == 2
    assert calls[2]["kwargs"]["json"]["points"][0]["payload"]["id"] == "ada"
    assert calls[4]["url"].endswith("/collections/docs/points/delete?wait=true")
    assert calls[4]["kwargs"]["json"]["points"]
    assert docs[0].id == "ada"
    assert docs[0].score == 0.98
    assert docs[0].metadata["topic"] == "computing"


def test_rag_chain_uses_retrieved_context() -> None:
    embedding_model = ScriptedEmbeddingModel()
    store = registry.get("ai_in_memory_vector_store").func(namespace="test")
    registry.get("ai_vector_store_upsert").func(
        documents={"documents": [{"id": "ada", "text": "Ada wrote notes."}]},
        model=embedding_model,
        store=store,
    )
    retriever = registry.get("ai_vector_retriever_v2").func(
        model=embedding_model,
        store=store,
        top_k=1,
    )
    chat = ScriptedChatModel(
        [ChatResponse(text="Ada wrote notes [1].", provider="test", model="chat")]
    )

    output = registry.get("ai_rag_chain").func(
        input={"question": "What did Ada write?"},
        model=chat,
        retriever=retriever,
        top_k=1,
    )

    assert output["answer"] == "Ada wrote notes [1]."
    assert output["context"][0]["id"] == "ada"
    assert "Ada wrote notes." in chat.requests[0].messages[-1].content


async def test_rag_workflow_executes_through_engine() -> None:
    reg = NodeRegistry()
    for node_id in [
        "ai_text_document_loader",
        "ai_recursive_text_splitter",
        "ai_in_memory_vector_store",
        "ai_vector_store_upsert",
        "ai_vector_retriever_v2",
        "ai_rag_chain",
    ]:
        reg.register(registry.get(node_id))

    @node(
        name="Scripted Embedding",
        id="scripted_embedding",
        inputs=[],
        outputs=["model"],
        output_kinds={"model": "ai_embedding_model"},
        registry=reg,
    )
    def scripted_embedding() -> EmbeddingModelAdapter:
        return ScriptedEmbeddingModel()

    @node(
        name="Scripted Chat",
        id="scripted_chat",
        inputs=[],
        outputs=["model"],
        output_kinds={"model": "ai_language_model"},
        registry=reg,
    )
    def scripted_chat() -> ChatModelAdapter:
        return ScriptedChatModel(
            [ChatResponse(text="Ada wrote notes [1].", provider="test", model="chat")]
        )

    graph = WorkflowGraph(
        nodes=[
            GraphNode(
                id="loader",
                type="ai_text_document_loader",
                params={
                    "text": (
                        "Ada wrote notes about computation.\n\n"
                        "Grace built compiler systems."
                    ),
                    "document_id": "history",
                },
            ),
            GraphNode(
                id="splitter",
                type="ai_recursive_text_splitter",
                params={"chunk_size": 100, "chunk_overlap": 0},
            ),
            GraphNode(id="embed", type="scripted_embedding"),
            GraphNode(id="store", type="ai_in_memory_vector_store"),
            GraphNode(id="upsert", type="ai_vector_store_upsert"),
            GraphNode(
                id="retriever",
                type="ai_vector_retriever_v2",
                params={"top_k": 1},
            ),
            GraphNode(id="chat", type="scripted_chat"),
            GraphNode(
                id="rag",
                type="ai_rag_chain",
                params={"question": "What did Ada write?", "top_k": 1},
            ),
        ],
        edges=[
            Edge(
                source="loader",
                source_output="loader",
                target="splitter",
                target_input="loader",
            ),
            Edge(
                source="splitter",
                source_output="documents",
                target="upsert",
                target_input="documents",
            ),
            Edge(
                source="embed",
                source_output="model",
                target="upsert",
                target_input="model",
            ),
            Edge(
                source="store",
                source_output="store",
                target="upsert",
                target_input="store",
            ),
            Edge(
                source="embed",
                source_output="model",
                target="retriever",
                target_input="model",
            ),
            Edge(
                source="upsert",
                source_output="store",
                target="retriever",
                target_input="store",
            ),
            Edge(
                source="chat",
                source_output="model",
                target="rag",
                target_input="model",
            ),
            Edge(
                source="retriever",
                source_output="retriever",
                target="rag",
                target_input="retriever",
            ),
        ],
    )

    result = await execute(graph, reg)

    assert result.status == "success"
    assert result.nodes["rag"].outputs["main"]["answer"] == "Ada wrote notes [1]."
    assert result.nodes["rag"].outputs["main"]["context"][0]["id"].startswith("history")


# ---------------------------------------------------------------------------
# Memory supplier
# ---------------------------------------------------------------------------


def test_buffer_memory_returns_adapter_and_windows() -> None:
    from noodle.ai_runtime import MessageRole

    fn = registry.get("ai_buffer_memory").func
    adapter = fn(window=2)
    assert isinstance(adapter, MemoryAdapter)
    adapter.save(
        session_id="s1",
        messages=[
            AIMessage(role=MessageRole.user, content="a"),
            AIMessage(role=MessageRole.user, content="b"),
            AIMessage(role=MessageRole.user, content="c"),
        ],
    )
    loaded = adapter.load(session_id="s1")
    assert [m.content for m in loaded] == ["b", "c"]
    adapter.clear(session_id="s1")
    assert adapter.load(session_id="s1") == []


def test_buffer_memory_seeds_system_prompt() -> None:
    fn = registry.get("ai_buffer_memory").func
    adapter = fn(window=10, system_prompt="You are helpful.")
    seeded = adapter.load(session_id="__seed__")
    assert len(seeded) == 1
    assert seeded[0].content == "You are helpful."


# ---------------------------------------------------------------------------
# Tool suppliers
# ---------------------------------------------------------------------------


def test_http_tool_returns_adapter_with_schema() -> None:
    fn = registry.get("ai_http_tool").func
    adapter = fn(
        name="weather",
        description="Get weather",
        url="https://example.test/weather",
        method="GET",
        parameters_schema=(
            '{"type": "object", "properties": {"city": {"type": "string"}}, '
            '"required": ["city"]}'
        ),
    )
    assert isinstance(adapter, ToolAdapter)
    schema = adapter.schema
    assert schema.name == "weather"
    assert "city" in schema.parameters.properties
    assert schema.parameters.required == ["city"]
    assert adapter.side_effecting is False


def test_http_tool_marks_write_methods_side_effecting() -> None:
    fn = registry.get("ai_http_tool").func
    adapter = fn(
        name="writer",
        description="Write data",
        url="https://example.test/write",
        method="POST",
    )
    assert adapter.side_effecting is True


def test_http_tool_blocks_private_targets(monkeypatch) -> None:
    def fake_request(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("private target should be blocked before requests")

    monkeypatch.setattr("requests.request", fake_request)
    adapter = registry.get("ai_http_tool").func(
        name="metadata",
        description="Read metadata",
        url="http://169.254.169.254/latest/meta-data",
        method="GET",
    )
    try:
        adapter.invoke({})
    except ValueError as exc:
        assert "private" in str(exc)
    else:
        raise AssertionError("private AI HTTP target should be blocked")


def test_workflow_tool_returns_adapter() -> None:
    fn = registry.get("ai_workflow_tool").func
    adapter = fn(
        name="run_report",
        description="Run a report",
        workflow_id="wf-123",
    )
    assert isinstance(adapter, ToolAdapter)
    assert adapter.workflow_id == "wf-123"
    assert adapter.schema.name == "run_report"
    assert adapter.side_effecting is True


async def test_workflow_tool_invokes_host_workflow_caller() -> None:
    fn = registry.get("ai_workflow_tool").func
    adapter = fn(
        name="run_report",
        description="Run a report",
        workflow_id="wf-123",
    )
    calls: list[tuple[str, dict[str, Any]]] = []

    async def caller(workflow_id: str, input_value: Any) -> Any:
        calls.append((workflow_id, input_value))
        return {"ok": True, "input": input_value}

    token = workflow_caller.set(caller)
    try:
        result = await adapter.invoke_async({"account": "A1"})
    finally:
        workflow_caller.reset(token)

    assert calls == [("wf-123", {"account": "A1"})]
    assert json.loads(result) == {"ok": True, "input": {"account": "A1"}}


def test_tool_bundle_merges_tools() -> None:
    fn = registry.get("ai_tool_bundle").func
    tools = fn(tool_1=DummyTool("one"), tool_2=[DummyTool("two")])
    assert [tool.schema.name for tool in tools] == ["one", "two"]


# ---------------------------------------------------------------------------
# Agent v2 executable
# ---------------------------------------------------------------------------


def test_agent_v2_requests_tool_and_resumes_to_final() -> None:
    model = ScriptedChatModel(
        [
            ChatResponse(
                tool_calls=[
                    ToolCall(
                        id="call_1",
                        name="lookup",
                        arguments={"query": "Ada"},
                    )
                ],
                model="test-model",
                provider="test",
            ),
            ChatResponse(
                text='{"answer": "done"}',
                model="test-model",
                provider="test",
            ),
        ]
    )
    parser = registry.get("ai_structured_output_parser").func(
        schema='{"required": ["answer"]}'
    )
    fn = registry.get("ai_agent_v2").func

    request = fn(
        input={"task": "Find Ada"},
        model=model,
        tool=DummyTool("lookup"),
        parser=parser,
        response_format="json_object",
        side_effect_approval="auto_approve",
    )

    assert isinstance(request, AgentActionRequest)
    assert request.allow_side_effects is True
    assert request.tool_calls[0].name == "lookup"
    assert model.requests[0].tools[0].name == "lookup"
    assert model.requests[0].messages[-1].role == MessageRole.user
    assert request.messages_so_far[-1].tool_calls[0].id == "call_1"

    resume = AgentResumeInput(
        tool_results=[
            ToolResult(tool_call_id="call_1", name="lookup", content="Ada Lovelace")
        ],
        messages_so_far=request.messages_so_far,
        step=1,
        max_steps=4,
    )
    output = fn(
        input={"task": "Find Ada"},
        model=model,
        tool=DummyTool("lookup"),
        parser=parser,
        response_format="json_object",
        agent_resume=resume,
    )

    assert output["parsed"] == {"answer": "done"}
    assert output["answer"] == '{"answer": "done"}'
    assert model.requests[1].messages[-1].role == MessageRole.tool


def test_agent_v2_saves_final_response_to_memory() -> None:
    memory = registry.get("ai_buffer_memory").func(window=10)
    model = ScriptedChatModel(
        [ChatResponse(text="hello", model="test-model", provider="test")]
    )
    fn = registry.get("ai_agent_v2").func

    output = fn(
        input={"task": "Say hello", "session_id": "s1"},
        model=model,
        memory=memory,
    )

    saved = memory.load(session_id="s1")
    assert output["answer"] == "hello"
    assert [msg.role for msg in saved] == [MessageRole.user, MessageRole.assistant]


# ---------------------------------------------------------------------------
# Output parser supplier
# ---------------------------------------------------------------------------


def test_output_parser_parses_json() -> None:
    fn = registry.get("ai_structured_output_parser").func
    parser = fn(schema='{"properties": {"name": {}}, "required": ["name"]}')
    assert isinstance(parser, OutputParserAdapter)
    result = parser.parse('{"name": "Ada"}')
    assert result == {"name": "Ada"}


def test_output_parser_strips_fences() -> None:
    fn = registry.get("ai_structured_output_parser").func
    parser = fn(schema=None)
    result = parser.parse("```json\n{\"x\": 1}\n```")
    assert result == {"x": 1}


def test_output_parser_rejects_missing_required_keys() -> None:
    import pytest

    fn = registry.get("ai_structured_output_parser").func
    parser = fn(schema='{"required": ["name"]}')
    with pytest.raises(ValueError, match="missing required keys"):
        parser.parse('{"other": 1}')


def test_output_parser_format_instructions() -> None:
    fn = registry.get("ai_structured_output_parser").func
    parser = fn(schema='{"properties": {"name": {}, "age": {}}, "required": ["name"]}')
    instr = parser.format_instructions
    assert "name" in instr
    assert "age" in instr


# ---------------------------------------------------------------------------
# Guardrail supplier
# ---------------------------------------------------------------------------


def test_guardrail_blocks_terms() -> None:
    import pytest

    from noodle.ai_runtime import ChatResponse

    fn = registry.get("ai_guardrail").func
    guard = fn(blocked_terms='["secret"]')
    assert isinstance(guard, GuardrailAdapter)
    debug: dict[str, Any] = {}
    token = node_debug.set(debug)
    try:
        with pytest.raises(ValueError, match="blocked term"):
            guard.check(ChatResponse(text="this is a secret"))
    finally:
        node_debug.reset(token)
    assert debug["guardrail_events"] == [
        {
            "type": "guardrail_blocked",
            "adapter": "keyword_guardrail",
            "reason": "blocked_term",
        }
    ]
    assert "secret" not in json.dumps(debug).lower()


def test_guardrail_redacts_patterns() -> None:
    from noodle.ai_runtime import ChatResponse

    fn = registry.get("ai_guardrail").func
    guard = fn(redact_patterns='["\\\\d{3}-\\\\d{2}-\\\\d{4}"]')
    debug: dict[str, Any] = {}
    token = node_debug.set(debug)
    try:
        out = guard.check(ChatResponse(text="ssn is 123-45-6789 ok"))
    finally:
        node_debug.reset(token)
    assert "[REDACTED]" in out.text
    assert "123-45-6789" not in out.text
    assert debug["guardrail_events"] == [
        {
            "type": "guardrail_redacted",
            "adapter": "keyword_guardrail",
            "replacement_count": 1,
        }
    ]


def test_guardrail_enforces_max_length() -> None:
    import pytest

    from noodle.ai_runtime import ChatResponse

    fn = registry.get("ai_guardrail").func
    guard = fn(max_length=5)
    with pytest.raises(ValueError, match="max length"):
        guard.check(ChatResponse(text="way too long"))


def test_guardrail_passes_clean_response() -> None:
    from noodle.ai_runtime import ChatResponse

    fn = registry.get("ai_guardrail").func
    guard = fn(blocked_terms='["bad"]')
    resp = ChatResponse(text="all good here")
    assert guard.check(resp).text == "all good here"
