from httpx import AsyncClient


async def test_list_nodes_returns_manifests(client: AsyncClient) -> None:
    resp = await client.get("/nodes")
    assert resp.status_code == 200
    manifests = resp.json()
    ids = {m["id"] for m in manifests}
    assert {"manual_trigger", "if", "code", "http_request"} <= ids

    if_manifest = next(m for m in manifests if m["id"] == "if")
    assert [o["name"] for o in if_manifest["outputs"]] == ["true", "false"]
