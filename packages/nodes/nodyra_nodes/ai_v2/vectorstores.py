"""AI v2 vector store nodes for RAG workflows."""

from __future__ import annotations

import json
import math
import re
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from nodyra.ai_runtime import (
    Document,
    EmbeddingModelAdapter,
    EmbeddingRequest,
    RetrievedDocument,
    VectorStoreAdapter,
)
from nodyra.sdk import node
from nodyra_nodes._creds import cred_multi
from nodyra_nodes.http_security import safe_request

AI_CATEGORY = "AI"
QDRANT_CREDENTIAL_FIELDS = ["url", "api_key"]
MAX_VECTOR_DOCUMENTS = 5_000
MAX_VECTOR_TOP_K = 100


def documents_from_value(
    value: Any,
    *,
    text_field: str = "text",
    max_documents: int = MAX_VECTOR_DOCUMENTS,
) -> list[Document]:
    """Coerce splitter output, raw lists, or document models into documents."""
    raw = value
    if isinstance(value, dict):
        raw = value.get("documents") or value.get("rows") or value.get("items") or value
    if isinstance(raw, Document):
        return [raw]
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        if raw is None:
            return []
        raw = [{"text": str(raw)}]

    documents: list[Document] = []
    limit = max(1, min(MAX_VECTOR_DOCUMENTS, int(max_documents or MAX_VECTOR_DOCUMENTS)))
    for index, item in enumerate(raw):
        if len(documents) >= limit:
            raise ValueError(
                f"ai vector documents exceed max_documents={limit}; "
                "split or filter upstream"
            )
        if isinstance(item, Document):
            documents.append(item)
            continue
        if isinstance(item, dict):
            text = item.get(text_field) or item.get("text") or item.get("content") or ""
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            doc_id = str(item.get("id") or f"doc-{index}")
            documents.append(
                Document(
                    id=doc_id,
                    text=str(text),
                    metadata={str(k): v for k, v in metadata.items()},
                )
            )
            continue
        documents.append(Document(id=f"doc-{index}", text=str(item)))
    return [document for document in documents if document.text]


