"""EXECUTION_SANDBOX=auto must fall back, as it is documented to.

``config.py`` defines the three modes, and "auto" promises a fallback:

    "off":      runs use the warm subprocess pool
    "auto":     use disposable hardened containers when a Docker daemon is
                reachable, else fall back to subprocess with a startup warning
    "required": refuse to start without a usable daemon + runtime

``deploy/docker-compose.yml`` ships ``EXECUTION_SANDBOX=auto`` and mounts no
Docker socket into the worker — sandboxing is opt-in via the separate
``docker-compose.sandbox.yml`` overlay. So the documented default command,

    docker compose -f deploy/docker-compose.yml up --build -d

produced a stack where *every* run failed:

    SandboxRequired: run requires sandboxed execution but this worker has no
    active sandbox (EXECUTION_SANDBOX=off or Docker unreachable)

``resolve_execution_mode`` returned "sandboxed" whenever ``execution_sandbox``
was anything but "off", never consulting whether a sandbox existed, so the
fallback the docstring promises could not happen.

The safety property must survive the fix: only "auto" degrades. "required" and
strict multi-tenancy still fail closed — a deployment that relies on an
isolation boundary must never be able to quietly lose it.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.services.sandbox_policy import sandbox_fallback_allowed


@pytest.fixture
def sandbox_settings(monkeypatch):
    def _apply(**kw):
        for key, value in kw.items():
            monkeypatch.setattr(settings, key, value, raising=False)

    _apply(
        execution_sandbox="auto",
        multi_tenancy_enabled=False,
        sandbox_policy_strict=True,
    )
    return _apply


def test_auto_degrades_when_no_sandbox_is_available(sandbox_settings):
    """The bug: this is what makes the shipped compose stack usable."""
    sandbox_settings(execution_sandbox="auto")
    assert sandbox_fallback_allowed() is True


def test_required_never_degrades(sandbox_settings):
    """An operator who asked for isolation must not silently lose it."""
    sandbox_settings(execution_sandbox="required")
    assert sandbox_fallback_allowed() is False


def test_off_does_not_degrade_because_it_never_escalated(sandbox_settings):
    """With sandboxing off, a run reaching the sandbox branch did so because a
    workflow or run explicitly asked for it, and that request stands."""
    sandbox_settings(execution_sandbox="off")
    assert sandbox_fallback_allowed() is False


def test_strict_multi_tenancy_never_degrades_even_on_auto(sandbox_settings):
    """The one that matters most. Multi-tenant isolation is the whole reason
    the fail-closed guard exists; 'auto' must not become a way around it."""
    sandbox_settings(
        execution_sandbox="auto",
        multi_tenancy_enabled=True,
        sandbox_policy_strict=True,
    )
    assert sandbox_fallback_allowed() is False


def test_multi_tenancy_without_strict_policy_still_follows_auto(sandbox_settings):
    """Non-strict multi-tenancy is an explicit operator choice, so 'auto' keeps
    its documented meaning there."""
    sandbox_settings(
        execution_sandbox="auto",
        multi_tenancy_enabled=True,
        sandbox_policy_strict=False,
    )
    assert sandbox_fallback_allowed() is True


def test_the_shipped_compose_default_is_the_case_this_fixes():
    """Pin the coupling: if deploy/docker-compose.yml stops defaulting to auto,
    the reasoning in this file needs revisiting."""
    from pathlib import Path

    compose = Path(__file__).resolve().parents[3] / "deploy" / "docker-compose.yml"
    if not compose.exists():
        pytest.skip("deploy/ is not present in this checkout")
    text = compose.read_text(encoding="utf-8")
    assert "EXECUTION_SANDBOX:-auto" in text, (
        "the shipped compose no longer defaults EXECUTION_SANDBOX to auto; "
        "re-check whether this fallback is still the behaviour you want"
    )
