"""dispatch_role topology (program A1): config validation + queued-only starts."""

from app.config import Settings


def test_inline_role_has_no_topology_errors():
    s = Settings(dispatch_role="inline", database_url="sqlite+aiosqlite:///x.db")
    assert s.dispatch_topology_errors() == []


def test_worker_role_requires_redis_and_postgres():
    s = Settings(
        dispatch_role="worker",
        queue_backend="none",
        database_url="sqlite+aiosqlite:///x.db",
    )
    errors = s.dispatch_topology_errors()
    assert any("queue_backend" in e for e in errors)
    assert any("postgres" in e.lower() for e in errors)


def test_disabled_role_valid_with_redis_and_postgres():
    s = Settings(
        dispatch_role="disabled",
        queue_backend="redis",
        database_url="postgresql+asyncpg://u:p@h:5432/db",
    )
    assert s.dispatch_topology_errors() == []
