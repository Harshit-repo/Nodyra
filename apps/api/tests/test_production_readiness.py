import json

import pytest
from httpx import AsyncClient

from app.config import settings
from app.services import licensing
from app.services import operational_evidence as evidence_service
from app.services import production_attestation as attestation_service


class _HealthyRedis:
    async def ping(self) -> bool:
        return True


class _HealthyArtifactBackend:
    def stats(self) -> dict:
        return {"backend": "test", "objects": 2, "bytes": 128}


async def _queue_stats(_session) -> dict:  # noqa: ANN001
    return {
        "queued": 1,
        "running": 2,
        "dead_lettered": 0,
        "oldest_queued_age_seconds": 3,
    }


async def test_production_attestation_has_bounded_live_evidence_and_remediation(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(attestation_service, "redis_client", _HealthyRedis())
    monkeypatch.setattr(attestation_service.run_queue, "stats", _queue_stats)
    monkeypatch.setattr(
        attestation_service,
        "get_backend",
        lambda: _HealthyArtifactBackend(),
    )

    async with licensing.SessionLocal() as session:
        result = await attestation_service.build_production_attestation(session)

    assert result["schema_version"] == 1
    assert result["summary"]["passed"] + result["summary"]["warnings"] + result[
        "summary"
    ]["failed"] == len(result["checks"])
    assert {"database.live", "redis.live", "queue.live", "artifacts.live"} <= {
        check["id"] for check in result["checks"]
    }
    assert all(
        check["remediation"] is None or check["remediation"].strip()
        for check in result["checks"]
    )


async def test_operational_evidence_never_serializes_runtime_secrets(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_marker = "do-not-emit-this-secret"
    token_marker = "do-not-emit-this-token"
    monkeypatch.setattr(settings, "secret_key", secret_marker)
    monkeypatch.setattr(settings, "internal_api_token", token_marker)
    monkeypatch.setattr(evidence_service.run_queue, "stats", _queue_stats)
    monkeypatch.setattr(
        evidence_service,
        "get_backend",
        lambda: _HealthyArtifactBackend(),
    )

    async def _attestation(_session) -> dict:  # noqa: ANN001
        return {"schema_version": 1, "production_ready": False, "checks": []}

    monkeypatch.setattr(
        evidence_service,
        "build_production_attestation",
        _attestation,
    )
    async with licensing.SessionLocal() as session:
        result = await evidence_service.collect_operational_evidence(session)

    serialized = json.dumps(result)
    assert secret_marker not in serialized
    assert token_marker not in serialized
    assert result["redaction"] == {
        "secrets_included": False,
        "connection_strings_included": False,
        "raw_configuration_included": False,
    }


async def test_execution_protocol_endpoint_distinguishes_waiting_from_terminal(
    client: AsyncClient,
) -> None:
    response = await client.get("/ops/execution-protocol")

    assert response.status_code == 200
    body = response.json()
    assert "waiting" in body["runner_completion_statuses"]
    assert "waiting" not in body["terminal_run_statuses"]
    assert "waiting" not in body["run_lifecycle"]["terminal_states"]
