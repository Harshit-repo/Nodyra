from __future__ import annotations

import json
import logging

from httpx import AsyncClient
from sqlalchemy import select

from app.models import Artifact, NodeRun, RunEvent
from nodyra.artifacts import ARTIFACT_MARKER, ARTIFACT_VERSION
from nodyra.models import RunResult, RunStatus


async def test_secret_redaction_covers_persisted_events_logs_and_artifacts(
    client: AsyncClient,
    monkeypatch,
    caplog,
) -> None:
    """A credential value must not leak through persisted run inspection surfaces."""
    import app.services.runner as runner_module

    caplog.set_level(logging.DEBUG)
    secret = "prod-redaction-sentinel-123456"
    artifact_id = "artifact-redaction-sentinel"
    await client.post(
        "/credentials",
        json={
            "name": "Leak sentinel",
            "type": "generic",
            "scope": "global",
            "data": {"token": secret},
        },
    )
    workflow_id = (await client.post("/workflows", json={"name": "Redaction"})).json()["id"]
    await client.put(
        f"/workflows/{workflow_id}",
        json={
            "graph": {
                "nodes": [
                    {
                        "id": "emit",
                        "type": "manual_trigger",
                        "params": {},
                        "position": {"x": 0, "y": 0},
                    }
                ],
                "edges": [],
            }
        },
    )

    artifact_ref = {
        ARTIFACT_MARKER: True,
        "version": ARTIFACT_VERSION,
        "artifact_id": artifact_id,
        "node_id": "emit",
        "name": "secret-preview.txt",
        "kind": "text",
        "content_type": "text/plain",
        "size_bytes": 128,
        "metadata": {"token": secret, "note": f"metadata contains {secret}"},
        "preview": f"artifact preview contains {secret}",
    }

    async def fake_execute(graph, registry, **kwargs) -> RunResult:  # noqa: ANN001, ARG001
        on_event = kwargs["options"].on_event
        await on_event(
            {
                "type": "node_finished",
                "node_id": "emit",
                "status": "success",
                "outputs": {
                    "main": {
                        "token": secret,
                        "message": f"output contains {secret}",
                        "artifact": artifact_ref,
                    }
                },
                "logs": [f"log contains {secret}"],
                "debug": {
                    "guardrail_events": [
                        {
                            "type": "guardrail_redacted",
                            "adapter": "keyword_guardrail",
                            "leaked_message": f"guardrail contains {secret}",
                            "nested": {"secret": secret},
                        }
                    ]
                },
                "started_at": 1.0,
                "finished_at": 2.0,
            }
        )
        return RunResult(status=RunStatus.success)

    monkeypatch.setattr(runner_module, "execute", fake_execute)

    run_id = (await client.post(f"/workflows/{workflow_id}/run", json={})).json()["run_id"]
    run_body = (await client.get(f"/runs/{run_id}")).json()
    timeline_body = (await client.get(f"/runs/{run_id}/timeline")).json()
    artifact_body = (await client.get(f"/artifacts/{artifact_id}")).json()

    api_blob = json.dumps([run_body, timeline_body, artifact_body], sort_keys=True)
    assert secret not in api_blob
    assert "***REDACTED***" in api_blob

    async with runner_module.SessionLocal() as session:
        node_runs = (await session.scalars(select(NodeRun))).all()
        events = (await session.scalars(select(RunEvent))).all()
        artifact = await session.get(Artifact, artifact_id)

    persisted_blob = json.dumps(
        {
            "node_runs": [
                {
                    "output": row.output,
                    "logs": row.logs,
                    "debug": row.debug,
                    "error": row.error,
                }
                for row in node_runs
            ],
            "events": [
                {
                    "type": row.event_type,
                    "payload": row.payload,
                    "node_id": row.node_id,
                }
                for row in events
            ],
            "artifact": {
                "metadata": artifact.artifact_metadata if artifact else None,
                "preview": artifact.preview if artifact else None,
            },
        },
        sort_keys=True,
        default=str,
    )
    assert secret not in persisted_blob
    assert "***REDACTED***" in persisted_blob

    log_blob = "\n".join(record.getMessage() for record in caplog.records)
    assert secret not in log_blob
