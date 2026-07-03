"""X1: runtime workers must not inherit API secrets via their environment.

``_RuntimeProcess.spawn`` used to do ``env = dict(os.environ)``, handing every
Code node the master KEK (SECRET_KEY), DATABASE_URL, and OAuth client secrets.
``_worker_env`` builds the subprocess environment from an explicit allowlist
instead.
"""

import pytest

import app.services.runtime_pool as _rp
from app.services.runtime_pool import _worker_env


@pytest.fixture(autouse=True)
def _reset_cache():
    _rp._WORKER_ENV_CACHE = None
    _rp._WORKER_ENV_CACHE_AT = 0.0
    yield
    _rp._WORKER_ENV_CACHE = None
    _rp._WORKER_ENV_CACHE_AT = 0.0


def test_secrets_never_reach_worker_env(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "super-secret")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "oauth-secret")
    monkeypatch.setenv("INTERNAL_API_TOKEN", "tok")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    env = _worker_env()
    for forbidden in (
        "SECRET_KEY",
        "DATABASE_URL",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "INTERNAL_API_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
    ):
        assert forbidden not in env


def test_required_os_vars_pass_through(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    env = _worker_env()
    assert env["PATH"] == "/usr/bin"
    # Always set explicitly so the runtime applies the same per-node default
    # as the in-process engine.
    assert "NODYRA_CODE_NODE_TIMEOUT_SECONDS" in env


def test_nodyra_prefixed_vars_pass_through(monkeypatch):
    monkeypatch.setenv("NODYRA_CUSTOM_FLAG", "1")
    env = _worker_env()
    assert env["NODYRA_CUSTOM_FLAG"] == "1"


def test_egress_default_blocks_private_in_multi_tenant(monkeypatch):
    """SEC-3: hosted multi-tenant blocks private egress by default so a tenant
    cannot reach internal services or cloud metadata."""
    monkeypatch.delenv("NODYRA_ALLOW_PRIVATE_EGRESS", raising=False)
    monkeypatch.setattr(_rp.settings, "multi_tenancy_enabled", True)
    env = _worker_env()
    assert env["NODYRA_ALLOW_PRIVATE_EGRESS"] == "0"


def test_egress_default_allows_private_in_single_tenant(monkeypatch):
    """SEC-3: single-tenant self-hosted trusts its own network, so internal
    targets (Ollama on localhost, a VPC database, self-hosted GitLab) work
    out of the box."""
    monkeypatch.delenv("NODYRA_ALLOW_PRIVATE_EGRESS", raising=False)
    monkeypatch.setattr(_rp.settings, "multi_tenancy_enabled", False)
    env = _worker_env()
    assert env["NODYRA_ALLOW_PRIVATE_EGRESS"] == "1"


def test_explicit_egress_env_overrides_deployment_default(monkeypatch):
    """SEC-3: an operator can pin the policy regardless of deployment model."""
    monkeypatch.setattr(_rp.settings, "multi_tenancy_enabled", True)
    monkeypatch.setenv("NODYRA_ALLOW_PRIVATE_EGRESS", "1")
    env = _worker_env()
    assert env["NODYRA_ALLOW_PRIVATE_EGRESS"] == "1"


def test_allowlist_is_case_insensitive_for_windows_names(monkeypatch):
    # Windows env var names are case-insensitive; os.environ normalises to
    # upper-case there, but guard against mixed-case entries on POSIX too.
    monkeypatch.setenv("SystemRoot", "C:\\Windows")
    env = _worker_env()
    assert "C:\\Windows" in env.values()