def _top_k(value: int) -> int:
    return max(1, min(MAX_VECTOR_TOP_K, int(value or 5)))


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class InMemoryVectorStoreAdapter(VectorStoreAdapter):
    """Simple per-run vector store for local RAG workflows and tests."""

    def __init__(self, *, namespace: str = "default") -> None:
        self._namespace = namespace or "default"
        self._records: dict[str, tuple[Document, list[float]]] = {}

    @property
    def count(self) -> int:
        return len(self._records)

    def upsert(
        self,
        *,
        documents: list[Document],
        embeddings: list[list[float]],
    ) -> None:
        if len(documents) != len(embeddings):
            raise ValueError(
                "in-memory vector store: documents and embeddings length mismatch"
            )
        for index, (document, embedding) in enumerate(
            zip(documents, embeddings, strict=True)
        ):
            doc_id = document.id or f"doc-{index}"
            clean_embedding = [float(value) for value in embedding]
            self._records[doc_id] = (
                document.model_copy(update={"id": doc_id}),
                clean_embedding,
            )

    def query(self, *, vector: list[float], top_k: int = 5) -> list[RetrievedDocument]:
        scored = [
            (
                _cosine(vector, embedding),
                document,
            )
            for document, embedding in self._records.values()
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        limit = _top_k(top_k)
        return [
            RetrievedDocument(
                id=document.id,
                text=document.text,
                metadata=document.metadata,
                score=float(score),
            )
            for score, document in scored[:limit]
        ]

    def delete(self, *, ids: list[str]) -> None:
        for doc_id in ids:
            self._records.pop(doc_id, None)

    def as_config(self) -> dict[str, Any]:
        return {
            "adapter": "in_memory_vector_store",
            "namespace": self._namespace,
            "count": self.count,
        }


class QdrantVectorStoreAdapter(VectorStoreAdapter):
    """Qdrant-backed vector store using the REST API."""

    def __init__(
        self,
        *,
        url: str,
        api_key: str = "",
        collection: str,
        create_if_missing: bool = True,
        distance: str = "Cosine",
        timeout_seconds: int = 30,
    ) -> None:
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._collection = collection
        self._create_if_missing = bool(create_if_missing)
        self._distance = distance or "Cosine"
        self._timeout = int(timeout_seconds or 30)

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["api-key"] = self._api_key
        return headers

    def _target_url(self, path: str) -> str:
        if not self._url:
            raise ValueError("qdrant vector store: url is required")
        return f"{self._url}{path}"

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict:
        if not self._collection:
            raise ValueError("qdrant vector store: collection is required")
        # SEC-2: self._url comes from user credentials — the guard validates the
        # target (and every redirect hop).
        response = safe_request(
            method,
            self._target_url(path),
            headers=self._headers,
            json=payload,
            timeout=max(1, min(300, self._timeout)),
            context="qdrant vector store",
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"qdrant vector store: HTTP {response.status_code}: "
                f"{response.text[:800]}"
            )
        try:
            body = response.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}

    def _collection_exists(self) -> bool:
        response = safe_request(
            "GET",
            self._target_url(f"/collections/{self._collection}"),
            headers=self._headers,
            timeout=max(1, min(300, self._timeout)),
            context="qdrant vector store",
        )
        if response.status_code == 404:
            return False
        if response.status_code >= 400:
            raise RuntimeError(
                f"qdrant vector store: HTTP {response.status_code}: "
                f"{response.text[:800]}"
            )
        return True

    def _ensure_collection(self, dimension: int) -> None:
        if not self._create_if_missing:
            return
        if self._collection_exists():
            return
        self._request(
            "PUT",
            f"/collections/{self._collection}",
            {
                "vectors": {
                    "size": int(dimension),
                    "distance": self._distance,
                }
            },
        )

    def _point_id(self, doc_id: str) -> str:
        return str(uuid5(NAMESPACE_URL, f"{self._collection}:{doc_id}"))

    def upsert(
        self,
        *,
        documents: list[Document],
        embeddings: list[list[float]],
    ) -> None:
        if len(documents) != len(embeddings):
            raise ValueError("qdrant vector store: documents and embeddings length mismatch")
        if not documents:
            return
        first_dimension = len(embeddings[0])
        if first_dimension <= 0:
            raise ValueError("qdrant vector store: embedding vectors are empty")
        self._ensure_collection(first_dimension)
        points: list[dict[str, Any]] = []
        for document, embedding in zip(documents, embeddings, strict=True):
            doc_id = document.id or str(uuid5(NAMESPACE_URL, document.text))
            points.append(
                {
                    "id": self._point_id(doc_id),
                    "vector": [float(value) for value in embedding],
                    "payload": {
                        "id": doc_id,
                        "text": document.text,
                        "metadata": document.metadata,
                    },
                }
            )
        self._request(
            "PUT",
            f"/collections/{self._collection}/points?wait=true",
            {"points": points},
        )

    def delete(self, *, ids: list[str]) -> None:
        point_ids = [self._point_id(doc_id) for doc_id in ids if doc_id]
        if not point_ids:
            return
        self._request(
            "POST",
            f"/collections/{self._collection}/points/delete?wait=true",
            {"points": point_ids},
        )

    def query(self, *, vector: list[float], top_k: int = 5) -> list[RetrievedDocument]:
        body = self._request(
            "POST",
            f"/collections/{self._collection}/points/search",
            {
                "vector": [float(value) for value in vector],
                "limit": _top_k(top_k),
                "with_payload": True,
            },
        )
        matches = body.get("result")
        if not isinstance(matches, list):
            matches = []
        documents: list[RetrievedDocument] = []
        for match in matches:
            if not isinstance(match, dict):
                continue
            payload = match.get("payload") if isinstance(match.get("payload"), dict) else {}
            metadata = (
                payload.get("metadata")
                if isinstance(payload.get("metadata"), dict)
                else {}
            )
            documents.append(
                RetrievedDocument(
                    id=str(payload.get("id") or match.get("id") or ""),
                    text=str(payload.get("text") or ""),
                    metadata={str(k): v for k, v in metadata.items()},
                    score=float(match.get("score") or 0.0),
                )
            )
        return documents

    def as_config(self) -> dict[str, Any]:
        return {
            "adapter": "qdrant_vector_store",
            "url": self._url,
            "collection": self._collection,
            "create_if_missing": self._create_if_missing,
            "distance": self._distance,
        }


def _embedding_model_name(model: EmbeddingModelAdapter) -> str:
    try:
        config = model.as_config()
    except Exception:  # noqa: BLE001 - adapter config is optional
        return ""
    return str(config.get("model") or "")


def _document_ids(input_value: Any, configured: Any) -> list[str]:
    raw = configured
    if isinstance(raw, str) and raw.strip():
        stripped = raw.strip()
        if stripped.startswith("["):
            try:
                loaded = json.loads(stripped)
            except ValueError:
                loaded = None
            if isinstance(loaded, list):
                return [str(item) for item in loaded if str(item)]
        return [item.strip() for item in re.split(r"[\n,]+", stripped) if item.strip()]
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item)]

    value = input_value
    if isinstance(value, dict):
        value = value.get("ids") or value.get("documents") or value.get("rows") or value
    if isinstance(value, dict):
        value = [value]
    if isinstance(value, list):
        ids: list[str] = []
        for item in value:
            if isinstance(item, dict):
                doc_id = item.get("id") or item.get("document_id")
                if doc_id:
                    ids.append(str(doc_id))
            elif item:
                ids.append(str(item))
        return ids
    return []


@node(
    name="AI In-Memory Vector Store",
    id="ai_in_memory_vector_store",
    category=AI_CATEGORY,
    role="supplier",
    icon="database",
    inputs=[],
    outputs=["store"],
    output_kinds={"store": "ai_vector_store"},
    params={
        "namespace": {
            "description": "Logical namespace label for this per-run store.",
        },
    },
)
def ai_in_memory_vector_store(namespace: str = "default") -> VectorStoreAdapter:
    """Supply an empty in-process vector store."""
    return InMemoryVectorStoreAdapter(namespace=namespace or "default")


