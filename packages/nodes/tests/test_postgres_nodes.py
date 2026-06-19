"""SEC-4: Postgres nodes must not connect to private hosts in multi-tenant mode."""

from __future__ import annotations

import pytest

from noodle_nodes.http_security import UnsafeHttpTargetError
from noodle_nodes.postgres_nodes import _assert_connection_allowed


def test_postgres_blocks_private_host_by_default(monkeypatch) -> None:
    monkeypatch.delenv("NOODLE_ALLOW_PRIVATE_EGRESS", raising=False)
    with pytest.raises(UnsafeHttpTargetError):
        _assert_connection_allowed("postgresql://user:pass@10.0.0.5:5432/db")


def test_postgres_blocks_localhost_by_default(monkeypatch) -> None:
    monkeypatch.delenv("NOODLE_ALLOW_PRIVATE_EGRESS", raising=False)
    with pytest.raises(UnsafeHttpTargetError):
        _assert_connection_allowed("postgresql://user:pass@localhost/db")


def test_postgres_allows_private_when_opted_in(monkeypatch) -> None:
    monkeypatch.setenv("NOODLE_ALLOW_PRIVATE_EGRESS", "1")
    # No raise — self-hosted operators reach their internal database.
    _assert_connection_allowed("postgresql://user:pass@10.0.0.5:5432/db")


def test_postgres_allows_public_host(monkeypatch) -> None:
    monkeypatch.delenv("NOODLE_ALLOW_PRIVATE_EGRESS", raising=False)
    # A public managed-DB host passes (DNS resolves to a public address, or the
    # lookup fails and the connection layer surfaces the real error).
    _assert_connection_allowed("postgresql://user:pass@db.example.com:5432/db")
