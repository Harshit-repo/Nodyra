"""Endpoint + validation tests for Docker-worker provisioning.

Uses the app client fixture (conftest). Patches spawn to avoid real Docker.
"""



async def _make_agent_pool(client):
    resp = await client.post("/runner-pools", json={"name": "dw", "provider": "agent"})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


async def test_add_docker_runner_requires_agent_pool(client, monkeypatch):
    from app.services import docker_workers

    async def _fake_spawn(session, pool, **kw):
        from app.models import Runner
        r = Runner(pool_id=pool.id, name="docker-x", status="offline",
                   capabilities={"docker_managed": True})
        session.add(r)
        await session.commit()
        await session.refresh(r)
        return r

    monkeypatch.setattr(docker_workers, "spawn_docker_runner", _fake_spawn)
    pool_id = await _make_agent_pool(client)
    resp = await client.post(f"/runner-pools/{pool_id}/docker-runners", json={})
    assert resp.status_code == 201, resp.text
    assert resp.json()["capabilities"]["docker_managed"] is True


async def test_add_docker_runner_daemon_unreachable_502(client, monkeypatch):
    from app.services import docker_workers

    async def _boom(session, pool, **kw):
        raise docker_workers.DaemonUnreachable("no daemon")

    monkeypatch.setattr(docker_workers, "spawn_docker_runner", _boom)
    pool_id = await _make_agent_pool(client)
    resp = await client.post(f"/runner-pools/{pool_id}/docker-runners", json={})
    assert resp.status_code == 502


async def test_add_docker_runner_rejects_non_agent_pool(client):
    resp = await client.post(
        "/runner-pools", json={"name": "dockerpool", "provider": "docker"}
    )
    assert resp.status_code in (200, 201), resp.text
    pool_id = resp.json()["id"]
    resp = await client.post(f"/runner-pools/{pool_id}/docker-runners", json={})
    assert resp.status_code == 400
    assert "agent" in resp.json()["detail"].lower()


async def test_remove_docker_runner_busy_409(client):
    from app.models import Runner
    from app.services import runner as runner_module

    pool_id = await _make_agent_pool(client)
    async with runner_module.SessionLocal() as db:
        r = Runner(
            pool_id=pool_id, name="busy", status="busy", current_runs=1,
            capabilities={"docker_managed": True, "container_name": "nodyra-worker-x"},
        )
        db.add(r)
        await db.commit()
        runner_id = r.id
    # Busy + no force → 409 (RunnerBusy is raised before any daemon contact).
    resp = await client.delete(f"/runner-pools/{pool_id}/docker-runners/{runner_id}")
    assert resp.status_code == 409


async def test_autoscale_config_validation_bounds(client):
    pool_id = await _make_agent_pool(client)
    bad = {"provider_config": {"docker_autoscale": {"min_runners": 5, "max_runners": 2}}}
    resp = await client.patch(f"/runner-pools/{pool_id}", json=bad)
    assert resp.status_code == 422

    bad2 = {"provider_config": {"docker_host": "http://evil"}}
    resp = await client.patch(f"/runner-pools/{pool_id}", json=bad2)
    assert resp.status_code == 422

    ok = {"provider_config": {"docker_autoscale": {"min_runners": 0, "max_runners": 4,
          "idle_seconds": 300}, "docker_runner": {"cpu": 1.0, "memory_mb": 1024}}}
    resp = await client.patch(f"/runner-pools/{pool_id}", json=ok)
    assert resp.status_code == 200


async def test_create_pool_validates_docker_config(client):
    """P0: the validator must run on POST /runner-pools, not only PATCH —
    otherwise a pool is created in one call with an out-of-band docker_host."""
    resp = await client.post(
        "/runner-pools",
        json={
            "name": "evil",
            "provider": "agent",
            "provider_config": {"docker_host": "http://evil-internal-host:9999"},
        },
    )
    assert resp.status_code == 422

    resp = await client.post(
        "/runner-pools",
        json={
            "name": "toobig",
            "provider": "agent",
            "provider_config": {"docker_runner": {"cpu": 999, "memory_mb": 999999999}},
        },
    )
    assert resp.status_code == 422
