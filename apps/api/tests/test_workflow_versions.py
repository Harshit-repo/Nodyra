"""Tests for workflow version list enhancements and the /graph endpoint."""

import pytest
from httpx import AsyncClient

SIMPLE_GRAPH = {
    "nodes": [
        {"id": "n1", "type": "user:test:fn", "params": {}, "position": {"x": 0, "y": 0}},
        {"id": "n2", "type": "user:test:fn", "params": {}, "position": {"x": 200, "y": 0}},
    ],
    "edges": [],
}


async def make_workflow(client: AsyncClient, name: str = "Test WF") -> str:
    resp = await client.post("/workflows", json={"name": name})
    assert resp.status_code == 201
    wf_id = resp.json()["id"]
    await client.put(f"/workflows/{wf_id}", json={"graph": SIMPLE_GRAPH})
    return wf_id


async def publish_workflow(client: AsyncClient, wf_id: str, notes: str = "") -> None:
    resp = await client.post(f"/workflows/{wf_id}/publish", json={"notes": notes})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_versions_includes_node_count_and_published(client: AsyncClient):
    """list_versions returns node_count and published, no graph."""
    wf_id = await make_workflow(client)
    await publish_workflow(client, wf_id)

    resp = await client.get(f"/workflows/{wf_id}/versions")
    assert resp.status_code == 200
    versions = resp.json()
    assert len(versions) >= 1
    # All versions must have the new fields and must NOT expose the graph blob
    for v in versions:
        assert "node_count" in v
        assert "published" in v
        assert "notes" in v
        assert "graph" not in v
        assert isinstance(v["node_count"], int)
        assert isinstance(v["published"], bool)
    # The published version (highest version number) has SIMPLE_GRAPH — 2 nodes
    published_v = next(v for v in versions if v["published"])
    assert published_v["node_count"] == 2


@pytest.mark.asyncio
async def test_list_versions_node_count_matches_graph(client: AsyncClient):
    """node_count reflects actual number of nodes in the version graph."""
    wf_id = await make_workflow(client)
    await publish_workflow(client, wf_id, notes="first release")

    resp = await client.get(f"/workflows/{wf_id}/versions")
    versions = resp.json()
    published_v = next(v for v in versions if v["published"])
    assert published_v["node_count"] == 2
    assert published_v["notes"] == "first release"


@pytest.mark.asyncio
async def test_list_versions_published_flag(client: AsyncClient):
    """published=True only for the currently published version."""
    wf_id = await make_workflow(client)
    await publish_workflow(client, wf_id)

    resp = await client.get(f"/workflows/{wf_id}/versions")
    versions = resp.json()
    published = [v for v in versions if v["published"]]
    assert len(published) == 1


@pytest.mark.asyncio
async def test_get_version_graph_returns_graph(client: AsyncClient):
    """New /graph sub-endpoint returns the full graph for one version."""
    wf_id = await make_workflow(client)
    await publish_workflow(client, wf_id)

    list_resp = await client.get(f"/workflows/{wf_id}/versions")
    # Use the published version — it has SIMPLE_GRAPH with 2 nodes
    published_v = next(v for v in list_resp.json() if v["published"])
    version_id = published_v["id"]

    resp = await client.get(f"/workflows/{wf_id}/versions/{version_id}/graph")
    assert resp.status_code == 200
    body = resp.json()
    assert "graph" in body
    assert "nodes" in body["graph"]
    assert "edges" in body["graph"]
    assert len(body["graph"]["nodes"]) == 2


@pytest.mark.asyncio
async def test_get_version_graph_404_unknown_version(client: AsyncClient):
    wf_id = await make_workflow(client)
    resp = await client.get(f"/workflows/{wf_id}/versions/nonexistent/graph")
    assert resp.status_code == 404
