"""Startup policy: multi-tenant deployments must not share the subprocess pool.

Called from the API lifespan and worker_main before any run can dispatch.
Kept separate from sandbox_pool so importing the policy never imports the
docker SDK.

This is a *fail-closed* startup guard, the same treatment AUTH-1 gets: an unsafe
combination aborts boot rather than degrading silently. "Make the safe path the
only path" — a deployment that relies on an isolation boundary cannot be
configured to bypass it.
"""

from app.config import settings

_VALID_MODES = ("off", "auto", "required")
VALID_EXECUTION_MODES = ("inherit", "sandboxed", "standard")
_RESOURCE_KEYS = ("memory_mb", "cpu", "tmpfs_mb")


def resolve_execution_mode(
    *, run_override: str | None, workflow_mode: str | None
) -> str:
    """Resolve the effective run isolation mode.

    Per-run overrides are escalate-only: only ``sandboxed`` is honoured, so a
    caller cannot use a run flag to downgrade a sandboxed workflow.
    """
    if settings.multi_tenancy_enabled and settings.sandbox_policy_strict:
        return "sandboxed"
    if settings.execution_sandbox == "required":
        return "sandboxed"
    mode = workflow_mode if workflow_mode in ("sandboxed", "standard") else "inherit"
    if mode == "inherit":
        mode = (
            settings.sandbox_workflow_default
            if settings.execution_sandbox != "off"
            else "standard"
        )
    if run_override == "sandboxed":
        return "sandboxed"
    return mode


def validate_sandbox_resources(payload: dict) -> dict:
    """Validate user-supplied per-workflow sandbox resource requests."""
    if not isinstance(payload, dict):
        raise ValueError("sandbox_resources must be an object")
    unknown = sorted(set(payload) - set(_RESOURCE_KEYS))
    if unknown:
        raise ValueError(f"sandbox_resources: unknown key(s) {unknown}")
    ceilings = {
        "memory_mb": settings.sandbox_max_memory_mb,
        "cpu": settings.sandbox_max_cpu,
        "tmpfs_mb": settings.sandbox_max_tmpfs_mb,
    }
    clean: dict = {}
    for key, value in payload.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"sandbox_resources: {key} must be a positive number")
        if value > ceilings[key]:
            raise ValueError(
                f"sandbox_resources: {key}={value} exceeds this deployment's "
                f"ceiling of {ceilings[key]}"
            )
        clean[key] = value
    return clean


def resolve_sandbox_overrides(requested: dict | None) -> dict:
    """Translate workflow sandbox resources into Docker spawn overrides.

    Unknown keys are ignored and values are clamped at spawn time so rows
    written under older ceilings still run under stricter deployments.
    """
    if not isinstance(requested, dict) or not requested:
        return {}
    overrides: dict = {}
    mem = requested.get("memory_mb")
    if isinstance(mem, (int, float)) and not isinstance(mem, bool) and mem > 0:
        overrides["mem_limit"] = f"{int(min(mem, settings.sandbox_max_memory_mb))}m"
    cpu = requested.get("cpu")
    if isinstance(cpu, (int, float)) and not isinstance(cpu, bool) and cpu > 0:
        overrides["nano_cpus"] = int(
            min(float(cpu), settings.sandbox_max_cpu) * 1_000_000_000
        )
    tmpfs = requested.get("tmpfs_mb")
    if isinstance(tmpfs, (int, float)) and not isinstance(tmpfs, bool) and tmpfs > 0:
        overrides["tmpfs"] = {
            "/tmp": f"size={int(min(tmpfs, settings.sandbox_max_tmpfs_mb))}m"
        }
    return overrides


def enforce_sandbox_policy() -> None:
    """Raise RuntimeError when the configuration is unsafe or invalid.

    Invariants enforced:

    * ``execution_sandbox`` must be a valid mode.
    * **SAFE-3 / NODE-1** — the in-process runner (``use_subprocess_runner=False``)
      execs uploaded module / Code-node Python *in the host process*, the same
      process that holds the master KEK and DB credentials. The runner's dispatch
      checks ``use_subprocess_runner`` **before** the sandbox branch
      (``runner.py``), so an in-process config silently bypasses
      ``execution_sandbox`` entirely. It is therefore rejected whenever an
      isolation boundary is expected (multi-tenancy, or a non-``off`` sandbox).
    * **MT** — ``multi_tenancy_enabled`` requires ``execution_sandbox=required``
      (tenant code must not share the worker's kernel namespace) **and** a
      subprocess/container runner, never in-process.
    * **SAFE-2** — under multi-tenancy the dedicated, egress-isolated
      ``sandbox_network`` must be configured; an empty value would drop run
      containers onto the default bridge where they can reach postgres/redis/minio.

    ``sandbox_policy_strict=False`` is the explicit, documented escape hatch that
    accepts shared-kernel execution for trusted-tenant deployments (and the test
    suite). It does **not** waive the config-lie check below, which catches a
    requested-but-bypassed sandbox regardless of tenancy.
    """
    if settings.execution_sandbox not in _VALID_MODES:
        raise RuntimeError(
            f"invalid execution_sandbox={settings.execution_sandbox!r}: "
            f"expected one of {_VALID_MODES}"
        )

    if not settings.multi_tenancy_enabled or not settings.sandbox_policy_strict:
        # Single-tenant, or strictness explicitly relaxed: in-process /
        # shared-kernel execution is the accepted trusted-author model. Still
        # fail closed on the *config lie* where a sandbox was requested but the
        # in-process path would silently bypass it — an operator who set
        # execution_sandbox!=off clearly expects isolation.
        if (
            settings.sandbox_policy_strict
            and settings.execution_sandbox != "off"
            and not settings.use_subprocess_runner
        ):
            raise RuntimeError(
                "use_subprocess_runner=false runs node/code execution in the host "
                f"process and bypasses execution_sandbox={settings.execution_sandbox!r} "
                "(the runner selects the in-process path before the sandbox). Set "
                "use_subprocess_runner=true, or execution_sandbox=off to acknowledge "
                "in-process execution."
            )
        return

    # Multi-tenant + strict regime.
    if settings.execution_sandbox != "required":
        raise RuntimeError(
            "multi_tenancy_enabled=true requires execution_sandbox=required "
            "(tenant code must not share the worker's kernel namespace). "
            "Set EXECUTION_SANDBOX=required, or SANDBOX_POLICY_STRICT=false "
            "to explicitly accept shared-kernel execution for trusted tenants."
        )
    if not settings.use_subprocess_runner:
        raise RuntimeError(
            "multi_tenancy_enabled=true with use_subprocess_runner=false runs "
            "tenant code in the host process (master KEK and DB credentials in "
            "memory), bypassing the container sandbox entirely. Set "
            "use_subprocess_runner=true (the default)."
        )
    if not settings.sandbox_network.strip():
        raise RuntimeError(
            "multi_tenancy_enabled=true requires a dedicated sandbox_network "
            "(SANDBOX_NETWORK) so run containers cannot reach postgres/redis/minio "
            "on the default bridge. Set SANDBOX_NETWORK to an egress-isolated "
            "network, or SANDBOX_POLICY_STRICT=false to accept the default bridge."
        )
