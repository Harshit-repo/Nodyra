"""Redacted operational evidence collection for support and security review."""

from __future__ import annotations

import importlib.metadata
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Run
from app.services import queue as run_queue
from app.services.artifact_backends import get_backend
from app.services.licensing import current_license
from app.services.production_attestation import build_production_attestation
from app.services.redaction import redact_text

_DEPENDENCIES = ("fastapi", "sqlalchemy", "pydantic", "redis", "nodyra-core")


async def _migration_revision(session: AsyncSession) -> str:
    try:
        value = await session.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
    except Exception:  # noqa: BLE001 - evidence must survive partial outages
        return "unavailable"
    return str(value or "unavailable")


def _dependency_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in _DEPENDENCIES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not-installed"
    return versions


async def collect_operational_evidence(session: AsyncSession) -> dict[str, Any]:
    """Return bounded evidence only; never include URLs, tokens, or raw configuration."""
    license_state = await current_license()
    try:
        queue = await run_queue.stats(session)
    except Exception:  # noqa: BLE001
        queue = {"status": "unavailable"}
    try:
        artifact_storage = get_backend().stats()
    except Exception:  # noqa: BLE001
        artifact_storage = {"backend": settings.artifact_storage_backend, "status": "unavailable"}

    recent_failures: list[dict[str, Any]] = []
    try:
        rows = (
            await session.scalars(
                select(Run)
                .where(Run.status.in_(("error", "timed_out")))
                .order_by(desc(Run.finished_at))
                .limit(20)
            )
        ).all()
        for row in rows:
            recent_failures.append(
                {
                    "run_id": row.id,
                    "workflow_id": row.workflow_id,
                    "status": row.status,
                    "finished_at": row.finished_at.isoformat() if row.finished_at else None,
                    "error": redact_text(str(row.error or ""))[:500] or None,
                }
            )
    except Exception:  # noqa: BLE001
        recent_failures = [{"status": "unavailable"}]

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "redaction": {
            "secrets_included": False,
            "connection_strings_included": False,
            "raw_configuration_included": False,
        },
        "configuration_posture": {
            "runtime_mode": settings.runtime_mode,
            "database_dialect": settings.database_url.split(":", 1)[0],
            "queue_backend": settings.queue_backend,
            "artifact_backend": settings.artifact_storage_backend,
            "sandbox_mode": settings.execution_sandbox,
            "kms_provider": settings.kms_provider,
            "otel_enabled": settings.otel_enabled,
            "auth_required": settings.auth_required,
            "multi_tenancy_enabled": settings.multi_tenancy_enabled,
        },
        "migration_revision": await _migration_revision(session),
        "dependencies": _dependency_versions(),
        "attestation": await build_production_attestation(session),
        "queue": queue,
        "artifact_storage": artifact_storage,
        "license": {
            "edition": license_state.edition.value,
            "valid": license_state.valid,
            "expires_at": license_state.expires_at,
            "notice": license_state.notice,
        },
        "recent_failures": recent_failures,
    }
