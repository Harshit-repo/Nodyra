"""Tests for additional AI integration nodes."""

from __future__ import annotations

import pytest

from nodyra_nodes.ai_extra import pinecone_query, pinecone_upsert
from nodyra_nodes.http_security import UnsafeHttpTargetError


def test_pinecone_query_rejects_private_index_host() -> None:
    with pytest.raises(UnsafeHttpTargetError):
        pinecone_query(
            credentials={"api_key": "pc-test", "index_host": "127.0.0.1:8080"},
            vector_json="[0.1, 0.2]",
        )


def test_pinecone_upsert_rejects_url_like_index_host() -> None:
    with pytest.raises(ValueError, match="index_host"):
        pinecone_upsert(
            credentials={"api_key": "pc-test", "index_host": "https://example.com"},
            vector_id="v1",
            values_json="[0.1, 0.2]",
        )
