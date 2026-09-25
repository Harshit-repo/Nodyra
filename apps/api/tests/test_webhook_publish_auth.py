"""Publish-time enforcement of webhook authentication.

The autouse fixture in conftest turns ``webhook_require_auth`` off so the rest
of the suite can publish open webhooks. Nothing turned it back on, so the
enforcement path itself had no coverage — and it rejected HMAC-signed
webhooks, which is the configuration its own error message recommends.
"""

import pytest
from httpx import AsyncClient

from app.config import settings


@pytest.fixture
def require_webhook_auth():
    previous = settings.webhook_require_auth
    settings.webhook_require_auth = True
    yield
    settings.webhook_require_auth = previous


def _graph(hook_params: dict) -> dict:
    return {
        "nodes": [
            {
                "id": "hook",
                "type": "webhook_trigger",
                "params": {"http_method": "POST", "path": "payments", **hook_params},
                "position": {"x": 0, "y": 0},
            },
            {
                "id": "handle",
                "type": "code",
                "params": {"code": "output = {'ok': True}"},
                "position": {"x": 240, "y": 0},
            },
        ],
        "edges": [{"source": "hook", "target": "handle"}],
    }


async def _publish(client: AsyncClient, hook_params: dict):
    workflow_id = (await client.post("/workflows", json={"name": "Hook"})).json()["id"]
    await client.put(f"/workflows/{workflow_id}", json={"graph": _graph(hook_params)})
    return await client.post(f"/workflows/{workflow_id}/publish", json={})


async def test_open_webhook_cannot_be_published(
    client: AsyncClient, require_webhook_auth
) -> None:
    response = await _publish(client, {"auth_type": "none"})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "payments" in detail["unauthenticated_nodes"][0]


async def test_hmac_signed_webhook_can_be_published(
    client: AsyncClient, require_webhook_auth
) -> None:
    """HMAC is authentication.

    It is how Stripe, GitHub and Shopify sign webhooks, and it authenticates
    the request body as well as the caller. The gate checked auth_type alone,
    so it refused the exact configuration its message told people to use.
    """
    response = await _publish(
        client,
        {
            "auth_type": "none",
            "hmac_verification": "on",
            "hmac_header": "X-Signature",
            "hmac_algorithm": "sha256",
            "hmac_secret": "cred-id-for-the-shared-secret",
        },
    )

    assert response.status_code == 200, response.text


async def test_hmac_without_a_secret_is_still_refused(
    client: AsyncClient, require_webhook_auth
) -> None:
    """Verification with no secret rejects every caller — say so, don't pass it."""
    response = await _publish(
        client, {"auth_type": "none", "hmac_verification": "on", "hmac_secret": ""}
    )

    assert response.status_code == 422
    node = response.json()["detail"]["unauthenticated_nodes"][0]
    assert "no hmac_secret is set" in node


@pytest.mark.parametrize("auth_type", ["basic", "header", "bearer", "jwt"])
async def test_every_documented_auth_type_can_be_published(
    client: AsyncClient, require_webhook_auth, auth_type: str
) -> None:
    """The message names basic, header, bearer, jwt and hmac. All must work."""
    response = await _publish(client, {"auth_type": auth_type})

    assert response.status_code == 200, response.text


async def test_the_gate_is_off_by_configuration(client: AsyncClient) -> None:
    """With enforcement disabled an open webhook publishes, as before."""
    settings.webhook_require_auth = False

    response = await _publish(client, {"auth_type": "none"})

    assert response.status_code == 200, response.text
