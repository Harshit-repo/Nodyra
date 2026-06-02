"""Tests for explicit local vs production runtime-mode configuration."""

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
        artifact_s3_bucket="prod-noodle-artifacts",
        queue_backend="redis",
    )
    assert s.runtime_warnings() == []


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
        artifact_s3_bucket="prod-noodle-artifacts",
        queue_backend="redis",
        webhook_role="inline",
    )
    assert any("webhook" in w.lower() for w in s.runtime_warnings())
