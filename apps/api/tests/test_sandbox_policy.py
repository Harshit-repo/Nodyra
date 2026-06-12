"""Sandbox mode settings and the MT enforcement policy."""
import json

import pytest

from app.config import settings
from app.services.sandbox_policy import enforce_sandbox_policy


def test_defaults_are_off_and_strict():
    # Class-level defaults: the suite's _relax_sandbox_policy fixture mutates
    # the live instance, so assert what a fresh production boot would get.
    from app.config import Settings

    assert Settings.model_fields["execution_sandbox"].default == "off"
    assert Settings.model_fields["sandbox_runtime"].default == "auto"
    assert Settings.model_fields["sandbox_policy_strict"].default is True


def test_single_tenant_any_mode_passes(monkeypatch):
    monkeypatch.setattr(settings, "multi_tenancy_enabled", False)
    for mode in ("off", "auto", "required"):
        monkeypatch.setattr(settings, "execution_sandbox", mode)
        enforce_sandbox_policy()  # must not raise


@pytest.mark.parametrize("mode", ["off", "auto"])
def test_mt_requires_sandbox_required(monkeypatch, mode):
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    monkeypatch.setattr(settings, "execution_sandbox", mode)
    monkeypatch.setattr(settings, "sandbox_policy_strict", True)
    with pytest.raises(RuntimeError, match="execution_sandbox=required"):
        enforce_sandbox_policy()


def test_mt_with_required_passes(monkeypatch):
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    monkeypatch.setattr(settings, "execution_sandbox", "required")
    enforce_sandbox_policy()


def test_strictness_escape_hatch(monkeypatch):
    """Trusted-tenant deployments (and the MT test suite) can opt out."""
    monkeypatch.setattr(settings, "multi_tenancy_enabled", True)
    monkeypatch.setattr(settings, "execution_sandbox", "off")
    monkeypatch.setattr(settings, "sandbox_policy_strict", False)
    enforce_sandbox_policy()  # must not raise


def test_invalid_mode_rejected(monkeypatch):
    monkeypatch.setattr(settings, "execution_sandbox", "definitely-not-a-mode")
    with pytest.raises(RuntimeError, match="execution_sandbox"):
        enforce_sandbox_policy()


async def test_health_ready_reports_sandbox_state(monkeypatch):
    """/health/ready: silent when off, informational in auto, 503 when
    required but the pool never came up."""
    from app.routers import health

    class _Conn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, _query):
            return None

    class _Engine:
        def connect(self):
            return _Conn()

    class _Redis:
        async def ping(self):
            return True

    monkeypatch.setattr(health, "engine", _Engine())
    monkeypatch.setattr(health, "redis_client", _Redis())

    def _body(resp):
        return json.loads(resp.body)

    monkeypatch.setattr(settings, "execution_sandbox", "off")
    resp = await health.ready()
    assert resp.status_code == 200
    assert "sandbox" not in _body(resp)["checks"]

    class _InactivePool:
        enabled = False

    monkeypatch.setattr(health, "sandbox_pool", _InactivePool())
    monkeypatch.setattr(settings, "execution_sandbox", "auto")
    resp = await health.ready()
    assert resp.status_code == 200
    assert _body(resp)["checks"]["sandbox"] == "inactive (subprocess fallback)"

    monkeypatch.setattr(settings, "execution_sandbox", "required")
    resp = await health.ready()
    assert resp.status_code == 503
    assert _body(resp)["checks"]["sandbox"] == "error: required but inactive"

    class _ActivePool:
        enabled = True

        def describe(self):
            return "runtime=runsc idle=1 active=0"

    monkeypatch.setattr(health, "sandbox_pool", _ActivePool())
    resp = await health.ready()
    assert resp.status_code == 200
    assert _body(resp)["checks"]["sandbox"] == "runtime=runsc idle=1 active=0"
