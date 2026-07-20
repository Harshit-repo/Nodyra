"""Tests for the Community Node Registry router (MS4 Slice 4E)."""

import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import AsyncClient

from app.config import settings
from app.services.registry_trust import canonical_package_manifest

_PUBLISHER_KEY_ID = "test-publisher"
_PUBLISHER_PRIVATE_KEY = Ed25519PrivateKey.generate()
_PUBLISHER_PUBLIC_KEY = base64.urlsafe_b64encode(
    _PUBLISHER_PRIVATE_KEY.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
).decode().rstrip("=")


def _signed_package(
    package_id: str,
    name: str,
    version: str,
    description: str,
    nodes: list[str],
) -> dict:
    package = {
        "id": package_id,
        "name": name,
        "description": description,
        "author": "community",
        "version": version,
        "nodes": nodes,
        "install_url": f"https://github.com/community/{package_id}",
        "pypi_package": package_id,
        "distribution_url": (
            f"https://files.pythonhosted.org/packages/{package_id}-{version}-py3-none-any.whl"
        ),
        "distribution_sha256": "a" * 64,
        "publisher_key_id": _PUBLISHER_KEY_ID,
        "permissions": {"network": []},
        "compatibility": {"nodyra": ">=0.1,<0.2"},
        "lifecycle": "active",
    }
    package["signature"] = base64.urlsafe_b64encode(
        _PUBLISHER_PRIVATE_KEY.sign(canonical_package_manifest(package))
    ).decode().rstrip("=")
    return package


# Sample registry index used by mock responses.
_SAMPLE_INDEX = {
    "packages": [
        _signed_package(
            "nodyra-stripe-nodes",
            "Stripe Nodes",
            "1.2.0",
            "Nodes for Stripe payment operations",
            ["stripe_charge", "stripe_refund", "stripe_webhook"],
        ),
        _signed_package(
            "nodyra-slack-nodes",
            "Slack Nodes",
            "0.4.1",
            "Send messages and interact with Slack",
            ["slack_send", "slack_listen"],
        ),
        _signed_package(
            "nodyra-ai-nodes",
            "AI Nodes",
            "2.0.0",
            "Extra AI/LLM nodes for advanced workflows",
            ["ai_embed", "ai_rerank"],
        ),
    ]
}


@pytest.fixture(autouse=True)
def _trust_test_publisher(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        settings,
        "registry_trusted_publishers",
        json.dumps({_PUBLISHER_KEY_ID: _PUBLISHER_PUBLIC_KEY}),
    )

# ── Tests ────────────────────────────────────────────────────────────────


async def test_registry_search_returns_all_when_no_query(
    client: AsyncClient, httpx_mock
) -> None:
    """Search with no query returns all packages from the registry index."""
    httpx_mock.add_response(
        url="https://raw.githubusercontent.com/nodyra-registry/packages/main/index.json",
        json=_SAMPLE_INDEX,
    )

    resp = await client.get("/node-registry/search")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["packages"]) == 3


async def test_registry_search_returns_matching_packages(
    client: AsyncClient, httpx_mock
) -> None:
    """Search with a query filters packages by name/description/nodes."""
    httpx_mock.add_response(
        url="https://raw.githubusercontent.com/nodyra-registry/packages/main/index.json",
        json=_SAMPLE_INDEX,
    )

    resp = await client.get("/node-registry/search?q=stripe")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["packages"]) == 1
    assert data["packages"][0]["id"] == "nodyra-stripe-nodes"


async def test_registry_search_filters_by_node_name(
    client: AsyncClient, httpx_mock
) -> None:
    """Search matches nodes within packages."""
    httpx_mock.add_response(
        url="https://raw.githubusercontent.com/nodyra-registry/packages/main/index.json",
        json=_SAMPLE_INDEX,
    )

    resp = await client.get("/node-registry/search?q=slack_send")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["packages"]) == 1
    assert data["packages"][0]["id"] == "nodyra-slack-nodes"


