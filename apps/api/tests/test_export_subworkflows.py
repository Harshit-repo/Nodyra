"""A3: exported scripts bundle referenced sub-workflow graphs."""

from httpx import AsyncClient


async def test_export_script_bundles_subworkflow_graphs(
    client: AsyncClient,
) -> None:
    sub = (await client.post("/workflows", json={"name": "Child"})).json()
    await client.put(
        f"/workflows/{sub['id']}",
        json={"graph": {"nodes": [
            {"id": "ct", "type": "manual_trigger", "params": {},
             "position": {"x": 0, "y": 0}}], "edges": []}},
    )
    parent = (await client.post("/workflows", json={"name": "Par"})).json()
    await client.put(
        f"/workflows/{parent['id']}",
        json={"graph": {"nodes": [
            {"id": "s", "type": "execute_workflow",
             "params": {"workflow_id": sub["id"]},
             "position": {"x": 0, "y": 0}}], "edges": []}},
    )

    resp = await client.get(f"/workflows/{parent['id']}/export.py")
    assert resp.status_code == 200
    script = resp.text
    assert "SUBWORKFLOWS" in script
    assert sub["id"] in script  # bundled child graph keyed by id
    assert repr(parent["id"]) in script  # ROOT_ID seeds the call chain


async def test_export_script_without_subs_has_empty_bundle(
    client: AsyncClient,
) -> None:
    wf = (await client.post("/workflows", json={"name": "Solo"})).json()
    await client.put(
        f"/workflows/{wf['id']}",
        json={"graph": {"nodes": [
            {"id": "t", "type": "manual_trigger", "params": {},
             "position": {"x": 0, "y": 0}}], "edges": []}},
    )
    resp = await client.get(f"/workflows/{wf['id']}/export.py")
    assert resp.status_code == 200
    assert "SUBWORKFLOWS = {}" in resp.text
