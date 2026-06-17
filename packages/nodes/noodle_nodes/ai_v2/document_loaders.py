"""AI v2 document loader supplier nodes for RAG workflows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests

from noodle.ai_runtime import Document, DocumentLoaderAdapter
from noodle.sdk import node
from noodle_nodes.http_security import assert_public_http_url

AI_CATEGORY = "AI"
MAX_AI_DOCUMENT_CHARS = 1_000_000
MAX_AI_DOCUMENT_BYTES = 4_000_000


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    if isinstance(value, str) and value.strip():
        try:
            loaded = json.loads(value)
        except ValueError:
            return {}
        if isinstance(loaded, dict):
            return {str(k): v for k, v in loaded.items()}
    return {}


def _limit_document_text(text: str, *, label: str) -> str:
    if len(text) > MAX_AI_DOCUMENT_CHARS:
        raise ValueError(
            f"{label}: document has {len(text)} chars; "
            f"limit is {MAX_AI_DOCUMENT_CHARS}"
        )
    return text


class TextDocumentLoaderAdapter(DocumentLoaderAdapter):
    """Loads a single configured text document."""

    def __init__(
        self,
        *,
        text: str,
        document_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._text = text
        self._document_id = document_id or "text"
        self._metadata = dict(metadata or {})

    def load(self) -> list[Document]:
        if not self._text:
            return []
        text = _limit_document_text(self._text, label="text document loader")
        return [
            Document(
                id=self._document_id,
                text=text,
                metadata={**self._metadata, "source": self._document_id},
            )
        ]


class FileDocumentLoaderAdapter(DocumentLoaderAdapter):
    """Loads one UTF-8-compatible text file from disk."""

    def __init__(
        self,
        *,
        path: str,
        encoding: str = "utf-8",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._path = path
        self._encoding = encoding or "utf-8"
        self._metadata = dict(metadata or {})

    def load(self) -> list[Document]:
        if not self._path:
            raise ValueError("file document loader: path is required")
        path = Path(self._path).expanduser()
        if not path.exists() or not path.is_file():
            raise ValueError(f"file document loader: file not found: {self._path}")
        size = path.stat().st_size
        if size > MAX_AI_DOCUMENT_BYTES:
            raise ValueError(
                f"file document loader: file is {size} bytes; "
                f"limit is {MAX_AI_DOCUMENT_BYTES}"
            )
        text = _limit_document_text(
            path.read_text(encoding=self._encoding, errors="replace"),
            label="file document loader",
        )
        return [
            Document(
                id=str(path),
                text=text,
                metadata={
                    **self._metadata,
                    "source": str(path),
                    "file_name": path.name,
                },
            )
        ]


class UrlDocumentLoaderAdapter(DocumentLoaderAdapter):
    """Loads a URL body as text."""

    def __init__(
        self,
        *,
        url: str,
        timeout_seconds: int = 30,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._url = url
        self._timeout = int(timeout_seconds or 30)
        self._metadata = dict(metadata or {})

    def load(self) -> list[Document]:
        if not self._url:
            raise ValueError("url document loader: url is required")
        assert_public_http_url(self._url, context="url document loader")
        response = requests.get(
            self._url,
            timeout=max(1, min(300, self._timeout)),
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"url document loader: HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
        text = _limit_document_text(response.text, label="url document loader")
        return [
            Document(
                id=self._url,
                text=text,
                metadata={
                    **self._metadata,
                    "source": self._url,
                    "status_code": response.status_code,
                    "content_type": response.headers.get("content-type", ""),
                },
            )
        ]


@node(
    name="AI Text Document Loader",
    id="ai_text_document_loader",
    category=AI_CATEGORY,
    role="supplier",
    icon="page",
    inputs=[],
    outputs=["loader"],
    output_kinds={"loader": "ai_document_loader"},
    params={
        "text": {
            "widget": "textarea",
            "description": "Document text to load.",
        },
        "document_id": {
            "description": "Stable id/source label for the document.",
        },
        "metadata": {
            "widget": "code",
            "description": "Optional JSON object merged into document metadata.",
        },
    },
)
def ai_text_document_loader(
    text: str = "",
    document_id: str = "text",
    metadata: Any = None,
) -> DocumentLoaderAdapter:
    """Supply a single text document to RAG ingestion nodes."""
    return TextDocumentLoaderAdapter(
        text=text,
        document_id=document_id or "text",
        metadata=_metadata(metadata),
    )


@node(
    name="AI File Document Loader",
    id="ai_file_document_loader",
    category=AI_CATEGORY,
    role="supplier",
    icon="page",
    inputs=[],
    outputs=["loader"],
    output_kinds={"loader": "ai_document_loader"},
    param_groups={"Options": ["encoding", "metadata"]},
    params={
        "path": {
            "description": "Path to a local text/markdown file readable by the runner.",
        },
        "encoding": {
            "description": "Text encoding used to read the file.",
            "group": "Options",
        },
        "metadata": {
            "widget": "code",
            "description": "Optional JSON object merged into document metadata.",
            "group": "Options",
        },
    },
)
def ai_file_document_loader(
    path: str = "",
    encoding: str = "utf-8",
    metadata: Any = None,
) -> DocumentLoaderAdapter:
    """Supply a local file loader to RAG ingestion nodes."""
    return FileDocumentLoaderAdapter(
        path=path,
        encoding=encoding or "utf-8",
        metadata=_metadata(metadata),
    )


@node(
    name="AI URL Document Loader",
    id="ai_url_document_loader",
    category=AI_CATEGORY,
    role="supplier",
    icon="globe",
    inputs=[],
    outputs=["loader"],
    output_kinds={"loader": "ai_document_loader"},
    param_groups={"Options": ["timeout_seconds", "metadata"]},
    params={
        "url": {
            "description": "HTTP(S) URL to load as text.",
        },
        "timeout_seconds": {
            "description": "HTTP timeout in seconds.",
            "group": "Options",
        },
        "metadata": {
            "widget": "code",
            "description": "Optional JSON object merged into document metadata.",
            "group": "Options",
        },
    },
)
def ai_url_document_loader(
    url: str = "",
    timeout_seconds: int = 30,
    metadata: Any = None,
) -> DocumentLoaderAdapter:
    """Supply a URL text loader to RAG ingestion nodes."""
    return UrlDocumentLoaderAdapter(
        url=url,
        timeout_seconds=int(timeout_seconds or 30),
        metadata=_metadata(metadata),
    )