@node(
    name="AI Qdrant Vector Store",
    id="ai_qdrant_vector_store",
    category=AI_CATEGORY,
    role="supplier",
    icon="database",
    inputs=[],
    outputs=["store"],
    output_kinds={"store": "ai_vector_store"},
    param_groups={"Options": ["create_if_missing", "distance", "timeout_seconds"]},
    params={
        "credentials": {
            **cred_multi("qdrant", "Qdrant credentials", QDRANT_CREDENTIAL_FIELDS),
            "description": "Qdrant URL and API key.",
        },
        "collection": {
            "description": "Qdrant collection name.",
        },
        "create_if_missing": {
            "description": "Create the collection on first upsert if absent.",
            "group": "Options",
        },
        "distance": {
            "choices": ["Cosine", "Dot", "Euclid", "Manhattan"],
            "description": "Distance metric used when creating the collection.",
            "group": "Options",
        },
        "timeout_seconds": {
            "description": "Qdrant HTTP timeout in seconds.",
            "group": "Options",
        },
    },
)
def ai_qdrant_vector_store(
    credentials: dict | None = None,
    collection: str = "",
    create_if_missing: bool = True,
    distance: str = "Cosine",
    timeout_seconds: int = 30,
) -> VectorStoreAdapter:
    """Supply a Qdrant-backed vector store."""
    creds = credentials if isinstance(credentials, dict) else {}
    return QdrantVectorStoreAdapter(
        url=str(creds.get("url") or ""),
        api_key=str(creds.get("api_key") or ""),
        collection=collection,
        create_if_missing=bool(create_if_missing),
        distance=distance or "Cosine",
        timeout_seconds=int(timeout_seconds or 30),
    )


@node(
    name="AI Vector Store Upsert",
    id="ai_vector_store_upsert",
    category=AI_CATEGORY,
    icon="database",
    inputs=["documents", "model", "store"],
    input_kinds={
        "documents": "main",
        "model": "ai_embedding_model",
        "store": "ai_vector_store",
    },
    outputs=["store"],
    output_kinds={"store": "ai_vector_store"},
    param_groups={"Options": ["text_field", "batch_size", "max_documents", "timeout_seconds"]},
    params={
        "text_field": {
            "description": "Field to embed when input documents are plain dict rows.",
            "group": "Options",
        },
        "batch_size": {
            "description": "Documents embedded per provider call.",
            "group": "Options",
        },
        "max_documents": {
            "description": "Maximum documents to embed/upsert, hard max 5000.",
            "group": "Options",
        },
        "timeout_seconds": {
            "description": "Embedding request timeout per batch.",
            "group": "Options",
        },
    },
)
def ai_vector_store_upsert(
    documents: Any = None,
    model: Any = None,
    store: Any = None,
    text_field: str = "text",
    batch_size: int = 64,
    max_documents: int = MAX_VECTOR_DOCUMENTS,
    timeout_seconds: int = 60,
) -> VectorStoreAdapter:
    """Embed documents and upsert them into a vector store."""
    if not isinstance(model, EmbeddingModelAdapter):
        raise ValueError("ai_vector_store_upsert: connect an AI Embedding Model")
    if not isinstance(store, VectorStoreAdapter):
        raise ValueError("ai_vector_store_upsert: connect an AI Vector Store")
    docs = documents_from_value(
        documents,
        text_field=text_field or "text",
        max_documents=max_documents,
    )
    if not docs:
        return store
    batch = max(1, min(256, int(batch_size or 64)))
    embeddings: list[list[float]] = []
    model_name = _embedding_model_name(model)
    for start in range(0, len(docs), batch):
        response = model.embed(
            EmbeddingRequest(
                texts=[document.text for document in docs[start : start + batch]],
                model=model_name,
                timeout_seconds=int(timeout_seconds or 60),
            )
        )
        embeddings.extend(response.embeddings)
    if len(embeddings) != len(docs):
        raise RuntimeError(
            "ai_vector_store_upsert: embedding provider returned "
            f"{len(embeddings)} embeddings for {len(docs)} documents"
        )
    store.upsert(documents=docs, embeddings=embeddings)
    return store


@node(
    name="AI Vector Store Delete",
    id="ai_vector_store_delete",
    category=AI_CATEGORY,
    icon="archive",
    inputs=["input", "store"],
    input_kinds={
        "input": "main",
        "store": "ai_vector_store",
    },
    outputs=["store"],
    output_kinds={"store": "ai_vector_store"},
    params={
        "document_ids": {
            "widget": "textarea",
            "description": "Document ids to delete. Supports JSON array, comma list, or lines.",
        },
    },
)
def ai_vector_store_delete(
    input: Any = None,
    store: Any = None,
    document_ids: Any = None,
) -> VectorStoreAdapter:
    """Delete document ids from a vector store."""
    if not isinstance(store, VectorStoreAdapter):
        raise ValueError("ai_vector_store_delete: connect an AI Vector Store")
    ids = _document_ids(input, document_ids)
    if ids:
        store.delete(ids=ids)
    return store
