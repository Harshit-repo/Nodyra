"""Regression: live-settings artifact caps must reach the run's artifact store.

An earlier refactor extracted ``_prepare_run_context`` from
``_execute_run_impl`` but left a local ``live = None`` behind — the in-process
artifact store was then always built with ``max_bytes=None`` /
``max_count=None`` (boot defaults), silently ignoring the admin UI's live
overrides for ``max_artifact_bytes`` / ``max_artifacts_per_run``.
"""

from httpx import AsyncClient

import app.services.runner as runner_module
from app.services.live_settings import LiveSettings


def _live(**overrides) -> LiveSettings:
    base = dict(
        max_concurrent_runs=4,
        runner_idle_seconds=300,
        run_retention_days=30,
        run_retention_max_per_workflow=0,
        max_output_bytes=262_144,
        max_artifact_bytes=1234,
        max_artifacts_per_run=7,
        app_timezone="UTC",
        worker_rss_soft_budget_bytes=0,
    )
    base.update(overrides)
    return LiveSettings(**base)


async def test_prepare_run_context_carries_live_artifact_caps(
    client: AsyncClient, monkeypatch
) -> None:
    async def fake_live_settings() -> LiveSettings:
        return _live()

    monkeypatch.setattr(runner_module, "get_live_settings", fake_live_settings)

    wf = (await client.post("/workflows", json={"name": "caps"})).json()
    prep = await runner_module._prepare_run_context(
        "run-caps", wf["id"], {"nodes": [], "edges": []}, None, None
    )
    assert prep.max_artifact_bytes == 1234
    assert prep.max_artifacts_per_run == 7
    assert prep.output_cap == 262_144


async def test_in_process_run_builds_artifact_store_with_live_caps(
    client: AsyncClient, monkeypatch
) -> None:
    async def fake_live_settings() -> LiveSettings:
        return _live(max_artifact_bytes=4321, max_artifacts_per_run=3)

    monkeypatch.setattr(runner_module, "get_live_settings", fake_live_settings)

    captured: dict = {}
    real_make = runner_module.make_artifact_store

    def spy_make_artifact_store(run_id, *, org_id=None, max_bytes=None, max_count=None):
        captured["max_bytes"] = max_bytes
        captured["max_count"] = max_count
        return real_make(
            run_id, org_id=org_id, max_bytes=max_bytes, max_count=max_count
        )

    monkeypatch.setattr(
        runner_module, "make_artifact_store", spy_make_artifact_store
    )

    wf = (await client.post("/workflows", json={"name": "caps-run"})).json()
    graph = {
        "nodes": [
            {"id": "t", "type": "manual_trigger", "params": {}},
            {
                "id": "c",
                "type": "code",
                "params": {"code": "output = 1"},
            },
        ],
        "edges": [
            {"source": "t", "target": "c", "source_output": "main", "target_input": "main"}
        ],
    }
    await client.put(f"/workflows/{wf['id']}", json={"graph": graph})
    resp = await client.post(f"/workflows/{wf['id']}/run", json={})
    assert resp.status_code == 202

    assert captured.get("max_bytes") == 4321
    assert captured.get("max_count") == 3
