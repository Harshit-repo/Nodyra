"""Startup policy: multi-tenant deployments must not share the subprocess pool.

Called from the API lifespan and worker_main before any run can dispatch.
Kept separate from sandbox_pool so importing the policy never imports the
docker SDK.
"""

from app.config import settings

_VALID_MODES = ("off", "auto", "required")


def enforce_sandbox_policy() -> None:
    """Raise RuntimeError when the configuration is unsafe or invalid."""
    if settings.execution_sandbox not in _VALID_MODES:
        raise RuntimeError(
            f"invalid execution_sandbox={settings.execution_sandbox!r}: "
            f"expected one of {_VALID_MODES}"
        )
    if not settings.multi_tenancy_enabled or not settings.sandbox_policy_strict:
        return
    if settings.execution_sandbox != "required":
        raise RuntimeError(
            "multi_tenancy_enabled=true requires execution_sandbox=required "
            "(tenant code must not share the worker's kernel namespace). "
            "Set EXECUTION_SANDBOX=required, or SANDBOX_POLICY_STRICT=false "
            "to explicitly accept shared-kernel execution for trusted tenants."
        )
