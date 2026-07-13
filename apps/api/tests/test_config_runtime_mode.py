"""Tests for explicit local vs production runtime-mode configuration."""

import pytest
from pydantic import ValidationError

from app.config import Settings

_PG = "postgresql+asyncpg://u:p@h/db"
_SQLITE = "sqlite+aiosqlite:///./dev.db"


def _settings(**overrides) -> Settings:
    # _env_file=None keeps tests independent of the repo's .env file.
    return Settings(_env_file=None, **overrides)


def test_default_runtime_mode_is_local_with_no_warnings():
    s = _settings()
    assert s.runtime_mode == "local"
    assert s.is_production is False
    assert s.runtime_warnings() == []


def test_runtime_heartbeat_timeout_must_exceed_emit_interval():
    with pytest.raises(ValidationError, match="must be greater"):
        _settings(
            runtime_heartbeat_interval_seconds=45,
            runtime_heartbeat_timeout_seconds=45,
        )


def test_production_with_sqlite_warns():
    s = _settings(
        runtime_mode="production",
        database_url=_SQLITE,
        artifact_storage_backend="s3",
        queue_backend="redis",
    )
    assert s.is_production is True
    assert any("sqlite" in w.lower() for w in s.runtime_warnings())


def test_production_with_local_artifacts_warns():
    s = _settings(
        runtime_mode="production",
        database_url=_PG,
        artifact_storage_backend="local",
        queue_backend="redis",
    )
    assert any("artifact" in w.lower() for w in s.runtime_warnings())


def test_production_with_no_queue_backend_warns():
    s = _settings(
        runtime_mode="production",
        database_url=_PG,
        artifact_storage_backend="s3",
        artifact_s3_bucket="x",
        queue_backend="none",
    )
    assert any("queue" in w.lower() for w in s.runtime_warnings())


def test_s3_backend_without_bucket_warns():
    s = _settings(
        runtime_mode="production",
        database_url=_PG,
        artifact_storage_backend="s3",
        artifact_s3_bucket="",
        queue_backend="redis",
    )
    assert any("artifact_s3_bucket" in w for w in s.runtime_warnings())


def test_production_fully_configured_has_no_warnings():
    s = _settings(
        runtime_mode="production",
        database_url=_PG,
        artifact_storage_backend="s3",
        artifact_s3_bucket="prod-nodyra-artifacts",
        queue_backend="redis",
        secret_key="a-strong-random-production-secret-key-xyz",
        cors_origins="https://app.example.com",
        auth_required=True,
        internal_api_token="a-strong-internal-token-xyz",
    )
    assert s.runtime_warnings() == []


def test_replica_safe_when_redis_is_configured():
    s = _settings(
        runtime_mode="production",
        database_url=_PG,
        artifact_storage_backend="s3",
        artifact_s3_bucket="prod-nodyra-artifacts",
        queue_backend="redis",
        api_replica_count=3,
        auth_required=True,
    )
    assert s.replica_safe() is True
    assert s.replica_unsafe_reasons() == []


def test_no_redis_flags_replica_unsafe_scale_out_subsystems():
    s = _settings(
        runtime_mode="production",
        database_url=_PG,
        artifact_storage_backend="s3",
        artifact_s3_bucket="prod-nodyra-artifacts",
        queue_backend="none",
        api_replica_count=2,
        auth_required=True,
        mcp_authorization_server_url="https://issuer.example.com",
        mcp_oauth_introspection_url="https://issuer.example.com/introspect",
    )
    reasons = s.replica_unsafe_reasons()
    assert s.replica_safe() is False
    assert any("run event broker" in reason for reason in reasons)
    assert any("rate limits" in reason for reason in reasons)
    assert any("secret redaction cache" in reason for reason in reasons)
    assert any("MCP OAuth introspection cache" in reason for reason in reasons)
    assert any("api_replica_count=2" in reason for reason in reasons)
    assert any("replica_safe=False" in warning for warning in s.runtime_warnings())


def test_local_mode_never_warns_even_with_sqlite_and_local_artifacts():
    s = _settings(
        runtime_mode="local",
        database_url=_SQLITE,
        artifact_storage_backend="local",
        queue_backend="none",
    )
    assert s.runtime_warnings() == []


def test_allow_insecure_override_is_exposed():
    s = _settings(
        runtime_mode="production",
        database_url=_SQLITE,
        runtime_allow_insecure=True,
    )
    # Warnings stay visible for observability...
    assert s.runtime_warnings()
    # ...but the override flag lets an operator deliberately opt in.
    assert s.runtime_allow_insecure is True


def test_scheduler_defaults_inline_webhook_defaults_ingress():
    s = _settings()
    assert s.scheduler_role == "inline"
    assert s.webhook_role == "ingress"


def test_production_with_inline_webhook_role_warns():
    """Production + webhook_role=inline is advisory: webhook bursts share the
    API process; prefer the dedicated ingress role."""
    s = _settings(
        runtime_mode="production",
        database_url=_PG,
        artifact_storage_backend="s3",
        artifact_s3_bucket="prod-nodyra-artifacts",
        queue_backend="redis",
        webhook_role="inline",
    )
    assert any("webhook" in w.lower() for w in s.runtime_warnings())


