"""Per-workflow sandbox resources: translation, validation, pool-key isolation."""

import pytest

from app.config import settings
from app.services.sandbox_policy import (
    resolve_sandbox_overrides,
    validate_sandbox_resources,
)


def test_empty_request_means_no_overrides():
    assert resolve_sandbox_overrides(None) == {}
    assert resolve_sandbox_overrides({}) == {}


def test_translation_to_docker_kwargs():
    out = resolve_sandbox_overrides({"memory_mb": 2048, "cpu": 2.0, "tmpfs_mb": 512})
    assert out["mem_limit"] == "2048m"
    assert out["nano_cpus"] == 2_000_000_000
    assert out["tmpfs"] == {"/tmp": "size=512m"}


def test_spawn_time_clamp_to_deployment_ceiling(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_max_memory_mb", 1024)
    monkeypatch.setattr(settings, "sandbox_max_cpu", 1.0)
    out = resolve_sandbox_overrides({"memory_mb": 999999, "cpu": 64})
    assert out["mem_limit"] == "1024m"
    assert out["nano_cpus"] == 1_000_000_000


def test_security_keys_are_never_translated():
    out = resolve_sandbox_overrides(
        {"memory_mb": 512, "network": "host", "cap_drop": [], "read_only": False}
    )
    assert set(out) == {"mem_limit"}


def test_validate_rejects_bad_payloads(monkeypatch):
    monkeypatch.setattr(settings, "sandbox_max_memory_mb", 1024)
    with pytest.raises(ValueError, match="memory_mb"):
        validate_sandbox_resources({"memory_mb": 4096})
    with pytest.raises(ValueError, match="unknown"):
        validate_sandbox_resources({"network": "host"})
    with pytest.raises(ValueError, match="cpu"):
        validate_sandbox_resources({"cpu": -1})
    assert validate_sandbox_resources({"memory_mb": 512}) == {"memory_mb": 512}


def test_pool_key_includes_resource_fingerprint():
    from app.services.sandbox_pool import overrides_key

    a = overrides_key({"mem_limit": "512m"})
    b = overrides_key({"mem_limit": "2048m"})
    assert a != b
    assert overrides_key({}) == overrides_key(None)
