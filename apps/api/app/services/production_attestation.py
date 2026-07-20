"""Machine-readable production posture checks with bounded live probes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import DEFAULT_SECRET_KEY, settings
from app.redis_client import redis_client
from app.services import queue as run_queue
from app.services.artifact_backends import get_backend
from app.services.licensing import current_license

CheckStatus = Literal["pass", "warning", "fail"]
CheckSeverity = Literal["info", "warning", "critical"]


class AttestationCheck(TypedDict):
    id: str
    category: str
    title: str
    status: CheckStatus
    severity: CheckSeverity
    evidence: dict[str, Any]
    remediation: str | None
    last_verified_at: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _check(
    check_id: str,
    category: str,
    title: str,
    passed: bool,
    evidence: dict[str, Any],
    remediation: str,
    *,
    severity: CheckSeverity = "critical",
    warning: bool = False,
) -> AttestationCheck:
    status: CheckStatus = "pass" if passed else ("warning" if warning else "fail")
    return {
        "id": check_id,
        "category": category,
        "title": title,
        "status": status,
        "severity": "info" if passed else severity,
        "evidence": evidence,
        "remediation": None if passed else remediation,
        "last_verified_at": _now(),
    }


async def _live_checks(session: AsyncSession) -> list[AttestationCheck]:
    checks: list[AttestationCheck] = []

    try:
        await asyncio.wait_for(session.execute(select(1)), timeout=5)
    except Exception:  # noqa: BLE001 - probes report bounded failures, never secrets
        checks.append(
            _check(
                "database.live",
                "database",
                "Database responds to a bounded query",
                False,
                {"reachable": False},
                "Restore database connectivity before accepting traffic.",
            )
        )
    else:
        checks.append(
            _check(
                "database.live",
                "database",
                "Database responds to a bounded query",
                True,
                {"reachable": True},
                "",
            )
        )

    redis_required = settings.queue_backend == "redis"
    try:
        pong = bool(await asyncio.wait_for(redis_client.ping(), timeout=5))
    except Exception:  # noqa: BLE001
        pong = False
    checks.append(
        _check(
            "redis.live",
            "redis",
            "Redis responds when required",
            pong if redis_required else True,
            {"required": redis_required, "reachable": pong},
            "Restore Redis connectivity and verify REDIS_URL from every API and worker replica.",
            severity="critical" if redis_required else "warning",
            warning=not redis_required,
        )
    )

    try:
        queue = await asyncio.wait_for(run_queue.stats(session), timeout=5)
    except Exception:  # noqa: BLE001
        checks.append(
            _check(
                "queue.live",
                "queue",
                "Queue state is observable",
                False,
                {"queryable": False},
                "Restore queue/database access and inspect dispatcher logs.",
            )
        )
    else:
        checks.append(
            _check(
                "queue.live",
                "queue",
                "Queue state is observable",
                True,
                {
                    "queryable": True,
                    "queued": int(queue.get("queued", 0)),
                    "running": int(queue.get("running", 0)),
                    "dead_lettered": int(queue.get("dead_lettered", 0)),
                    "oldest_queued_age_seconds": queue.get("oldest_queued_age_seconds"),
                },
                "",
            )
        )

    try:
        backend_stats = await asyncio.wait_for(asyncio.to_thread(get_backend().stats), timeout=5)
    except Exception:  # noqa: BLE001
        checks.append(
            _check(
                "artifacts.live",
                "artifacts",
                "Artifact backend can be inspected",
                False,
                {"inspectable": False, "backend": settings.artifact_storage_backend},
                "Verify artifact backend credentials, bucket/container, endpoint, and network policy.",
            )
        )
    else:
        checks.append(
            _check(
                "artifacts.live",
                "artifacts",
                "Artifact backend can be inspected",
                True,
                {"inspectable": True, "backend": backend_stats.get("backend")},
                "",
            )
        )

    return checks


async def build_production_attestation(session: AsyncSession) -> dict[str, Any]:
    """Evaluate configuration and live dependencies without exposing secrets."""
    try:
        database_dialect = make_url(settings.database_url).get_backend_name()
    except Exception:  # noqa: BLE001
        database_dialect = "unknown"
    public_https = settings.public_api_url.lower().startswith("https://")
    secret_ready = settings.secret_key != DEFAULT_SECRET_KEY and bool(settings.internal_api_token)
    remote_artifacts = settings.artifact_storage_backend != "local"
    sandbox_ready = settings.execution_sandbox == "required" and settings.sandbox_policy_strict
    license_state = await current_license()

    checks: list[AttestationCheck] = [
        _check(
            "runtime.mode",
            "runtime",
            "Production runtime mode is explicit",
            settings.runtime_mode == "production",
            {"mode": settings.runtime_mode, "insecure_override": settings.runtime_allow_insecure},
            "Set RUNTIME_MODE=production and remove RUNTIME_ALLOW_INSECURE after resolving failures.",
        ),
        _check(
            "auth.required",
            "auth",
            "Authentication is mandatory",
            settings.auth_required,
            {"auth_required": settings.auth_required},
            "Set AUTH_REQUIRED=true and configure an identity path before exposure.",
        ),
        _check(
            "cors.explicit",
            "cors",
            "Browser origins are explicit",
            "*" not in settings.cors_origin_list and bool(settings.cors_origin_list),
            {"wildcard": "*" in settings.cors_origin_list, "origin_count": len(settings.cors_origin_list)},
            "Replace wildcard/empty CORS with the exact HTTPS frontend origins.",
        ),
        _check(
            "secrets.configured",
            "secrets",
            "Signing and internal API secrets are configured",
            secret_ready,
            {
                "default_signing_key": settings.secret_key == DEFAULT_SECRET_KEY,
                "internal_api_token_configured": bool(settings.internal_api_token),
            },
            "Generate independent high-entropy SECRET_KEY and INTERNAL_API_TOKEN values in a secret manager.",
        ),
        _check(
            "database.production",
            "database",
            "Database supports concurrent durable operation",
            database_dialect == "postgresql",
            {"dialect": database_dialect},
            "Use a supported PostgreSQL deployment and run migrations before startup.",
        ),
        _check(
            "redis.production",
            "redis",
            "Shared Redis coordination is enabled",
            settings.queue_backend == "redis",
            {"queue_backend": settings.queue_backend},
            "Set QUEUE_BACKEND=redis for durable queueing and multi-replica coordination.",
        ),
        _check(
            "artifacts.production",
            "artifacts",
            "Artifacts use shared durable storage",
            remote_artifacts,
            {"backend": settings.artifact_storage_backend},
            "Configure an S3-compatible shared artifact backend with versioning and lifecycle policy.",
        ),
        _check(
            "edge.tls_proxy",
            "tls",
            "Public URL and trusted proxy contract are explicit",
            public_https and settings.trusted_proxy_count > 0 and settings.session_cookie_secure,
            {
                "public_https": public_https,
                "trusted_proxy_count": settings.trusted_proxy_count,
                "secure_cookie": settings.session_cookie_secure,
            },
            "Set PUBLIC_API_URL to HTTPS, configure TRUSTED_PROXY_COUNT exactly, and keep secure cookies enabled.",
        ),
        _check(
            "sandbox.required",
            "sandbox",
            "Untrusted workflow execution is sandboxed by default",
            sandbox_ready,
            {
                "mode": settings.execution_sandbox,
                "strict_policy": settings.sandbox_policy_strict,
                "workflow_default": settings.sandbox_workflow_default,
            },
            "Set EXECUTION_SANDBOX=required and SANDBOX_POLICY_STRICT=true; verify runtime admission and cleanup.",
        ),
        _check(
            "backups.attested",
            "backups",
            "Backup freshness is externally attested",
            False,
            {"status": "not_attested", "reason": "backup systems are external to the API"},
            "Attach the latest encrypted backup, restore-drill timestamp, RPO, and RTO evidence to the release bundle.",
            severity="warning",
            warning=True,
        ),
        _check(
            "kms.external",
            "kms",
            "Credentials use an external key manager",
            settings.kms_provider != "env",
            {"provider": settings.kms_provider},
            "Configure Vault Transit, AWS KMS, or GCP KMS and exercise decryptability during restore drills.",
            severity="warning",
            warning=True,
        ),
        _check(
            "tracing.enabled",
            "tracing",
            "Distributed tracing is enabled",
            settings.otel_enabled,
            {"otel_enabled": settings.otel_enabled},
            "Enable OTEL and export traces to a retained collector with run/trace correlation.",
            severity="warning",
            warning=True,
        ),
        _check(
            "replicas.safe",
            "replicas",
            "Configured topology is replica-safe",
            settings.replica_safe(),
            {"safe": settings.replica_safe(), "reason_count": len(settings.replica_unsafe_reasons())},
            "Resolve every replica-unsafe reason before scaling API or workers horizontally.",
        ),
        _check(
            "webhooks.hardened",
            "webhooks",
            "Webhook ingress is isolated and authenticated",
            settings.webhook_role == "ingress" and settings.webhook_require_auth,
            {"role": settings.webhook_role, "auth_required": settings.webhook_require_auth},
            "Use WEBHOOK_ROLE=ingress, require trigger authentication, and retain rate limits.",
        ),
        _check(
            "license.valid",
            "license",
            "License posture is explicit and valid",
            license_state.valid,
            {
                "edition": license_state.edition.value,
                "valid": license_state.valid,
                "expires_at": license_state.expires_at,
            },
            "Replace the invalid/expired license or operate within Community capabilities.",
        ),
    ]
    checks.extend(await _live_checks(session))

    failures = [check for check in checks if check["status"] == "fail"]
    warnings = [check for check in checks if check["status"] == "warning"]
    return {
        "schema_version": 1,
        "generated_at": _now(),
        "production_ready": not failures and settings.runtime_mode == "production",
        "insecure_override": settings.runtime_allow_insecure,
        "summary": {
            "passed": sum(check["status"] == "pass" for check in checks),
            "warnings": len(warnings),
            "failed": len(failures),
        },
        "checks": checks,
    }