# --- AUTH-1 / AUTH-3: fail-closed security startup guard ---------------------
from app.config import DEFAULT_SECRET_KEY  # noqa: E402


def test_default_secret_with_auth_required_aborts():
    s = _settings(auth_required=True)  # secret_key left at the built-in default
    errs = s.security_startup_errors()
    assert any("SECRET_KEY" in e for e in errs)


def test_default_secret_with_multi_tenancy_aborts():
    s = _settings(multi_tenancy_enabled=True)
    assert any("SECRET_KEY" in e for e in s.security_startup_errors())


def test_default_secret_without_boundary_is_allowed():
    # Auth-disabled single-user dev must keep booting on the default secret.
    s = _settings(auth_required=False, multi_tenancy_enabled=False)
    assert s.security_startup_errors() == []


def test_strong_secret_with_auth_required_is_allowed():
    s = _settings(auth_required=True, secret_key="a-strong-random-secret-value")
    assert s.security_startup_errors() == []


def test_allow_insecure_bypasses_security_guard():
    s = _settings(
        auth_required=True,
        secret_key=DEFAULT_SECRET_KEY,
        runtime_allow_insecure=True,
    )
    assert s.security_startup_errors() == []


def test_blank_internal_token_in_split_topology_aborts():
    s = _settings(
        dispatch_role="control",
        queue_backend="redis",
        database_url=_PG,
        secret_key="a-strong-random-secret-value",
        internal_api_token="",
    )
    assert any("INTERNAL_API_TOKEN" in e for e in s.security_startup_errors())


def test_inline_topology_tolerates_blank_internal_token():
    s = _settings(secret_key="a-strong-random-secret-value", internal_api_token="")
    assert s.security_startup_errors() == []


# --- M1: wildcard CORS + credentials is a fail-closed startup error ----------

def test_wildcard_cors_with_auth_aborts():
    s = _settings(
        auth_required=True,
        secret_key="a-strong-random-secret-value",
        cors_origins="*",
    )
    assert any("CORS" in e for e in s.security_startup_errors())


def test_wildcard_cors_with_multi_tenancy_aborts():
    s = _settings(
        multi_tenancy_enabled=True,
        secret_key="a-strong-random-secret-value",
        cors_origins="https://app.example.com, *",
    )
    assert any("CORS" in e for e in s.security_startup_errors())


def test_explicit_cors_origins_are_allowed():
    s = _settings(
        auth_required=True,
        secret_key="a-strong-random-secret-value",
        cors_origins="https://app.example.com",
    )
    assert s.security_startup_errors() == []


def test_wildcard_cors_without_boundary_is_allowed():
    # Auth-disabled single-user dev keeps booting with the permissive default.
    s = _settings(auth_required=False, multi_tenancy_enabled=False, cors_origins="*")
    assert s.security_startup_errors() == []


def test_allow_insecure_bypasses_cors_guard():
    s = _settings(
        auth_required=True,
        secret_key="a-strong-random-secret-value",
        cors_origins="*",
        runtime_allow_insecure=True,
    )
    assert s.security_startup_errors() == []


# --- M7: multi-tenancy hardens the unsafe-node policy default ----------------

def test_unsafe_node_policy_defaults_to_require_approval_under_multi_tenancy():
    s = _settings(multi_tenancy_enabled=True)
    assert s.unsafe_node_policy == "require_approval"


def test_explicit_unsafe_node_policy_is_respected_under_multi_tenancy():
    s = _settings(multi_tenancy_enabled=True, unsafe_node_policy="warn")
    assert s.unsafe_node_policy == "warn"


def test_unsafe_node_policy_default_unchanged_without_multi_tenancy():
    s = _settings()
    assert s.unsafe_node_policy == "warn"


# --- PY-1: multi-tenancy hardens sandbox defaults ---------------------------


def test_multi_tenant_defaults_require_sandbox_and_warm_pool():
    s = _settings(multi_tenancy_enabled=True)
    assert s.execution_sandbox == "required"
    assert s.sandbox_warm_per_key > 0
    assert s.sandbox_warm_total > 0
    assert s.sandbox_warm_ttl_seconds > 0
    assert s.sandbox_max_runs_per_container > 0


def test_explicit_multi_tenant_sandbox_kwargs_are_respected():
    s = _settings(
        multi_tenancy_enabled=True,
        execution_sandbox="off",
        sandbox_warm_per_key=0,
        sandbox_warm_total=0,
        sandbox_warm_ttl_seconds=0,
        sandbox_max_runs_per_container=0,
    )
    assert s.execution_sandbox == "off"
    assert s.sandbox_warm_per_key == 0
    assert s.sandbox_warm_total == 0
    assert s.sandbox_warm_ttl_seconds == 0
    assert s.sandbox_max_runs_per_container == 0


def test_explicit_multi_tenant_sandbox_env_is_respected(monkeypatch):
    monkeypatch.setenv("MULTI_TENANCY_ENABLED", "true")
    monkeypatch.setenv("EXECUTION_SANDBOX", "off")
    s = Settings(_env_file=None)
    assert s.multi_tenancy_enabled is True
    assert s.execution_sandbox == "off"
