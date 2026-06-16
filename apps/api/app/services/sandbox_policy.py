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
