"""AI v2 text splitter nodes for RAG ingestion."""

from __future__ import annotations

from typing import Any

from noodle.ai_runtime import Document, DocumentLoaderAdapter
from noodle.sdk import node

AI_CATEGORY = "AI"

_DEFAULT_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]
MAX_RECURSIVE_SPLITTER_CHUNKS = 10_000


def _merge_parts(parts: list[str], *, separator: str, chunk_size: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    joiner = separator
    for part in parts:
        piece = part if not current else f"{joiner}{part}"
        if len(current) + len(piece) <= chunk_size:
            current = f"{current}{piece}"
            continue
        if current:
            chunks.append(current.strip())
        current = part
    if current:
        chunks.append(current.strip())
    return [chunk for chunk in chunks if chunk]


def _hard_split(text: str, *, chunk_size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        chunks.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(0, end - overlap)
    return [chunk for chunk in chunks if chunk]


def _split_text(text: str, *, chunk_size: int, overlap: int) -> list[str]:
    pending = [text.strip()]
    for separator in _DEFAULT_SEPARATORS:
        next_pending: list[str] = []
        finished: list[str] = []
        for segment in pending:
            if len(segment) <= chunk_size:
                finished.append(segment)
                continue
            if not separator:
                finished.extend(
                    _hard_split(segment, chunk_size=chunk_size, overlap=overlap)
                )
                continue
            pieces = [piece for piece in segment.split(separator) if piece.strip()]
            if len(pieces) <= 1:
                next_pending.append(segment)
                continue
            merged = _merge_parts(
                pieces,
                separator=separator,
                chunk_size=chunk_size,
            )
            for chunk in merged:
                if len(chunk) <= chunk_size:
                    finished.append(chunk)
                else:
                    next_pending.append(chunk)
        pending = [*finished, *next_pending]
    return pending


def _serialize_documents(documents: list[Document]) -> list[dict[str, Any]]:
    return [document.model_dump(mode="json") for document in documents]


@node(
    name="AI Recursive Text Splitter",
    id="ai_recursive_text_splitter",
    category=AI_CATEGORY,
    icon="branch",
    inputs=["loader"],
    input_kinds={"loader": "ai_document_loader"},
    outputs=["documents"],
    output_kinds={"documents": "main"},
    param_groups={"Options": ["chunk_overlap", "max_chunks"]},
    params={
        "chunk_size": {
            "description": "Maximum characters per chunk.",
        },
        "chunk_overlap": {
            "description": "Characters repeated across hard-split chunks.",
            "group": "Options",
        },
        "max_chunks": {
            "description": "Maximum chunks to emit, hard max 10000.",
            "group": "Options",
        },
    },
)
def ai_recursive_text_splitter(
    loader: Any = None,
    chunk_size: int = 1200,
    chunk_overlap: int = 120,
    max_chunks: int = 5000,
) -> dict[str, Any]:
    """Load and split documents into RAG chunks."""
    if not isinstance(loader, DocumentLoaderAdapter):
        raise ValueError("ai_recursive_text_splitter: connect an AI Document Loader")
    size = max(100, min(20000, int(chunk_size or 1200)))
    overlap = max(0, min(size - 1, int(chunk_overlap or 0)))
    chunk_limit = max(1, min(MAX_RECURSIVE_SPLITTER_CHUNKS, int(max_chunks or 5000)))
    chunks: list[Document] = []
    for doc_index, document in enumerate(loader.load()):
        for chunk_index, text in enumerate(
            _split_text(document.text, chunk_size=size, overlap=overlap)
        ):
            if len(chunks) >= chunk_limit:
                raise ValueError(
                    "ai_recursive_text_splitter: chunk count exceeds "
                    f"max_chunks={chunk_limit}"
                )
            chunk_id = f"{document.id or f'doc-{doc_index}'}:chunk-{chunk_index}"
            chunks.append(
                Document(
                    id=chunk_id,
                    text=text,
                    metadata={
                        **document.metadata,
                        "source_document_id": document.id,
                        "chunk_index": chunk_index,
                        "chunk_char_count": len(text),
                    },
                )
            )
    return {"documents": _serialize_documents(chunks), "count": len(chunks)}
