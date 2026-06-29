"""Tests for the Community Node Registry router (MS4 Slice 4E)."""

import pytest
from httpx import AsyncClient

# Sample registry index used by mock responses.
_SAMPLE_INDEX = {
    "packages": [
        {
            "id": "noodle-stripe-nodes",
            "name": "Stripe Nodes",
            "description": "Nodes for Stripe payment operations",
            "author": "community",
            "version": "1.2.0",
            "nodes": ["stripe_charge", "stripe_refund", "stripe_webhook"],
            "install_url": "https://github.com/author/noodle-stripe-nodes",
            "pypi_package": "noodle-stripe-nodes",
        },
        {
            "id": "noodle-slack-nodes",
            "name": "Slack Nodes",
            "description": "Send messages and interact with Slack",
            "author": "community",
            "version": "0.4.1",
            "nodes": ["slack_send", "slack_listen"],
            "install_url": "https://github.com/author/noodle-slack-nodes",
            "pypi_package": "noodle-slack-nodes",
        },
        {
            "id": "noodle-ai-nodes",
            "name": "AI Nodes",
            "description": "Extra AI/LLM nodes for advanced workflows",
            "author": "noodle-labs",
            "version": "2.0.0",
            "nodes": ["ai_embed", "ai_rerank"],
            "install_url": "https://github.com/noodle-labs/noodle-ai-nodes",
            "pypi_package": "noodle-ai-nodes",
        },
    ]
}

# ── Tests ────────────────────────────────────────────────────────────────


async def test_registry_search_returns_all_when_no_query(
    client: AsyncClient, httpx_mock
) -> None:
    """Search with no query returns all packages from the registry index."""
    httpx_mock.add_response(
        url="https://raw.githubusercontent.com/noodle-registry/packages/main/index.json",
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
        url="https://raw.githubusercontent.com/noodle-registry/packages/main/index.json",
        json=_SAMPLE_INDEX,
    )

    resp = await client.get("/node-registry/search?q=stripe")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["packages"]) == 1
    assert data["packages"][0]["id"] == "noodle-stripe-nodes"


async def test_registry_search_filters_by_node_name(
    client: AsyncClient, httpx_mock
) -> None:
    """Search matches nodes within packages."""
    httpx_mock.add_response(
        url="https://raw.githubusercontent.com/noodle-registry/packages/main/index.json",
        json=_SAMPLE_INDEX,
    )

    resp = await client.get("/node-registry/search?q=slack_send")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["packages"]) == 1
    assert data["packages"][0]["id"] == "noodle-slack-nodes"


async def test_registry_search_handles_no_matches(
    client: AsyncClient, httpx_mock
) -> None:
    """Search with no matches returns an empty list."""
    httpx_mock.add_response(
        url="https://raw.githubusercontent.com/noodle-registry/packages/main/index.json",
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
        url="https://raw.githubusercontent.com/noodle-registry/packages/main/index.json",
        json=_SAMPLE_INDEX,
    )

    resp = await client.get("/node-registry/packages/noodle-stripe-nodes")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "noodle-stripe-nodes"
    assert data["name"] == "Stripe Nodes"
    assert data["pypi_package"] == "noodle-stripe-nodes"


async def test_registry_get_package_not_found(
    client: AsyncClient, httpx_mock
) -> None:
    """GET /node-registry/packages/{id} returns 404 for unknown packages."""
    httpx_mock.add_response(
        url="https://raw.githubusercontent.com/noodle-registry/packages/main/index.json",
        json=_SAMPLE_INDEX,
    )

    resp = await client.get("/node-registry/packages/unknown-package")
    assert resp.status_code == 404


async def test_registry_index_unreachable_returns_502(
    client: AsyncClient, httpx_mock
) -> None:
    """When the registry index is unreachable, return 502."""
    httpx_mock.add_response(
        url="https://raw.githubusercontent.com/noodle-registry/packages/main/index.json",
        status_code=503,
    )

    resp = await client.get("/node-registry/search")
    assert resp.status_code == 502


async def test_registry_install_requires_env_and_package_id(
    client: AsyncClient, httpx_mock
) -> None:
    """POST /node-registry/install returns 422 when fields are missing."""
    # Need to mock the registry index fetch that happens for validation
    httpx_mock.add_response(
        url="https://raw.githubusercontent.com/noodle-registry/packages/main/index.json",
        json=_SAMPLE_INDEX,
    )

    # Create an environment first
    env = (
        await client.post("/environments", json={"name": "InstallTest"})
    ).json()
    env_id = env["id"]

    # Missing environment_id
    resp = await client.post(
        "/node-registry/install",
        json={"package_id": "noodle-stripe-nodes"},
    )
    assert resp.status_code == 422

    # Missing package_id
    # Need new mock for second request since the first consumed it (the index fetch)
    httpx_mock.add_response(
        url="https://raw.githubusercontent.com/noodle-registry/packages/main/index.json",
        json=_SAMPLE_INDEX,
    )
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
        url="https://raw.githubusercontent.com/noodle-registry/packages/main/index.json",
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
            "package_id": "noodle-stripe-nodes",
            "environment_id": env_id,
        },
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "install_id" in data
    assert data["status"] == "pending"

    # Verify the package was added to the environment
    env_resp = await client.get(f"/environments/{env_id}")
    assert env_resp.status_code == 200
    env_data = env_resp.json()
    assert "noodle-stripe-nodes" in env_data["packages"]


async def test_registry_install_rejects_unknown_package(
    client: AsyncClient, httpx_mock
) -> None:
    """POST /node-registry/install returns 404 for unknown packages."""
    httpx_mock.add_response(
        url="https://raw.githubusercontent.com/noodle-registry/packages/main/index.json",
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


async def test_registry_install_status(client: AsyncClient, httpx_mock) -> None:
    """GET /node-registry/installs/{id} returns the install status."""
    httpx_mock.add_response(
        url="https://raw.githubusercontent.com/noodle-registry/packages/main/index.json",
        json=_SAMPLE_INDEX,
    )

    env = (
        await client.post("/environments", json={"name": "InstallTest4"})
    ).json()
    env_id = env["id"]

    install_resp = await client.post(
        "/node-registry/install",
        json={
            "package_id": "noodle-stripe-nodes",
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
    assert status_data["status"] in ("pending", "installing", "ready", "failed")
    assert status_data["package_id"] == "noodle-stripe-nodes"


async def test_registry_install_status_not_found(
    client: AsyncClient,
) -> None:
    """GET /node-registry/installs/{id} returns 404 for unknown install IDs."""
    resp = await client.get("/node-registry/installs/unknown123")
    assert resp.status_code == 404
