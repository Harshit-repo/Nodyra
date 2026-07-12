from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

import app.services.environment_builds as environment_builds
from app.models import Environment, EnvironmentBuildJob
from app.services.environment_builds import _environment_hash, _snapshot_kwargs


async def test_environment_create_enqueues_build_job(client: AsyncClient) -> None:
    created = (
        await client.post(
            "/environments",
            json={"name": "Queued Env", "packages": ["pandas"]},
        )
    ).json()
    assert created["status"] == "pending"
    assert created["build_job_id"]
    assert created["build_job_status"] == "queued"

    listing = (
        await client.get(f"/environments/{created['id']}/build-jobs")
    ).json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == created["build_job_id"]
    assert listing["items"][0]["status"] == "queued"
    assert listing["items"][0]["package_snapshot"] == ["pandas"]

    detail = (
        await client.get(
            f"/environments/{created['id']}/build-jobs/{created['build_job_id']}"
        )
    ).json()
    assert detail["id"] == created["build_job_id"]
    assert detail["interpreter"] == "cpython"


async def test_environment_hash_differs_by_interpreter() -> None:
    base = Environment(
        name="a",
        python_version="3.14",
        packages=["pandas"],
        backend="venv",
        backend_config={},
        interpreter="cpython",
    )
    ft = Environment(
        name="a",
        python_version="3.14",
        packages=["pandas"],
        backend="venv",
        backend_config={},
        interpreter="cpython-ft",
    )
    assert _environment_hash(base) != _environment_hash(ft)


async def test_snapshot_kwargs_carries_interpreter() -> None:
    env = Environment(
        name="a",
        python_version="3.13",
        packages=[],
        backend="venv",
        backend_config={},
        interpreter="pypy",
    )
    snapshot = _snapshot_kwargs(env)
    assert snapshot["interpreter"] == "pypy"


async def test_package_update_supersedes_stale_queued_build(client: AsyncClient) -> None:
    created = (await client.post("/environments", json={"name": "Supersede"})).json()
    first_job_id = created["build_job_id"]

    updated = (
        await client.put(
            f"/environments/{created['id']}/packages",
            json={"packages": ["duckdb"]},
        )
    ).json()
    second_job_id = updated["build_job_id"]
    assert second_job_id != first_job_id

    listing = (
        await client.get(f"/environments/{created['id']}/build-jobs")
    ).json()
    by_id = {item["id"]: item for item in listing["items"]}
    assert by_id[first_job_id]["status"] == "superseded"
    assert by_id[second_job_id]["status"] == "queued"


async def test_process_environment_build_job_marks_success(
    client: AsyncClient,
    monkeypatch,
) -> None:
    created = (
        await client.post(
            "/environments",
            json={"name": "Process Env", "packages": ["numpy"]},
        )
    ).json()

    async def fake_build_environment(env_id: str) -> None:
        async with environment_builds.SessionLocal() as session:
            env = await session.get(Environment, env_id)
            assert env is not None
            env.status = "ready"
            env.status_detail = "built"
            await session.commit()

    monkeypatch.setattr(
        "app.services.backends.build_environment",
        fake_build_environment,
    )

    await environment_builds.process_environment_build_job(created["build_job_id"])

    async with environment_builds.SessionLocal() as session:
        job = await session.get(EnvironmentBuildJob, created["build_job_id"])
        env = await session.get(Environment, created["id"])
        assert job is not None
        assert env is not None
        assert job.status == "succeeded"
        assert job.finished_at is not None
        assert env.status == "ready"
        assert env.status_detail == "built"


async def test_expired_environment_build_lease_requeues(
    client: AsyncClient,
) -> None:
    created = (await client.post("/environments", json={"name": "Lease Env"})).json()
    moment = datetime.now(UTC)
    async with environment_builds.SessionLocal() as session:
        leased = await environment_builds.lease_environment_build(
            session,
            worker_id="test-worker",
            now=moment,
        )
        assert leased is not None
        leased.lease_expires_at = moment - timedelta(seconds=1)
        await session.commit()

    async with environment_builds.SessionLocal() as session:
        acted = await environment_builds.requeue_expired_environment_build_leases(
            session,
            now=moment,
        )
        await session.commit()
        assert acted == 1

    async with environment_builds.SessionLocal() as session:
        job = await session.get(EnvironmentBuildJob, created["build_job_id"])
        assert job is not None
        assert job.status == "queued"
        assert job.last_error == "lease expired (worker lost)"
