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

@pytest.fixture(autouse=True)
def _blocked_egress_posture(monkeypatch):
    """These tests verify the BLOCKED posture of nodyra_nodes.http_security;
    single-tenant API processes default to allowing private egress (mirroring
    workers), so pin the env explicitly."""
    monkeypatch.setenv("NODYRA_ALLOW_PRIVATE_EGRESS", "0")

