from __future__ import annotations

import pytest
from nodyra_client.client import NodyraClient, NodyraError


def test_401_carries_login_hint(httpx_mock) -> None:
    httpx_mock.add_response(status_code=401, json={"detail": "Not authenticated"})
    with NodyraClient(base_url="http://api.test", token="bad") as client:
        with pytest.raises(NodyraError) as exc_info:
            client.whoami()
    assert exc_info.value.status == 401
    assert "nodyra login" in (exc_info.value.hint or "")


def test_403_carries_scope_hint(httpx_mock) -> None:
    httpx_mock.add_response(status_code=403, json={"detail": "missing scope workflow:write"})
    with NodyraClient(base_url="http://api.test", token="t") as client:
        with pytest.raises(NodyraError) as exc_info:
            client.workflows.create(name="x")
    assert "scope" in (exc_info.value.hint or "").lower()


def test_transport_uses_connect_retries() -> None:
    with NodyraClient(base_url="http://api.test") as client:
        transport = client._client._transport
        assert transport._pool._retries == 2