async def test_registry_search_handles_no_matches(
    client: AsyncClient, httpx_mock
) -> None:
    """Search with no matches returns an empty list."""
    httpx_mock.add_response(
        url="https://raw.githubusercontent.com/nodyra-registry/packages/main/index.json",
        json=_SAMPLE_INDEX,
    )

    resp = await client.get("/node-registry/search?q=zzzznonexistent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["packages"] == []


async def test_registry_get_package_by_id(
    client: AsyncClient, httpx_mock
) -> None:
    """GET /node-registry/packages/{id} returns the single package."""
    httpx_mock.add_response(
        url="https://raw.githubusercontent.com/nodyra-registry/packages/main/index.json",
        json=_SAMPLE_INDEX,
    )

    resp = await client.get("/node-registry/packages/nodyra-stripe-nodes")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "nodyra-stripe-nodes"
    assert data["name"] == "Stripe Nodes"
    assert data["pypi_package"] == "nodyra-stripe-nodes"
    assert data["trust"]["status"] == "verified"


async def test_registry_get_package_not_found(
    client: AsyncClient, httpx_mock
) -> None:
    """GET /node-registry/packages/{id} returns 404 for unknown packages."""
    httpx_mock.add_response(
        url="https://raw.githubusercontent.com/nodyra-registry/packages/main/index.json",
        json=_SAMPLE_INDEX,
    )

    resp = await client.get("/node-registry/packages/unknown-package")
    assert resp.status_code == 404


async def test_registry_index_unreachable_returns_502(
    client: AsyncClient, httpx_mock
) -> None:
    """When the registry index is unreachable, return 502."""
    httpx_mock.add_response(
        url="https://raw.githubusercontent.com/nodyra-registry/packages/main/index.json",
        status_code=503,
    )

    resp = await client.get("/node-registry/search")
    assert resp.status_code == 502


async def test_registry_install_requires_env_and_package_id(
    client: AsyncClient,
) -> None:
    """POST /node-registry/install returns 422 when fields are missing.

    Field validation happens *before* the registry index is fetched, so no
    httpx mock for the index URL is needed in this test.
    """
    # Create an environment first
    env = (
        await client.post("/environments", json={"name": "InstallTest"})
    ).json()
    env_id = env["id"]

    # Missing environment_id
    resp = await client.post(
        "/node-registry/install",
        json={"package_id": "nodyra-stripe-nodes"},
    )
    assert resp.status_code == 422

    # Missing package_id
    resp = await client.post(
        "/node-registry/install",
        json={"environment_id": env_id},
    )
    assert resp.status_code == 422


async def test_registry_install_adds_to_env_packages(
    client: AsyncClient, httpx_mock
) -> None:
    """POST /node-registry/install adds the pypi package to the environment."""
    httpx_mock.add_response(
        url="https://raw.githubusercontent.com/nodyra-registry/packages/main/index.json",
        json=_SAMPLE_INDEX,
    )

    # Create an environment
    env = (
        await client.post("/environments", json={"name": "InstallTest2"})
    ).json()
    env_id = env["id"]

    # Install a package
    resp = await client.post(
        "/node-registry/install",
        json={
            "package_id": "nodyra-stripe-nodes",
            "environment_id": env_id,
        },
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "install_id" in data
    assert data["status"] == "queued"

    # Verify the package was added to the environment
    env_resp = await client.get(f"/environments/{env_id}")
    assert env_resp.status_code == 200
    env_data = env_resp.json()
    assert any(
        package.startswith("nodyra-stripe-nodes @ https://")
        and "#sha256=" in package
        for package in env_data["packages"]
    )


async def test_registry_install_rejects_unknown_package(
    client: AsyncClient, httpx_mock
) -> None:
    """POST /node-registry/install returns 404 for unknown packages."""
    httpx_mock.add_response(
        url="https://raw.githubusercontent.com/nodyra-registry/packages/main/index.json",
        json=_SAMPLE_INDEX,
    )

    env = (
        await client.post("/environments", json={"name": "InstallTest3"})
    ).json()
    env_id = env["id"]

    resp = await client.post(
        "/node-registry/install",
        json={
            "package_id": "nonexistent-package",
            "environment_id": env_id,
        },
    )
    assert resp.status_code == 404


async def test_registry_install_rejects_tampered_signature(
    client: AsyncClient, httpx_mock
) -> None:
    package = dict(_SAMPLE_INDEX["packages"][0])
    package["version"] = "1.2.1"
    httpx_mock.add_response(
        url="https://raw.githubusercontent.com/nodyra-registry/packages/main/index.json",
        json={"packages": [package]},
    )
    env_id = (await client.post("/environments", json={"name": "Tampered"})).json()["id"]

    resp = await client.post(
        "/node-registry/install",
        json={"package_id": package["id"], "environment_id": env_id},
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["status"] == "invalid"


async def test_production_never_installs_unverified_package(
    client: AsyncClient, httpx_mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = dict(_SAMPLE_INDEX["packages"][0])
    package.pop("signature")
    monkeypatch.setattr(settings, "runtime_mode", "production")
    monkeypatch.setattr(settings, "registry_allow_unverified_install", True)
    httpx_mock.add_response(
        url="https://raw.githubusercontent.com/nodyra-registry/packages/main/index.json",
        json={"packages": [package]},
    )
    env_id = (await client.post("/environments", json={"name": "Unsigned"})).json()["id"]

    resp = await client.post(
        "/node-registry/install",
        json={"package_id": package["id"], "environment_id": env_id},
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["status"] == "unverified"


async def test_registry_install_status(client: AsyncClient, httpx_mock) -> None:
    """GET /node-registry/installs/{id} returns the install status."""
    httpx_mock.add_response(
        url="https://raw.githubusercontent.com/nodyra-registry/packages/main/index.json",
        json=_SAMPLE_INDEX,
    )

    env = (
        await client.post("/environments", json={"name": "InstallTest4"})
    ).json()
    env_id = env["id"]

    install_resp = await client.post(
        "/node-registry/install",
        json={
            "package_id": "nodyra-stripe-nodes",
            "environment_id": env_id,
        },
    )
    install_data = install_resp.json()
    install_id = install_data["install_id"]

    # Poll status
    status_resp = await client.get(
        f"/node-registry/installs/{install_id}"
    )
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["install_id"] == install_id
    assert status_data["status"] in ("queued", "building", "ready", "failed")
    assert status_data["package_id"] == "nodyra-stripe-nodes"


async def test_registry_install_status_not_found(
    client: AsyncClient,
) -> None:
    """GET /node-registry/installs/{id} returns 404 for unknown install IDs."""
    resp = await client.get("/node-registry/installs/unknown123")
    assert resp.status_code == 404
