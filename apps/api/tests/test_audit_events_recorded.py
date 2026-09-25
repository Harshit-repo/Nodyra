"""Behavioural proof that privileged mutations land audit rows (F-05).

``test_audit_coverage`` is a static gate — it proves the *call* exists. These
tests drive the real endpoints and assert the row actually reaches the
``audit_events`` table, which is what an auditor reads.
"""



async def _events(client, target_type: str) -> list[tuple[str, str, str]]:
    """Audit rows as an auditor sees them — through GET /audit, not the ORM."""
    resp = await client.get("/audit", params={"limit": 500})
    assert resp.status_code == 200, resp.text
    return [
        (item["action"], item.get("target_id") or "", item.get("detail") or "")
        for item in resp.json()["items"]
        if item.get("target_type") == target_type
    ]


async def test_environment_package_changes_are_recorded(client):
    env = (await client.post("/environments", json={"name": "audited-env"})).json()
    env_id = env["id"]

    await client.post(f"/environments/{env_id}/packages", json={"package": "httpx==0.27.0"})
    await client.post(f"/environments/{env_id}/rebuild")

    recorded = await _events(client, "environment")
    actions = {a for a, _, _ in recorded}
    assert "add_package" in actions, recorded
    assert "rebuild" in actions, recorded
    assert any(
        target == env_id and "httpx==0.27.0" in detail
        for action, target, detail in recorded
        if action == "add_package"
    ), recorded


async def test_environment_delete_is_recorded_before_the_row_disappears(client):
    env = (await client.post("/environments", json={"name": "doomed-env"})).json()

    resp = await client.delete(f"/environments/{env['id']}")
    assert resp.status_code in (200, 204), resp.text

    recorded = await _events(client, "environment")
    assert any(
        action == "delete" and target == env["id"] and "doomed-env" in detail
        for action, target, detail in recorded
    ), recorded


async def test_runner_pool_lifecycle_is_recorded(client):
    pool = (
        await client.post(
            "/runner-pools",
            json={"name": "audited-pool", "provider": "agent", "max_concurrent_runs": 2},
        )
    ).json()
    pool_id = pool["id"]

    await client.patch(f"/runner-pools/{pool_id}", json={"max_concurrent_runs": 5})
    await client.delete(f"/runner-pools/{pool_id}")

    recorded = await _events(client, "runner_pool")
    by_action = {action: (target, detail) for action, target, detail in recorded}
    assert {"create", "update", "delete"} <= set(by_action), recorded
    assert by_action["create"][0] == pool_id
    assert "audited-pool" in by_action["create"][1]
    # The delete detail must be captured while the row still exists.
    assert "audited-pool" in by_action["delete"][1], by_action["delete"]


async def test_queue_drain_is_recorded(client):
    resp = await client.post("/ops/drain", json={"draining": True})
    assert resp.status_code == 200, resp.text
    await client.post("/ops/drain", json={"draining": False})

    recorded = await _events(client, "queue")
    actions = {a for a, _, _ in recorded}
    assert {"drain", "undrain"} <= actions, recorded


async def test_audit_failure_never_breaks_the_action(client, monkeypatch):
    """The recorder swallows its own persistence errors: an audit outage must
    not take down the endpoint it is observing."""
    from app.services import audit as audit_service

    async def _boom(*args, **kwargs):
        raise RuntimeError("audit table unavailable")

    monkeypatch.setattr(audit_service, "log_audit", _boom)

    resp = await client.post("/ops/drain", json={"draining": False})
    assert resp.status_code == 200, resp.text
