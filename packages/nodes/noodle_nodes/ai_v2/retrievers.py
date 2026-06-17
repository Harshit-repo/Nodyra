"""AI v2 retriever and RAG chain nodes."""

from __future__ import annotations

import json
from typing import Any

from noodle.ai_runtime import (
    AIMessage,
    ChatModelAdapter,
    ChatRequest,
    EmbeddingModelAdapter,
    EmbeddingRequest,
    RetrievedDocument,
    RetrieverAdapter,
    VectorStoreAdapter,
)
from noodle.sdk import node

AI_CATEGORY = "AI"
MAX_RETRIEVER_TOP_K = 100


def _top_k(value: int) -> int:
    return max(1, min(MAX_RETRIEVER_TOP_K, int(value or 5)))


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict | list):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _query_text(input_value: Any, query: str) -> str:
    if query.strip():
        return query.strip()
    if isinstance(input_value, dict):
        for key in ("query", "question", "prompt", "text", "input"):
            value = input_value.get(key)
            if value:
                return _as_text(value).strip()
    return _as_text(input_value).strip()


def _model_name(model: ChatModelAdapter | EmbeddingModelAdapter) -> str:
    try:
        config = model.as_config()
    except Exception:  # noqa: BLE001 - adapter config is optional
        return ""
    return str(config.get("model") or config.get("deployment") or "")


def _docs_json(documents: list[RetrievedDocument]) -> list[dict[str, Any]]:
    return [document.model_dump(mode="json") for document in documents]


class VectorStoreRetrieverAdapter(RetrieverAdapter):
    """Embeds text queries and retrieves nearest documents from a vector store."""

    def __init__(
        self,
        *,
        model: EmbeddingModelAdapter,
        store: VectorStoreAdapter,
        top_k: int = 5,
        timeout_seconds: int = 60,
    ) -> None:
        self._model = model
        self._store = store
        self._top_k = _top_k(top_k)
        self._timeout = int(timeout_seconds or 60)

    def retrieve(self, query: str, *, top_k: int = 5) -> list[RetrievedDocument]:
        query_text = query.strip()
        if not query_text:
            return []
        response = self._model.embed(
            EmbeddingRequest(
                texts=[query_text],
                model=_model_name(self._model),
                timeout_seconds=self._timeout,
            )
        )
        if not response.embeddings:
            return []
        return self._store.query(
            vector=response.embeddings[0],
            top_k=_top_k(top_k or self._top_k),
        )

    def as_config(self) -> dict[str, Any]:
        return {
            "adapter": "vector_store_retriever",
            "top_k": self._top_k,
            "timeout_seconds": self._timeout,
        }


@node(
    name="AI Vector Retriever v2",
    id="ai_vector_retriever_v2",
    category=AI_CATEGORY,
    role="supplier",
    icon="eye",
    inputs=["model", "store"],
    input_kinds={
        "model": "ai_embedding_model",
        "store": "ai_vector_store",
    },
    outputs=["retriever"],
    output_kinds={"retriever": "ai_retriever"},
    param_groups={"Options": ["timeout_seconds"]},
    params={
        "top_k": {
            "description": "Default number of documents to retrieve.",
        },
        "timeout_seconds": {
            "description": "Embedding request timeout for queries.",
            "group": "Options",
        },
    },
)
def ai_vector_retriever_v2(
    model: Any = None,
    store: Any = None,
    top_k: int = 5,
    timeout_seconds: int = 60,
) -> RetrieverAdapter:
    """Supply a vector-store-backed retriever to RAG nodes."""
    if not isinstance(model, EmbeddingModelAdapter):
        raise ValueError("ai_vector_retriever_v2: connect an AI Embedding Model")
    if not isinstance(store, VectorStoreAdapter):
        raise ValueError("ai_vector_retriever_v2: connect an AI Vector Store")
    return VectorStoreRetrieverAdapter(
        model=model,
        store=store,
        top_k=_top_k(top_k),
        timeout_seconds=int(timeout_seconds or 60),
    )


@node(
    name="AI Retrieve Documents",
    id="ai_retrieve_documents",
    category=AI_CATEGORY,
    icon="eye",
    inputs=["input", "retriever"],
    input_kinds={
        "input": "main",
        "retriever": "ai_retriever",
    },
    outputs=["documents"],
    output_kinds={"documents": "main"},
    params={
        "query": {
            "widget": "textarea",
            "description": "Search query. Blank uses input.query, input.question, or input.",
        },
        "top_k": {
            "description": "Number of documents to retrieve.",
        },
    },
)
def ai_retrieve_documents(
    input: Any = None,
    retriever: Any = None,
    query: str = "",
    top_k: int = 5,
) -> dict[str, Any]:
    """Retrieve documents for inspection or downstream non-chat nodes."""
    if not isinstance(retriever, RetrieverAdapter):
        raise ValueError("ai_retrieve_documents: connect an AI Retriever")
    q = _query_text(input, query)
    documents = retriever.retrieve(q, top_k=_top_k(top_k))
    return {"query": q, "documents": _docs_json(documents), "count": len(documents)}


@node(
    name="AI RAG Chain",
    id="ai_rag_chain",
    category=AI_CATEGORY,
    icon="sparkles",
    inputs=["input", "model", "retriever"],
    input_kinds={
        "input": "main",
        "model": "ai_language_model",
        "retriever": "ai_retriever",
    },
    outputs=["main"],
    output_kinds={"main": "main"},
    param_groups={
        "Options": ["top_k", "max_context_chars", "system", "temperature", "max_tokens"]
    },
    params={
        "question": {
            "widget": "textarea",
            "description": "Question. Blank uses input.query, input.question, or input.",
        },
        "top_k": {
            "description": "Number of context documents to retrieve.",
            "group": "Options",
        },
        "max_context_chars": {
            "description": "Maximum context characters sent to the model.",
            "group": "Options",
        },
        "system": {
            "widget": "textarea",
            "description": "Optional system instruction.",
            "group": "Options",
        },
        "temperature": {
            "description": "Sampling temperature.",
            "group": "Options",
        },
        "max_tokens": {
            "description": "Maximum response tokens.",
            "group": "Options",
        },
    },
)
def ai_rag_chain(
    input: Any = None,
    model: Any = None,
    retriever: Any = None,
    question: str = "",
    top_k: int = 5,
    max_context_chars: int = 12000,
    system: str = "",
    temperature: float = 0.2,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Answer a question using retrieved documents as context."""
    if not isinstance(model, ChatModelAdapter):
        raise ValueError("ai_rag_chain: connect an AI Chat Model")
    if not isinstance(retriever, RetrieverAdapter):
        raise ValueError("ai_rag_chain: connect an AI Retriever")
    q = _query_text(input, question)
    if not q:
        raise ValueError("ai_rag_chain: question is required")
    docs = retriever.retrieve(q, top_k=_top_k(top_k))
    budget = max(1000, min(100000, int(max_context_chars or 12000)))
    context_parts: list[str] = []
    used = 0
    for idx, document in enumerate(docs):
        piece = f"[{idx + 1}] {document.text}"
        if used + len(piece) > budget:
            break
        context_parts.append(piece)
        used += len(piece)
    default_system = (
        "Answer using only the supplied context. If the context is insufficient, "
        "say what is missing. Cite context numbers in brackets."
    )
    context = "\n\n".join(context_parts)
    prompt = f"Question:\n{q}\n\nContext:\n{context}"
    response = model.complete(
        ChatRequest(
            messages=[
                AIMessage.system(system.strip() or default_system),
                AIMessage.user(prompt),
            ],
            model=_model_name(model),
            temperature=float(temperature),
            max_tokens=max_tokens,
        )
    )
    return {
        "answer": response.text,
        "question": q,
        "context": _docs_json(docs[: len(context_parts)]),
        "usage": response.usage.model_dump(mode="json"),
        "provider": response.provider,
        "model": response.model,
    }
